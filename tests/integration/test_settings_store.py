import asyncio
from typing import ClassVar

import pytest
from redis.asyncio import Redis
from sqlalchemy import text

from ggbot.core import events
from ggbot.core.db.engine import Database
from ggbot.core.settings.schema import GeneralSettings, SettingsModel
from ggbot.core.settings.store import SettingsStore, cache_key


async def test_default_wenn_nichts_gespeichert(db: Database, redis: Redis) -> None:
    store = SettingsStore(db, redis)
    assert (await store.get(1, GeneralSettings)).embed_color == 0x5865F2


async def test_speichern_und_lesen(db: Database, redis: Redis) -> None:
    store = SettingsStore(db, redis)
    await store.save(1, GeneralSettings(embed_color=0xFF0000))
    assert (await store.get(1, GeneralSettings)).embed_color == 0xFF0000


async def test_server_sind_getrennt(db: Database, redis: Redis) -> None:
    store = SettingsStore(db, redis)
    await store.save(1, GeneralSettings(embed_color=0x111111))
    assert (await store.get(2, GeneralSettings)).embed_color == 0x5865F2


async def test_lesen_fuellt_den_cache(db: Database, redis: Redis) -> None:
    store = SettingsStore(db, redis)
    await store.get(1, GeneralSettings)
    assert await redis.exists(cache_key(1, "general")) == 1


async def test_cache_wird_gelesen_nicht_die_db(db: Database, redis: Redis) -> None:
    store = SettingsStore(db, redis)
    await store.save(1, GeneralSettings(embed_color=0x123456))
    await store.get(1, GeneralSettings)  # füllt den Cache
    async with db.session() as session:
        await session.execute(text("DELETE FROM guild_settings"))
    assert (await store.get(1, GeneralSettings)).embed_color == 0x123456


async def test_speichern_invalidiert_den_cache(db: Database, redis: Redis) -> None:
    store = SettingsStore(db, redis)
    await store.save(1, GeneralSettings(embed_color=1))
    await store.get(1, GeneralSettings)
    await store.save(1, GeneralSettings(embed_color=2))
    assert await redis.exists(cache_key(1, "general")) == 0
    assert (await store.get(1, GeneralSettings)).embed_color == 2


async def test_speichern_meldet_ein_event(db: Database, redis: Redis) -> None:
    store = SettingsStore(db, redis)
    received: list[events.Event] = []

    async def listen() -> None:
        async for event in events.subscribe(redis):
            received.append(event)
            return

    task = asyncio.create_task(listen())
    await asyncio.sleep(0.2)  # Abo muss stehen, bevor publiziert wird
    await store.save(7, GeneralSettings())
    await asyncio.wait_for(task, timeout=3)
    assert received == [events.SettingsChanged(guild_id=7, module="general")]


async def test_audit_nur_mit_dashboard_nutzer(db: Database, redis: Redis) -> None:
    store = SettingsStore(db, redis)
    await store.save(1, GeneralSettings(embed_color=1))
    await store.save(1, GeneralSettings(embed_color=2), audit_user_id=42)
    async with db.session() as session:
        rows = (
            await session.execute(
                text("SELECT user_id, before, after FROM dashboard_audit WHERE guild_id = 1")
            )
        ).all()
    assert len(rows) == 1
    user_id, before, after = rows[0]
    assert user_id == 42
    assert before["embed_color"] == 1
    assert after["embed_color"] == 2


async def test_ungueltiger_wert_wird_nicht_gespeichert() -> None:
    with pytest.raises(ValueError):  # noqa: PT011 - pydantic.ValidationError ist ein ValueError
        GeneralSettings(embed_color=-1)
    with pytest.raises(ValueError):  # noqa: PT011
        GeneralSettings(embed_color=0x1000000)


async def test_unbekannte_felder_und_neue_defaults(db: Database, redis: Redis) -> None:
    class Evolved(SettingsModel):
        module: ClassVar[str] = "general"
        embed_color: int = 5
        neu: bool = True

    async with db.session() as session:
        await session.execute(
            text(
                "INSERT INTO guild_settings (guild_id, module, data) "
                "VALUES (1, 'general', '{\"embed_color\": 9, \"veraltet\": 1}')"
            )
        )
    store = SettingsStore(db, redis)
    loaded = await store.get(1, Evolved)
    assert (loaded.embed_color, loaded.neu) == (9, True)


async def test_kaputte_daten_in_der_db_sind_laut(db: Database, redis: Redis) -> None:
    async with db.session() as session:
        await session.execute(
            text(
                "INSERT INTO guild_settings (guild_id, module, data) "
                "VALUES (1, 'general', '{\"embed_color\": \"kein-int\"}')"
            )
        )
    with pytest.raises(ValueError):  # noqa: PT011
        await SettingsStore(db, redis).get(1, GeneralSettings)
