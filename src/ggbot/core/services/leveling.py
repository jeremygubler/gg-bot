"""Leveling ohne Discord: XP vergeben, Rang berechnen, Bestenliste, Import, Rank-Card-Stil."""

import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from redis.asyncio import Redis
from sqlalchemy.exc import SQLAlchemyError

from ggbot.core.db.engine import Database
from ggbot.core.db.repos import leveling as repo
from ggbot.core.errors import UserError
from ggbot.core.leveling.importer import ImportRow
from ggbot.core.leveling.rules import effective_multiplier, excluded, scaled
from ggbot.core.leveling.xp import (
    MAX_LEVEL,
    LevelProgress,
    level_from_xp,
    progress,
    total_xp_for_level,
)
from ggbot.core.redis import key
from ggbot.core.settings.cache import SettingsCache
from ggbot.core.settings.card import CardStyle
from ggbot.core.settings.leveling import LevelingSettings
from ggbot.core.settings.store import SettingsStore

PAGE_SIZE = 10
MAX_GIVE = 100_000_000

RandInt = Callable[[int, int], int]


@dataclass(frozen=True)
class XpContext:
    """Was der Service über die Quelle der XP wissen muss (aus Discord-Objekten abgeleitet)."""

    guild_id: int
    user_id: int
    channel_id: int
    # Elternkanal bei Threads bzw. None.
    parent_channel_id: int | None
    role_ids: frozenset[int]


@dataclass(frozen=True)
class Award:
    amount: int
    xp: int
    old_level: int
    new_level: int

    @property
    def leveled_up(self) -> bool:
        return self.new_level > self.old_level

    @property
    def level_changed(self) -> bool:
        return self.new_level != self.old_level


@dataclass(frozen=True)
class RankInfo:
    progress: LevelProgress
    position: int
    total: int
    msg_count: int
    voice_seconds: int


@dataclass(frozen=True)
class LeaderboardRow:
    position: int
    user_id: int
    xp: int
    level: int


@dataclass(frozen=True)
class LeaderboardPage:
    rows: list[LeaderboardRow]
    page: int
    pages: int
    total: int


@dataclass(frozen=True)
class ImportResult:
    created: int
    updated: int


def _utcnow() -> datetime:
    return datetime.now(UTC)


