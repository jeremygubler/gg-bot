"""`uvicorn ggbot.api.main:create_app --factory`"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from sqlalchemy import text

from ggbot.core.config import AppConfig, get_config
from ggbot.core.db.engine import Database
from ggbot.core.logging import configure_logging, get_logger, register_secret
from ggbot.core.redis import create_redis

log = get_logger(__name__)


def create_app(config: AppConfig | None = None) -> FastAPI:
    config = config or get_config()
    register_secret(config.discord_token.get_secret_value())
    configure_logging(config.log_level, config.log_format)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.db = Database(config.database_url)
        app.state.redis = create_redis(config.redis_url)
        try:
            yield
        finally:
            await app.state.redis.aclose()
            await app.state.db.dispose()

    app = FastAPI(title="GG-Bot API", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz(response: Response) -> dict[str, str]:
        """200 nur, wenn Datenbank und Redis antworten."""
        status = {"db": "ok", "redis": "ok"}
        try:
            async with app.state.db.session() as session:
                await session.execute(text("SELECT 1"))
        except Exception:
            log.exception("health_db_failed")
            status["db"] = "error"
        try:
            await app.state.redis.ping()
        except Exception:
            log.exception("health_redis_failed")
            status["redis"] = "error"
        if "error" in status.values():
            response.status_code = 503
        return status

    return app
