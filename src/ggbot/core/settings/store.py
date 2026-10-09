"""Lesen/Schreiben der Server-Einstellungen mit Redis-Cache und Invalidierung."""

from typing import TypeVar

from redis.asyncio import Redis

from ggbot.core import events
from ggbot.core.db.engine import Database
from ggbot.core.db.repos import settings as repo
from ggbot.core.redis import get_str, key
from ggbot.core.settings.schema import SettingsModel

M = TypeVar("M", bound=SettingsModel)

CACHE_TTL_SECONDS = 3600


def cache_key(guild_id: int, module: str) -> str:
    return key("settings", guild_id, module)


class SettingsStore:
    def __init__(self, db: Database, redis: Redis, ttl: int = CACHE_TTL_SECONDS) -> None:
        self._db = db
        self._redis = redis
        self._ttl = ttl

    async def get(self, guild_id: int, model: type[M]) -> M:
        cached = await get_str(self._redis, cache_key(guild_id, model.module))
        if cached is not None:
            return model.model_validate_json(cached)

        async with self._db.session() as session:
            raw = await repo.get(session, guild_id, model.module)
        settings = model.model_validate(raw) if raw is not None else model()
        await self._redis.set(
            cache_key(guild_id, model.module), settings.model_dump_json(), ex=self._ttl
        )
        return settings

    async def save(
        self, guild_id: int, settings: SettingsModel, *, audit_user_id: int | None = None
    ) -> None:
        """Speichert, räumt den Cache und meldet die Änderung.

        Mit `audit_user_id` (Dashboard) wird zusätzlich Vorher/Nachher protokolliert.
        """
        module = settings.module
        after = settings.model_dump(mode="json")
        async with self._db.session() as session:
            if audit_user_id is not None:
                before = await repo.get(session, guild_id, module)
                await repo.add_audit(session, guild_id, audit_user_id, module, before, after)
            await repo.upsert(session, guild_id, module, after, settings.schema_version)

        # Erst nach dem Commit löschen: sonst könnte ein paralleler Leser den alten
        # Stand zurück in den Cache schreiben.
        await self._redis.delete(cache_key(guild_id, module))
        await events.publish(self._redis, events.SettingsChanged(guild_id=guild_id, module=module))
