"""Der Bot lädt alle seine Cogs über `load_extension`, so wie beim echten Start."""

import pytest
from redis.asyncio import Redis

from ggbot.bot.client import COGS, GGBot
from ggbot.bot.services import Services
from ggbot.core.config import AppConfig
from ggbot.core.db.engine import Database


@pytest.fixture
async def bot(db: Database, redis: Redis) -> GGBot:
    return GGBot(Services.build(AppConfig(), db, redis))


@pytest.mark.parametrize("extension", COGS)
async def test_jede_erweiterung_laesst_sich_laden(bot: GGBot, extension: str) -> None:
    """Die Tests setzen Cogs sonst von Hand zusammen; ohne `setup()` bräche erst der echte Start."""
    async with bot:
        await bot.load_extension(extension)
        assert extension in bot.extensions


async def test_alle_erweiterungen_zusammen_ergeben_die_erwarteten_befehle(bot: GGBot) -> None:
    async with bot:
        for extension in COGS:
            await bot.load_extension(extension)
        names = {command.name for command in bot.tree.get_commands()}
    assert {"suggest", "suggestion", "sticky", "voice", "tempvoice", "verification"} <= names
