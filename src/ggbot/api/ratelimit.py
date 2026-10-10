"""Einfache Begrenzung pro Nutzer (festes Fenster in Redis)."""

from redis.asyncio import Redis

from ggbot.api.access import api_error
from ggbot.core.redis import key


async def hit(redis: Redis, bucket: str, subject: int | str, *, limit: int, window: int) -> None:
    """Zählt einen Aufruf; wirft 429, wenn `limit` im Fenster überschritten ist."""
    name = key("ratelimit", bucket, subject)
    count = await redis.incr(name)
    if count == 1:
        await redis.expire(name, window)
    if count > limit:
        raise api_error(429, "rate_limited")
