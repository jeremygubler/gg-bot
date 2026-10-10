"""Server-Stammdaten (Sprache, Zeitzone) mit Redis-Cache."""

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from redis.asyncio import Redis

from ggbot.core import events
from ggbot.core.db.engine import Database
from ggbot.core.db.repos import guilds as repo
from ggbot.core.errors import UserError
from ggbot.core.i18n import LOCALES
from ggbot.core.redis import get_str, key

CACHE_TTL_SECONDS = 3600


class GuildService:
    def __init__(self, db: Database, redis: Redis, *, default_locale: str) -> None:
        self._db = db
        self._redis = redis
        self._default_locale = default_locale

    def _key(self, guild_id: int) -> str:
        return key("guild", guild_id, "locale")

    async def ensure(self, guild_id: int) -> None:
        """Beim Beitritt/Start: Server anlegen, Cache verwerfen."""
        async with self._db.session() as session:
            await repo.ensure(session, guild_id, default_locale=self._default_locale)
        await self._redis.delete(self._key(guild_id))

    async def mark_left(self, guild_id: int, when: datetime) -> None:
        async with self._db.session() as session:
            await repo.mark_left(session, guild_id, when)

    async def get_locale(self, guild_id: int | None) -> str:
        if guild_id is None:
            return self._default_locale
        cached = await get_str(self._redis, self._key(guild_id))
        if cached is not None:
            return cached
        async with self._db.session() as session:
            guild = await repo.get(session, guild_id)
        locale = guild.locale if guild is not None else self._default_locale
        await self._redis.set(self._key(guild_id), locale, ex=CACHE_TTL_SECONDS)
        return locale

    async def set_locale(self, guild_id: int, locale: str) -> None:
        if locale not in LOCALES:
            raise ValueError(f"unbekannte Sprache: {locale}")
        async with self._db.session() as session:
            await repo.ensure(session, guild_id, default_locale=self._default_locale)
            await repo.set_locale(session, guild_id, locale)
        await self._redis.delete(self._key(guild_id))
        await events.publish(self._redis, events.SettingsChanged(guild_id=guild_id, module="guild"))

    async def get_timezone(self, guild_id: int) -> str:
        async with self._db.session() as session:
            guild = await repo.get(session, guild_id)
        return guild.timezone if guild is not None else "UTC"

    async def set_timezone(self, guild_id: int, name: str) -> str:
        try:
            ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
            raise UserError("error.unknown_timezone", name=name) from exc
        async with self._db.session() as session:
            await repo.ensure(session, guild_id, default_locale=self._default_locale)
            await repo.set_timezone(session, guild_id, name)
        await events.publish(self._redis, events.SettingsChanged(guild_id=guild_id, module="guild"))
        return name
