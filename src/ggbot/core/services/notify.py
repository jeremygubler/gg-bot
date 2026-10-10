"""Benachrichtigungen: Abos verwalten und Zustandsänderungen atomar beanspruchen. Ohne Discord."""

from dataclasses import dataclass

from ggbot.core import messages
from ggbot.core.db.engine import Database
from ggbot.core.db.models import NotifySubscription
from ggbot.core.db.repos import notify as repo
from ggbot.core.errors import UserError
from ggbot.core.notify.rules import StreamState
from ggbot.core.notify.types import Resolved

MAX_SUBSCRIPTIONS = 25
MAX_MESSAGE = 300
TWITCH_FIELDS = ("streamer", "title", "game", "url", "viewers")
YOUTUBE_FIELDS = ("channel", "title", "url")
FIELDS = {"twitch": TWITCH_FIELDS, "youtube": YOUTUBE_FIELDS}


@dataclass(frozen=True)
class SubView:
    id: int
    guild_id: int
    provider: str
    ref: str
    display_name: str
    channel_id: int
    message: str | None
    role_id: int | None
    last_key: str | None
    live: bool
    misses: int
    seen: tuple[str, ...]
    message_id: int | None

    @property
    def stream_state(self) -> StreamState:
        return StreamState(self.last_key, self.live, self.misses)

    @classmethod
    def from_model(cls, s: NotifySubscription) -> "SubView":
        return cls(
            s.id,
            s.guild_id,
            s.provider,
            s.ref,
            s.display_name,
            s.channel_id,
            s.message,
            s.role_id,
            s.last_key,
            s.live,
            s.misses,
            tuple(s.seen),
            s.message_id,
        )


class NotifyService:
    def __init__(self, db: Database) -> None:
        self._db = db

    # --- Verwalten ---

    async def add(
        self,
        guild_id: int,
        resolved: Resolved,
        *,
        channel_id: int,
        message: str | None = None,
        role_id: int | None = None,
    ) -> SubView:
        if message is not None:
            if len(message) > MAX_MESSAGE:
                raise UserError("error.notify_message_long", limit=MAX_MESSAGE)
            try:
                messages.validate_template(message, FIELDS[resolved.provider])
            except ValueError as exc:
                raise UserError("error.notify_message_invalid", detail=str(exc)) from exc
        async with self._db.session() as session:
            if await repo.count(session, guild_id) >= MAX_SUBSCRIPTIONS:
                raise UserError("error.notify_limit", limit=MAX_SUBSCRIPTIONS)
            if await repo.by_ref(session, guild_id, resolved.provider, resolved.ref):
                raise UserError("error.notify_duplicate", name=resolved.display_name)
            sub = await repo.add(
                session,
                guild_id=guild_id,
                provider=resolved.provider,
                ref=resolved.ref,
                display_name=resolved.display_name[:100],
                channel_id=channel_id,
                message=message or None,
                role_id=role_id,
                # Was beim Hinzufügen schon läuft oder da ist, gilt als bekannt.
                last_key=resolved.live_stream_id,
                live=resolved.live_stream_id is not None,
                seen=list(resolved.seen_videos),
            )
            return SubView.from_model(sub)

    async def remove(self, guild_id: int, provider: str, ref: str) -> SubView:
        async with self._db.session() as session:
            sub = await repo.by_ref(session, guild_id, provider, ref)
            if sub is None:
                raise UserError("error.notify_unknown")
            view = SubView.from_model(sub)
            await repo.delete_sub(session, guild_id, sub.id)
            return view

    async def get(self, guild_id: int, sub_id: int) -> SubView:
        async with self._db.session() as session:
            sub = await repo.get(session, guild_id, sub_id)
            if sub is None:
                raise UserError("error.notify_unknown")
            return SubView.from_model(sub)

    async def for_guild(self, guild_id: int) -> list[SubView]:
        async with self._db.session() as session:
            return [SubView.from_model(s) for s in await repo.list_for_guild(session, guild_id)]

    # --- Abfragen (über alle Server) ---

    async def all_for_provider(self, provider: str) -> list[SubView]:
        async with self._db.session() as session:
            return [SubView.from_model(s) for s in await repo.all_for_provider(session, provider)]

    async def claim_stream(self, sub: SubView, stream_id: str) -> bool:
        async with self._db.session() as session:
            return await repo.claim_stream(session, sub.id, old_key=sub.last_key, new_key=stream_id)

    async def revert_stream(self, sub: SubView, stream_id: str) -> None:
        async with self._db.session() as session:
            await repo.revert_stream(
                session,
                sub.id,
                new_key=stream_id,
                old_key=sub.last_key,
                live=sub.live,
                misses=sub.misses,
            )

    async def save_stream_state(self, sub: SubView, state: StreamState) -> None:
        async with self._db.session() as session:
            await repo.save_stream_state(session, sub.id, live=state.live, misses=state.misses)

    async def set_message(self, sub_id: int, message_id: int | None) -> None:
        async with self._db.session() as session:
            await repo.set_message(session, sub_id, message_id)

    async def claim_videos(self, sub: SubView, new_seen: tuple[str, ...]) -> bool:
        async with self._db.session() as session:
            return await repo.claim_videos(session, sub.id, old=list(sub.seen), new=list(new_seen))

    async def revert_videos(self, sub: SubView, new_seen: tuple[str, ...]) -> None:
        async with self._db.session() as session:
            await repo.claim_videos(session, sub.id, old=list(new_seen), new=list(sub.seen))
