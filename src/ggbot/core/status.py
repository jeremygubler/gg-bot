"""Lebenszeichen und Zähler des Bots in Redis, für Healthcheck, Dashboard und Metriken.

Der Bot schreibt, die API liest. Es gibt bewusst keinen zweiten HTTP-Server im Bot: ein weiterer
offener Port wäre eine weitere Angriffsfläche, und Redis ist ohnehin die gemeinsame Leitung.
"""

import time

from pydantic import BaseModel
from redis.asyncio import Redis

from ggbot.core.redis import get_str, key

HEARTBEAT_KEY = key("heartbeat", "bot")
HEARTBEAT_INTERVAL = 10
HEARTBEAT_TTL = 30
COUNTERS = ("commands", "command_errors")


class BotStatus(BaseModel):
    version: str
    started_at: float
    guilds: int
    shards: int
    latency_ms: float | None
    written_at: float


async def write_status(redis: Redis, status: BotStatus) -> None:
    await redis.set(HEARTBEAT_KEY, status.model_dump_json(), ex=HEARTBEAT_TTL)


async def read_status(redis: Redis) -> BotStatus | None:
    """None, wenn der Bot kein frisches Lebenszeichen hinterlassen hat (läuft nicht oder hängt)."""
    raw = await get_str(redis, HEARTBEAT_KEY)
    if raw is None:
        return None
    try:
        return BotStatus.model_validate_json(raw)
    except ValueError:
        # Altes Format ("1") aus einer früheren Version: gilt als lebendig, aber ohne Zahlen.
        return BotStatus(
            version="unknown",
            started_at=time.time(),
            guilds=0,
            shards=0,
            latency_ms=None,
            written_at=time.time(),
        )


def _counter_key(name: str) -> str:
    return key("metrics", name)


async def count(redis: Redis, name: str) -> None:
    await redis.incr(_counter_key(name))


async def read_counters(redis: Redis) -> dict[str, int]:
    values = await redis.mget([_counter_key(name) for name in COUNTERS])
    return {name: int(value or 0) for name, value in zip(COUNTERS, values, strict=True)}
