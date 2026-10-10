import pytest
from redis.asyncio import Redis

from ggbot.core import status
from ggbot.core.status import BotStatus, count, read_counters, read_status, write_status


def make(**kw: object) -> BotStatus:
    base: dict[str, object] = dict(
        version="1.2.3", started_at=100.0, guilds=3, shards=1, latency_ms=12.5, written_at=200.0
    )
    return BotStatus(**{**base, **kw})


async def test_status_rundreise_und_ablauf(redis: Redis) -> None:
    await write_status(redis, make())

    assert await read_status(redis) == make()
    assert 0 < await redis.ttl(status.HEARTBEAT_KEY) <= status.HEARTBEAT_TTL


async def test_ohne_lebenszeichen_ist_der_bot_aus(redis: Redis) -> None:
    assert await read_status(redis) is None


async def test_altes_format_gilt_als_lebendig(redis: Redis) -> None:
    """Eine ältere Version schrieb nur "1"; ein Update darf den Bot nicht kurz als tot zeigen."""
    await redis.set(status.HEARTBEAT_KEY, "1", ex=30)

    old = await read_status(redis)

    assert old is not None
    assert old.version == "unknown"


async def test_zaehler(redis: Redis) -> None:
    assert await read_counters(redis) == {"commands": 0, "command_errors": 0}

    await count(redis, "commands")
    await count(redis, "commands")
    await count(redis, "command_errors")

    assert await read_counters(redis) == {"commands": 2, "command_errors": 1}


async def test_latenz_darf_fehlen(redis: Redis) -> None:
    await write_status(redis, make(latency_ms=None))

    loaded = await read_status(redis)
    assert loaded is not None
    assert loaded.latency_ms is None


def test_pydantic_lehnt_unsinn_ab() -> None:
    with pytest.raises(ValueError, match="validation error"):
        BotStatus.model_validate_json('{"version": 1}')
