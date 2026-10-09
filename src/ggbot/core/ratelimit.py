"""Cooldowns über Redis, damit sie von allen Prozessen/Shards geteilt werden."""

from redis.asyncio import Redis

from ggbot.core.errors import UserError
from ggbot.core.redis import key


class Cooldown:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def hit(self, scope: str, subject: object, *, seconds: int) -> bool:
        """True, wenn die Aktion erlaubt ist (und der Cooldown jetzt läuft)."""
        return bool(await self._redis.set(key("cd", scope, subject), "1", ex=seconds, nx=True))

    async def ensure(self, scope: str, subject: object, *, seconds: int) -> None:
        if not await self.hit(scope, subject, seconds=seconds):
            remaining = await self._redis.ttl(key("cd", scope, subject))
            raise UserError("error.cooldown", seconds=max(remaining, 1))
