"""Snapshot-Pflege: der Bot gilt im Dashboard nur so lange als anwesend, wie er es ist."""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from redis.asyncio import Redis

from ggbot.bot.client import GGBot
from ggbot.bot.cogs import lifecycle
from ggbot.bot.cogs.lifecycle import LifecycleCog
from ggbot.bot.services import Services
from ggbot.core.config import AppConfig
from ggbot.core.db.engine import Database
from ggbot.core.snapshot import load_snapshot, snapshot_exists

GUILD = 123456789012345678


@pytest.fixture
async def bot(db: Database, redis: Redis) -> GGBot:
    return GGBot(Services.build(AppConfig(), db, redis))


def make_guild() -> MagicMock:
    guild = MagicMock()
    guild.id = GUILD
    guild.name = "Alpha"
    guild.channels = [
        SimpleNamespace(
            id=7, name="general", type=SimpleNamespace(name="text"), position=0, category_id=None
        )
    ]
    guild.roles = [
        SimpleNamespace(id=5, name="Mod", color=SimpleNamespace(value=0), position=1, managed=False)
    ]
    guild.emojis = []
    guild.me.top_role.position = 3
    return guild


async def test_beitritt_schreibt_snapshot_und_austritt_loescht_ihn(bot: GGBot) -> None:
    cog = LifecycleCog(bot)
    guild = make_guild()

    await cog.on_guild_join(guild)
    assert await snapshot_exists(bot.services.redis, GUILD)

    await cog.on_guild_remove(guild)
    assert not await snapshot_exists(bot.services.redis, GUILD)


async def test_verspaeteter_snapshot_nach_austritt_legt_den_server_nicht_wieder_an(
    bot: GGBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lifecycle, "DEBOUNCE_SECONDS", 0)
    cog = LifecycleCog(bot)
    guild = make_guild()
    monkeypatch.setattr(bot, "get_guild", lambda guild_id: None)  # Bot ist schon weg

    cog._schedule(guild)
    await asyncio.gather(*cog._pending.values())

    assert await load_snapshot(bot.services.redis, GUILD) is None


async def test_snapshot_nach_aenderung_wird_geschrieben_solange_der_bot_da_ist(
    bot: GGBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lifecycle, "DEBOUNCE_SECONDS", 0)
    cog = LifecycleCog(bot)
    guild = make_guild()
    monkeypatch.setattr(bot, "get_guild", lambda guild_id: guild)

    cog._schedule(guild)
    await asyncio.gather(*cog._pending.values())

    snapshot = await load_snapshot(bot.services.redis, GUILD)
    assert snapshot is not None
    assert snapshot.channels[0].id == 7
