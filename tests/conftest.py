import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from redis.asyncio import Redis
from sqlalchemy import text

from ggbot.core.db.engine import Database
from ggbot.core.redis import create_redis

TEST_DB_URL = os.environ.get(
    "GGBOT_TEST_DATABASE_URL", "postgresql+asyncpg://ggbot:ggbot@localhost:5432/ggbot_test"
)
TEST_REDIS_URL = os.environ.get("GGBOT_TEST_REDIS_URL", "redis://localhost:6379/15")
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def migrated() -> None:
    """Baut das Schema über die echten Migrationen auf, so testet jeder Lauf sie mit."""
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", TEST_DB_URL)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


@pytest.fixture
async def db(migrated: None) -> AsyncIterator[Database]:
    database = Database(TEST_DB_URL, pool=False)
    async with database.session() as session:
        await session.execute(
            text(
                "TRUNCATE guilds, guild_settings, dashboard_audit, scheduled_jobs RESTART IDENTITY"
            )
        )
    yield database
    await database.dispose()


@pytest.fixture
async def redis() -> AsyncIterator[Redis]:
    client = create_redis(TEST_REDIS_URL)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()
