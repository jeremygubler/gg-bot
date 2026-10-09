"""Redis-Anbindung und Schlüssel-Namensraum."""

from redis.asyncio import Redis

NAMESPACE = "gg"


def key(*parts: object) -> str:
    """Baut einen Schlüssel im Namensraum, z. B. key("settings", 1, "general")."""
    return ":".join([NAMESPACE, *(str(part) for part in parts)])


def create_redis(url: str) -> Redis:
    return Redis.from_url(url, decode_responses=True)


async def get_str(redis: Redis, name: str) -> str | None:
    """`GET` als str. Der Client ist mit decode_responses=True gebaut, die Typen
    von redis-py kennen das aber nicht."""
    value = await redis.get(name)
    if isinstance(value, bytes):
        return value.decode()
    return value
