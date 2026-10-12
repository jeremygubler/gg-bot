"""Auto-Löschen: Einstellungen und die Sperre gegen doppelte Läufe. Ohne Discord."""

from redis.asyncio import Redis

from ggbot.core.redis import key
from ggbot.core.settings.autodelete import AutodeleteSettings
from ggbot.core.settings.cache import SettingsCache
from ggbot.core.settings.store import SettingsStore


class AutodeleteService:
    def __init__(self, redis: Redis, store: SettingsStore) -> None:
        self._redis = redis
        self._settings = SettingsCache(store, AutodeleteSettings)

    async def settings(self, guild_id: int) -> AutodeleteSettings:
        return await self._settings.get(guild_id)

    def invalidate(self, guild_id: int) -> None:
        self._settings.invalidate(guild_id)

    async def claim_run(self, guild_id: int, channel_id: int, *, seconds: int) -> bool:
        """True, wenn dieser Prozess den Lauf für den Kanal übernimmt. Mehrere Shards oder ein
        langsamer Vorlauf sollen nicht gleichzeitig dieselben Nachrichten anfassen."""
        claimed = await self._redis.set(
            key("autodelete", "run", guild_id, channel_id), "1", ex=seconds, nx=True
        )
        return bool(claimed)
