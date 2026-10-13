"""Sticky-Nachrichten: Texte je Kanal und die Sperre, die Neuposts bündelt. Ohne Discord."""

import time
from collections.abc import Callable
from dataclasses import dataclass

from redis.asyncio import Redis

from ggbot.core.db.engine import Database
from ggbot.core.db.repos import channels as repo
from ggbot.core.errors import UserError
from ggbot.core.redis import key
from ggbot.core.sticky import rules

CACHE_SECONDS = 30.0


@dataclass(frozen=True)
class StickyView:
    guild_id: int
    channel_id: int
    text: str
    last_message_id: int | None


class StickyService:
    def __init__(
        self, db: Database, redis: Redis, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._db = db
        self._redis = redis
        self._clock = clock
        # Jede Nachricht im Server fragt hier an; ohne Zwischenspeicher wäre das eine
        # Datenbankabfrage pro Nachricht.
        self._channels: dict[int, tuple[float, frozenset[int]]] = {}

    def invalidate(self, guild_id: int) -> None:
        self._channels.pop(guild_id, None)

    async def channel_ids(self, guild_id: int) -> frozenset[int]:
        cached = self._channels.get(guild_id)
        if cached is not None and cached[0] > self._clock():
            return cached[1]
        async with self._db.session() as session:
            ids = frozenset(r.channel_id for r in await repo.sticky_list(session, guild_id))
        self._channels[guild_id] = (self._clock() + CACHE_SECONDS, ids)
        return ids

    async def set(self, guild_id: int, channel_id: int, text: str, user_id: int) -> StickyView:
        cleaned = rules.clean_text(text)
        async with self._db.session() as session:
            existing = await repo.sticky_get(session, channel_id)
            if existing is None and await repo.sticky_count(session, guild_id) >= (
                rules.MAX_STICKY_PER_GUILD
            ):
                raise UserError("error.sticky_limit", max=rules.MAX_STICKY_PER_GUILD)
            await repo.sticky_upsert(session, guild_id, channel_id, cleaned, user_id)
            row = await repo.sticky_get(session, channel_id)
        self.invalidate(guild_id)
        assert row is not None  # gerade geschrieben
        return StickyView(row.guild_id, row.channel_id, row.text, row.last_message_id)

    async def get(self, channel_id: int) -> StickyView | None:
        async with self._db.session() as session:
            row = await repo.sticky_get(session, channel_id)
        if row is None:
            return None
        return StickyView(row.guild_id, row.channel_id, row.text, row.last_message_id)

    async def list(self, guild_id: int) -> list[StickyView]:
        async with self._db.session() as session:
            rows = await repo.sticky_list(session, guild_id)
        return [StickyView(r.guild_id, r.channel_id, r.text, r.last_message_id) for r in rows]

    async def remove(self, guild_id: int, channel_id: int) -> StickyView:
        async with self._db.session() as session:
            row = await repo.sticky_delete(session, guild_id, channel_id)
        self.invalidate(guild_id)
        if row is None:
            raise UserError("error.sticky_unknown")
        return StickyView(row.guild_id, row.channel_id, row.text, row.last_message_id)

    async def set_last(self, channel_id: int, message_id: int | None) -> None:
        async with self._db.session() as session:
            await repo.sticky_set_last(session, channel_id, message_id)

    async def claim_repost(self, channel_id: int) -> bool:
        """True für genau einen Anrufer je Zeitfenster: der plant den Neupost ein."""
        claimed = await self._redis.set(
            key("sticky", "repost", channel_id), "1", ex=rules.REPOST_DELAY_SECONDS, nx=True
        )
        return bool(claimed)
