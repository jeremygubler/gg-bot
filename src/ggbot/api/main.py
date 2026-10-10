"""`uvicorn ggbot.api.main:create_app --factory`"""

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Response
from sqlalchemy import text

from ggbot.api.discord import DiscordOAuth
from ggbot.api.routers import auth, guilds
from ggbot.api.sessions import SessionStore
from ggbot.core.config import AppConfig, get_config
from ggbot.core.db.engine import Database
from ggbot.core.logging import configure_logging, get_logger, register_secret
from ggbot.core.redis import create_redis
from ggbot.core.settings.store import SettingsStore

log = get_logger(__name__)


def create_app(
    config: AppConfig | None = None, *, transport: httpx.AsyncBaseTransport | None = None
) -> FastAPI:
    """`transport` ersetzt den Netzzugang zu Discord (Tests)."""
    config = config or get_config()
    register_secret(config.discord_token.get_secret_value())
    register_secret(config.discord_client_secret.get_secret_value())
    register_secret(config.session_secret.get_secret_value())
    configure_logging(config.log_level, config.log_format)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.db = Database(config.database_url)
        app.state.redis = create_redis(config.redis_url)
        app.state.http = httpx.AsyncClient(timeout=10, transport=transport)
        app.state.config = config
        app.state.clock = time.time
        app.state.settings = SettingsStore(app.state.db, app.state.redis)
        app.state.oauth = DiscordOAuth(app.state.http, config)
        app.state.sessions = (
            SessionStore(app.state.redis, config.session_secret.get_secret_value())
            if config.dashboard_enabled
            else None
        )
        if app.state.sessions is None:
            log.warning("dashboard_login_disabled")
        try:
            yield
        finally:
            await app.state.http.aclose()
            await app.state.redis.aclose()
            await app.state.db.dispose()

    app = FastAPI(title="GG-Bot API", lifespan=lifespan)

    app.include_router(auth.router)
    app.include_router(guilds.router)

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
