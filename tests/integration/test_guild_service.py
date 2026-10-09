from datetime import UTC, datetime

import pytest
from redis.asyncio import Redis

from ggbot.core.db.engine import Database
from ggbot.core.errors import UserError
from ggbot.core.services.guilds import GuildService


def service(db: Database, redis: Redis, default: str = "de") -> GuildService:
    return GuildService(db, redis, default_locale=default)


async def test_unbekannter_server_nutzt_standardsprache(db: Database, redis: Redis) -> None:
    assert await service(db, redis, "en").get_locale(99) == "en"


async def test_ohne_server_nutzt_standardsprache(db: Database, redis: Redis) -> None:
    assert await service(db, redis).get_locale(None) == "de"


async def test_ensure_legt_an_und_ist_idempotent(db: Database, redis: Redis) -> None:
    svc = service(db, redis)
    await svc.ensure(1)
    await svc.ensure(1)
    assert await svc.get_locale(1) == "de"


async def test_sprache_aendern_wirkt_sofort_trotz_cache(db: Database, redis: Redis) -> None:
    svc = service(db, redis)
    await svc.ensure(1)
    assert await svc.get_locale(1) == "de"  # Cache gefüllt
    await svc.set_locale(1, "en")
    assert await svc.get_locale(1) == "en"


async def test_sprache_ohne_vorherigen_ensure(db: Database, redis: Redis) -> None:
    svc = service(db, redis)
    await svc.set_locale(5, "en")
    assert await svc.get_locale(5) == "en"


async def test_unbekannte_sprache_wird_abgelehnt(db: Database, redis: Redis) -> None:
    with pytest.raises(ValueError, match="unbekannte Sprache"):
        await service(db, redis).set_locale(1, "fr")


async def test_server_sind_getrennt(db: Database, redis: Redis) -> None:
    svc = service(db, redis)
    await svc.set_locale(1, "en")
    assert await svc.get_locale(2) == "de"


async def test_zeitzone_gueltig_und_ungueltig(db: Database, redis: Redis) -> None:
    svc = service(db, redis)
    assert await svc.set_timezone(1, "Europe/Zurich") == "Europe/Zurich"
    for bad in ("Mars/Olympus", "", "../etc/passwd"):
        with pytest.raises(UserError) as exc:
            await svc.set_timezone(1, bad)
        assert exc.value.key == "error.unknown_timezone"


async def test_wiederbeitritt_hebt_austritt_auf(db: Database, redis: Redis) -> None:
    from sqlalchemy import text

    svc = service(db, redis)
    await svc.ensure(1)
    await svc.mark_left(1, datetime.now(UTC))
    await svc.ensure(1)
    async with db.session() as session:
        left = (await session.execute(text("SELECT left_at FROM guilds WHERE id = 1"))).scalar()
    assert left is None
