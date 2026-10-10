"""Willkommen und Abschied: Einstellungen, Doppelschutz und Drosselung. Ohne Discord."""

from redis.asyncio import Redis

from ggbot.core.ratelimit import Cooldown
from ggbot.core.redis import key
from ggbot.core.settings.cache import SettingsCache
from ggbot.core.settings.store import SettingsStore
from ggbot.core.settings.welcome import WelcomeSettings

CLAIM_SECONDS = 60
# Mehr Begrüssungen pro Minute und Server sind ein Ansturm; die Nachricht hilft dann niemandem
# und würde den Kanal fluten (und das Rate Limit des Bots aufbrauchen).
MAX_MESSAGES_PER_MINUTE = 20


class WelcomeService:
    def __init__(self, store: SettingsStore, redis: Redis) -> None:
        self._cache = SettingsCache(store, WelcomeSettings)
        self._redis = redis
        self._cooldown = Cooldown(redis)

    async def settings(self, guild_id: int) -> WelcomeSettings:
        return await self._cache.get(guild_id)

    def invalidate(self, guild_id: int) -> None:
        self._cache.invalidate(guild_id)

    async def claim(self, guild_id: int, user_id: int, kind: str) -> bool:
        """True genau einmal je Mitglied und Art (join/leave) in kurzer Zeit.

        Beitritt und "Screening bestanden" können für dasselbe Mitglied beide den Ablauf auslösen,
        und das Gateway liefert Ereignisse gelegentlich doppelt.
        """
        return await self._cooldown.hit(
            f"welcome:{kind}", f"{guild_id}:{user_id}", seconds=CLAIM_SECONDS
        )

    async def throttle(self, guild_id: int) -> bool:
        """True, wenn der Server in dieser Minute noch eine Nachricht bekommen darf."""
        redis_key = key("welcome", "rate", guild_id)
        count = await self._redis.incr(redis_key)
        if count == 1:
            await self._redis.expire(redis_key, 60)
        return count <= MAX_MESSAGES_PER_MINUTE
