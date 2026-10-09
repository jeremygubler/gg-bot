"""Docker-Healthcheck: Der Bot schreibt alle 10 s ein Lebenszeichen nach Redis."""

import asyncio
import sys

from ggbot.bot.client import HEARTBEAT_KEY
from ggbot.core.config import get_config
from ggbot.core.redis import create_redis


async def check() -> bool:
    redis = create_redis(get_config().redis_url)
    try:
        return bool(await redis.exists(HEARTBEAT_KEY))
    finally:
        await redis.aclose()


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(check()) else 1)
