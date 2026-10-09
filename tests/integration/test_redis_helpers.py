import asyncio

import pytest
from redis.asyncio import Redis

from ggbot.core import events
from ggbot.core.errors import UserError
from ggbot.core.ratelimit import Cooldown
from ggbot.core.snapshot import (
    ChannelInfo,
    GuildSnapshot,
    RoleInfo,
    load_snapshot,
    save_snapshot,
)


async def test_cooldown_erlaubt_einmal_pro_fenster(redis: Redis) -> None:
    cd = Cooldown(redis)
    assert await cd.hit("xp", 1, seconds=60) is True
    assert await cd.hit("xp", 1, seconds=60) is False
    assert await cd.hit("xp", 2, seconds=60) is True  # anderer Nutzer


async def test_cooldown_ensure_wirft_user_error(redis: Redis) -> None:
    cd = Cooldown(redis)
    await cd.ensure("cmd", 1, seconds=30)
    with pytest.raises(UserError) as exc:
        await cd.ensure("cmd", 1, seconds=30)
    assert exc.value.key == "error.cooldown"
    assert 1 <= exc.value.params["seconds"] <= 30


async def test_snapshot_roundtrip(redis: Redis) -> None:
    snap = GuildSnapshot(
        guild_id=5,
        name="Test",
        channels=[ChannelInfo(id=1, name="allgemein", type="text", position=0, parent_id=None)],
        roles=[RoleInfo(id=2, name="Mod", color=255, position=3, managed=False)],
        emojis=[],
    )
    assert await load_snapshot(redis, 5) is None
    await save_snapshot(redis, snap)
    assert await load_snapshot(redis, 5) == snap
    assert await load_snapshot(redis, 6) is None


async def test_event_roundtrip_und_kaputte_nachricht_wird_uebersprungen(redis: Redis) -> None:
    received: list[events.Event] = []

    async def listen() -> None:
        async for event in events.subscribe(redis):
            received.append(event)
            return

    task = asyncio.create_task(listen())
    await asyncio.sleep(0.2)
    await redis.publish(events.CHANNEL, "kein json")
    await redis.publish(events.CHANNEL, '{"type": "unbekannt"}')
    await events.publish(redis, events.GuildSync(guild_id=3))
    await asyncio.wait_for(task, timeout=3)
    assert received == [events.GuildSync(guild_id=3)]
