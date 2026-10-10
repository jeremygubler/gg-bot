"""Lebenszeichen und Zähler des Bots: was Healthcheck, Dashboard und Metriken zu sehen bekommen."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from discord import app_commands
from redis.asyncio import Redis

from ggbot import __version__
from ggbot.bot.client import GGBot
from ggbot.bot.services import Services
from ggbot.core import status
from ggbot.core.config import AppConfig
from ggbot.core.db.engine import Database
from ggbot.core.errors import UserError


@pytest.fixture
async def bot(db: Database, redis: Redis) -> GGBot:
    return GGBot(Services.build(AppConfig(), db, redis))


async def test_herzschlag_schreibt_version_und_zahlen(
    bot: GGBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(status, "HEARTBEAT_INTERVAL", 0.01)
    monkeypatch.setattr(bot, "is_ready", lambda: True)
    task = asyncio.create_task(bot._heartbeat())
    try:
        for _ in range(100):
            written = await status.read_status(bot.services.redis)
            if written is not None:
                break
            await asyncio.sleep(0.02)
    finally:
        task.cancel()
    assert written is not None
    assert written.version == __version__
    assert written.guilds == 0
    assert written.latency_ms is None  # vor der Verbindung ist die Latenz NaN
    assert written.shards >= 1


async def test_ohne_bereitschaft_kein_lebenszeichen(
    bot: GGBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(status, "HEARTBEAT_INTERVAL", 0.01)
    monkeypatch.setattr(bot, "is_ready", lambda: False)
    task = asyncio.create_task(bot._heartbeat())
    await asyncio.sleep(0.1)
    task.cancel()

    assert await status.read_status(bot.services.redis) is None


async def test_fehler_beim_schreiben_beendet_den_herzschlag_nicht(
    bot: GGBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ein Redis-Aussetzer darf den Bot nicht „ungesund“ lassen, wenn Redis zurück ist."""
    monkeypatch.setattr(status, "HEARTBEAT_INTERVAL", 0.01)
    monkeypatch.setattr(bot, "is_ready", lambda: True)
    real = status.write_status
    calls = {"n": 0}

    async def flaky(redis: Redis, value: status.BotStatus) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("Redis weg")
        await real(redis, value)

    monkeypatch.setattr(status, "write_status", flaky)
    task = asyncio.create_task(bot._heartbeat())
    try:
        for _ in range(100):
            if await status.read_status(bot.services.redis) is not None:
                break
            await asyncio.sleep(0.02)
    finally:
        task.cancel()

    assert calls["n"] >= 2
    assert await status.read_status(bot.services.redis) is not None


async def test_abgeschlossene_befehle_werden_gezaehlt(bot: GGBot) -> None:
    await bot.on_app_command_completion(MagicMock(), MagicMock())
    await bot.on_app_command_completion(MagicMock(), MagicMock())

    assert (await status.read_counters(bot.services.redis))["commands"] == 2


async def test_zaehler_fehler_kosten_keinen_befehl(
    bot: GGBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(status, "count", AsyncMock(side_effect=ConnectionError("weg")))

    await bot.on_app_command_completion(MagicMock(), MagicMock())  # darf nicht werfen


def fake_interaction(bot: GGBot) -> MagicMock:
    interaction = MagicMock()
    interaction.client = bot
    interaction.guild_id = 1
    interaction.response.is_done.return_value = False
    interaction.response.send_message = AsyncMock()
    return interaction


async def test_nur_echte_fehler_zaehlen_als_befehlsfehler(bot: GGBot) -> None:
    tree = bot.tree
    interaction = fake_interaction(bot)

    await tree.on_error(
        interaction,
        app_commands.CommandInvokeError(MagicMock(), UserError("error.cooldown", seconds=1)),
    )
    await tree.on_error(interaction, app_commands.NoPrivateMessage())
    assert (await status.read_counters(bot.services.redis))["command_errors"] == 0

    await tree.on_error(
        interaction,
        app_commands.CommandInvokeError(MagicMock(), RuntimeError("Bug")),
    )
    assert (await status.read_counters(bot.services.redis))["command_errors"] == 1
    interaction.response.send_message.assert_awaited()
