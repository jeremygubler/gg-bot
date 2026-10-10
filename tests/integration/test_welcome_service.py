from redis.asyncio import Redis

from ggbot.core.db.engine import Database
from ggbot.core.redis import key
from ggbot.core.services.welcome import MAX_MESSAGES_PER_MINUTE, WelcomeService
from ggbot.core.settings.store import SettingsStore
from ggbot.core.settings.welcome import WelcomeSettings


async def test_einstellungen_und_invalidierung(db: Database, redis: Redis) -> None:
    store = SettingsStore(db, redis)
    service = WelcomeService(store, redis)
    assert (await service.settings(1)).join_enabled is False
    await store.save(1, WelcomeSettings(join_enabled=True, join_channel_id=5))
    assert (await service.settings(1)).join_enabled is False  # noch im Prozess-Cache
    service.invalidate(1)
    assert (await service.settings(1)).join_enabled is True
    assert (await service.settings(2)).join_enabled is False  # anderer Server


async def test_claim_gilt_einmal_je_mitglied_und_art(db: Database, redis: Redis) -> None:
    service = WelcomeService(SettingsStore(db, redis), redis)
    assert await service.claim(1, 7, "join") is True
    assert await service.claim(1, 7, "join") is False  # doppeltes Ereignis
    assert await service.claim(1, 7, "leave") is True  # andere Art
    assert await service.claim(1, 8, "join") is True  # anderes Mitglied
    assert await service.claim(2, 7, "join") is True  # anderer Server


async def test_drosselung_pro_server(db: Database, redis: Redis) -> None:
    service = WelcomeService(SettingsStore(db, redis), redis)
    results = [await service.throttle(1) for _ in range(MAX_MESSAGES_PER_MINUTE + 3)]
    assert results[:MAX_MESSAGES_PER_MINUTE] == [True] * MAX_MESSAGES_PER_MINUTE
    assert results[MAX_MESSAGES_PER_MINUTE:] == [False] * 3
    assert await service.throttle(2) is True  # anderer Server hat sein eigenes Fenster
    assert 0 < await redis.ttl(key("welcome", "rate", 1)) <= 60


async def test_drosselfenster_endet(db: Database, redis: Redis) -> None:
    service = WelcomeService(SettingsStore(db, redis), redis)
    for _ in range(MAX_MESSAGES_PER_MINUTE):
        await service.throttle(1)
    assert await service.throttle(1) is False
    await redis.delete(key("welcome", "rate", 1))  # entspricht dem Ablauf der Minute
    assert await service.throttle(1) is True