class LevelingService:
    def __init__(
        self,
        db: Database,
        store: SettingsStore,
        redis: Redis,
        *,
        clock: Callable[[], datetime] = _utcnow,
        rng: RandInt = random.randint,
    ) -> None:
        self._db = db
        self._cache = SettingsCache(store, LevelingSettings)
        self._store = store
        self._redis = redis
        self._clock = clock
        self._rng = rng

    async def settings(self, guild_id: int) -> LevelingSettings:
        return await self._cache.get(guild_id)

    def invalidate(self, guild_id: int) -> None:
        self._cache.invalidate(guild_id)

    # --- XP vergeben ---

    def _multiplier(self, settings: LevelingSettings, ctx: XpContext) -> float:
        return effective_multiplier(
            base=settings.multiplier,
            role_ids=ctx.role_ids,
            channel_ids=[ctx.channel_id, ctx.parent_channel_id],
            role_multipliers=settings.role_multipliers,
            channel_multipliers=settings.channel_multipliers,
        )

    def _excluded(self, settings: LevelingSettings, ctx: XpContext) -> bool:
        return excluded(
            channel_ids=[ctx.channel_id, ctx.parent_channel_id],
            role_ids=ctx.role_ids,
            no_xp_channels=settings.no_xp_channels,
            no_xp_roles=settings.no_xp_roles,
        )

    async def award_message(self, ctx: XpContext) -> Award | None:
        """XP für eine Nachricht, höchstens einmal pro Cooldown und Nutzer."""
        settings = await self._cache.get(ctx.guild_id)
        if not settings.enabled or self._excluded(settings, ctx):
            return None
        if settings.cooldown_seconds:
            # Erst nach den Ausschlüssen: eine ausgenommene Nachricht startet keinen Cooldown.
            started = await self._redis.set(
                key("xp", "cd", ctx.guild_id, ctx.user_id),
                "1",
                ex=settings.cooldown_seconds,
                nx=True,
            )
            if not started:
                return None
        amount = scaled(
            self._rng(settings.xp_min, settings.xp_max), self._multiplier(settings, ctx)
        )
        if amount <= 0:
            return None
        return await self._add(ctx.guild_id, ctx.user_id, amount, messages=1)

    async def award_voice(self, ctx: XpContext) -> Award | None:
        """XP für eine volle Minute im Sprachkanal. Die Taktung übernimmt der Aufrufer."""
        settings = await self._cache.get(ctx.guild_id)
        if not (settings.enabled and settings.voice.enabled) or self._excluded(settings, ctx):
            return None
        amount = scaled(
            self._rng(settings.voice.xp_min, settings.voice.xp_max),
            self._multiplier(settings, ctx),
        )
        if amount <= 0:
            return None
        return await self._add(ctx.guild_id, ctx.user_id, amount, voice_seconds=60)

    async def _add(
        self, guild_id: int, user_id: int, amount: int, *, messages: int = 0, voice_seconds: int = 0
    ) -> Award:
        async with self._db.session() as session:
            new_xp = await repo.add_xp(
                session,
                guild_id,
                user_id,
                amount,
                messages=messages,
                voice_seconds=voice_seconds,
                now=self._clock(),
            )
        old_xp = max(0, new_xp - amount)
        return Award(amount, new_xp, level_from_xp(old_xp), level_from_xp(new_xp))

    # --- Abfragen ---

    async def rank(self, guild_id: int, user_id: int) -> RankInfo:
        async with self._db.session() as session:
            row = await repo.get(session, guild_id, user_id)
            xp = row.xp if row else 0
            position = await repo.position(session, guild_id, user_id, xp)
            total = await repo.count(session, guild_id)
            msgs = row.msg_count if row else 0
            voice = row.voice_seconds if row else 0
        return RankInfo(progress(xp), position, total, msgs, voice)

    async def top(self, guild_id: int, limit: int) -> list[LeaderboardRow]:
        """Die ersten `limit` Plätze (für die öffentliche Rangliste)."""
        async with self._db.session() as session:
            members = await repo.top(session, guild_id, limit=limit, offset=0)
        return [
            LeaderboardRow(index + 1, m.user_id, m.xp, level_from_xp(m.xp))
            for index, m in enumerate(members)
        ]

    async def leaderboard(self, guild_id: int, page: int = 1) -> LeaderboardPage:
        async with self._db.session() as session:
            total = await repo.count(session, guild_id)
            pages = max(1, -(-total // PAGE_SIZE))
            page = min(max(page, 1), pages)
            offset = (page - 1) * PAGE_SIZE
            members = await repo.top(session, guild_id, limit=PAGE_SIZE, offset=offset)
        rows = [
            LeaderboardRow(offset + index + 1, m.user_id, m.xp, level_from_xp(m.xp))
            for index, m in enumerate(members)
        ]
        return LeaderboardPage(rows, page, pages, total)

    async def members_with_xp(self, guild_id: int, limit: int = 5000) -> list[tuple[int, int]]:
        """(Nutzer-ID, XP) der Mitglieder mit XP, die meisten zuerst."""
        async with self._db.session() as session:
            rows = await repo.top(session, guild_id, limit=limit, offset=0)
        return [(row.user_id, row.xp) for row in rows]

    # --- Verwaltung ---

    async def give(self, guild_id: int, user_id: int, amount: int) -> Award:
        """XP geben oder (negativ) abziehen. Das Ergebnis zeigt, ob sich das Level geändert hat."""
        if not -MAX_GIVE <= amount <= MAX_GIVE:
            raise UserError("error.leveling_amount_range", limit=MAX_GIVE)
        async with self._db.session() as session:
            before = await repo.get(session, guild_id, user_id)
            old_xp = before.xp if before else 0
            new_xp = await repo.add_xp(session, guild_id, user_id, amount, now=self._clock())
        return Award(new_xp - old_xp, new_xp, level_from_xp(old_xp), level_from_xp(new_xp))

    async def set_level(self, guild_id: int, user_id: int, level: int) -> Award:
        if not 0 <= level <= MAX_LEVEL:
            raise UserError("error.leveling_level_range", max=MAX_LEVEL)
        target = total_xp_for_level(level)
        async with self._db.session() as session:
            before = await repo.get(session, guild_id, user_id)
            old_xp = before.xp if before else 0
            await repo.set_xp(session, guild_id, user_id, target)
        return Award(target - old_xp, target, level_from_xp(old_xp), level)

    async def reset_user(self, guild_id: int, user_id: int) -> bool:
        async with self._db.session() as session:
            return await repo.delete_user(session, guild_id, user_id)

    async def reset_all(self, guild_id: int) -> int:
        async with self._db.session() as session:
            return await repo.delete_guild(session, guild_id)

    async def import_rows(
        self, guild_id: int, rows: list[ImportRow], mode: repo.ImportMode
    ) -> ImportResult:
        """Alles oder nichts: bei einem Fehler in der Mitte bleibt der Bestand unverändert."""
        pairs = [(row.user_id, row.xp) for row in rows]
        try:
            async with self._db.session() as session:
                known = await repo.existing_ids(session, guild_id, [uid for uid, _ in pairs])
                await repo.bulk_upsert(session, guild_id, pairs, mode)
        except SQLAlchemyError as exc:
            raise UserError("error.import_failed") from exc
        updated = len(known)
        return ImportResult(created=len(pairs) - updated, updated=updated)

    # --- Rank-Card-Stil ---

    async def card_style(self, guild_id: int, user_id: int) -> CardStyle:
        """Persönlicher Stil, sonst der Standard des Servers."""
        async with self._db.session() as session:
            raw = await repo.get_style(session, guild_id, user_id)
        if raw is not None:
            return CardStyle.model_validate(raw)
        return (await self._cache.get(guild_id)).card

    async def set_card_style(self, guild_id: int, user_id: int, style: CardStyle) -> None:
        async with self._db.session() as session:
            await repo.set_style(session, guild_id, user_id, style.model_dump())

    async def reset_card_style(self, guild_id: int, user_id: int) -> None:
        async with self._db.session() as session:
            await repo.delete_style(session, guild_id, user_id)
