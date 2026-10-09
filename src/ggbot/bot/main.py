"""Einstiegspunkt: `python -m ggbot.bot.main`."""

import asyncio

from ggbot.bot.client import GGBot
from ggbot.bot.services import Services
from ggbot.core.config import get_config
from ggbot.core.db.engine import Database
from ggbot.core.logging import configure_logging, get_logger, register_secret
from ggbot.core.redis import create_redis


async def run() -> None:
    config = get_config()
    token = config.require_token()
    register_secret(token)
    configure_logging(config.log_level, config.log_format)
    log = get_logger("ggbot.bot")

    db = Database(config.database_url)
    redis = create_redis(config.redis_url)
    bot = GGBot(Services.build(config, db, redis))
    try:
        log.info("bot_starting")
        async with bot:
            await bot.start(token)
    finally:
        await redis.aclose()
        await db.dispose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
