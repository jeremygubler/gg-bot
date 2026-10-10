"""Fragt Twitch und YouTube ab und gibt Ankündigungen an einen Sender weiter. Ohne Discord."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Protocol

from ggbot.core.logging import get_logger
from ggbot.core.notify.rules import new_videos, plan_stream
from ggbot.core.notify.twitch import TwitchClient
from ggbot.core.notify.types import ProviderError, StreamInfo, VideoInfo
from ggbot.core.notify.youtube import YouTubeClient
from ggbot.core.services.notify import NotifyService, SubView

log = get_logger(__name__)

YOUTUBE_CONCURRENCY = 5


@dataclass(frozen=True)
class SendResult:
    """Wie das Senden ausging.

    sent: angekommen (mit Nachrichten-ID). retry: vorübergehend gescheitert, beim nächsten Mal
    nochmal. drop: dauerhaft unmöglich (Kanal weg, keine Rechte): nicht jede Minute wiederholen.
    """

    kind: Literal["sent", "retry", "drop"]
    message_id: int | None = None


class Announcer(Protocol):
    def available(self, guild_id: int) -> bool:
        """Gehört der Server zu diesem Prozess? (Bei mehreren Prozessen/Shards nur dort senden.)"""

    async def announce_stream(self, sub: SubView, stream: StreamInfo) -> SendResult: ...

    async def announce_video(self, sub: SubView, video: VideoInfo) -> SendResult: ...

    async def end_stream(self, sub: SubView) -> None: ...


class Poller:
    def __init__(
        self,
        service: NotifyService,
        announcer: Announcer,
        twitch: TwitchClient | None,
        youtube: YouTubeClient,
        *,
        youtube_gate: Callable[[str], Awaitable[bool]],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._service = service
        self._announcer = announcer
        self._twitch = twitch
        self._youtube = youtube
        self._gate = youtube_gate
        self._clock = clock

    async def poll_twitch(self) -> int:
        """Eine Runde. Gibt die Zahl der Ankündigungen zurück."""
        if self._twitch is None or not self._twitch.configured:
            return 0
        subs = [
            s
            for s in await self._service.all_for_provider("twitch")
            if self._announcer.available(s.guild_id)
        ]
        if not subs:
            return 0
        try:
            live = await self._twitch.live(sorted({s.ref for s in subs}))
        except ProviderError as exc:
            # Bei einem Fehler nichts ändern: "nicht erreichbar" ist nicht "offline".
            log.warning("twitch_poll_failed", reason=exc.key)
            return 0
        sent = 0
        for sub in subs:
            try:
                sent += await self._twitch_sub(sub, live.get(sub.ref))
            except Exception:
                log.exception("twitch_sub_failed", sub_id=sub.id, guild_id=sub.guild_id)
        return sent

    async def _twitch_sub(self, sub: SubView, stream: StreamInfo | None) -> int:
        plan = plan_stream(sub.stream_state, stream)
        if plan.announce is not None:
            if not await self._service.claim_stream(sub, plan.announce.id):
                return 0  # jemand anderes war schneller
            result = await self._announcer.announce_stream(sub, plan.announce)
            if result.kind == "retry":
                await self._service.revert_stream(sub, plan.announce.id)
                return 0
            if result.kind == "sent":
                await self._service.set_message(sub.id, result.message_id)
                return 1
            return 0
        if plan.ended:
            await self._service.save_stream_state(sub, plan.state)
            await self._announcer.end_stream(sub)
        elif plan.state != sub.stream_state:
            await self._service.save_stream_state(sub, plan.state)
        return 0

    async def poll_youtube(self) -> int:
        subs = [
            s
            for s in await self._service.all_for_provider("youtube")
            if self._announcer.available(s.guild_id)
        ]
        by_ref: dict[str, list[SubView]] = {}
        for sub in subs:
            by_ref.setdefault(sub.ref, []).append(sub)
        semaphore = asyncio.Semaphore(YOUTUBE_CONCURRENCY)

        async def one(ref: str, group: list[SubView]) -> int:
            if not await self._gate(ref):
                return 0
            async with semaphore:
                try:
                    _, videos = await self._youtube.videos(ref)
                except ProviderError as exc:
                    log.warning("youtube_poll_failed", ref=ref, reason=exc.key)
                    return 0
            total = 0
            for sub in group:
                try:
                    total += await self._youtube_sub(sub, videos)
                except Exception:
                    log.exception("youtube_sub_failed", sub_id=sub.id, guild_id=sub.guild_id)
            return total

        results = await asyncio.gather(*(one(ref, group) for ref, group in by_ref.items()))
        return sum(results)

    async def _youtube_sub(self, sub: SubView, videos: list[VideoInfo]) -> int:
        announce, seen = new_videos(videos, sub.seen, self._clock())
        if seen == sub.seen:
            return 0
        if not await self._service.claim_videos(sub, seen):
            return 0
        sent = 0
        for video in announce:
            result = await self._announcer.announce_video(sub, video)
            if result.kind == "retry":
                # Vorübergehend gescheitert: alles zurück, beim nächsten Mal nochmal. Schon
                # Gesendetes ginge dann doppelt hinaus, darum bei Teilerfolg nicht zurückdrehen.
                if sent == 0:
                    await self._service.revert_videos(sub, seen)
                return sent
            if result.kind == "sent":
                sent += 1
        return sent

    async def poll(self) -> int:
        return await self.poll_twitch() + await self.poll_youtube()
