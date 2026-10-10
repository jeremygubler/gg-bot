"""`uvicorn ggbot.api.main:create_app --factory`"""

import hmac
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Response
from sqlalchemy import text

from ggbot import __version__
from ggbot.api import errors, security
from ggbot.api.access import api_error
from ggbot.api.discord import DiscordOAuth
from ggbot.api.metrics import collect
from ggbot.api.routers import auth, guilds, panels, preview, public
from ggbot.api.sessions import SessionStore
from ggbot.core.config import AppConfig, get_config
from ggbot.core.db.engine import Database
from ggbot.core.logging import configure_logging, get_logger, register_secret
from ggbot.core.redis import create_redis
from ggbot.core.services.rolepanels import RolePanelService
from ggbot.core.services.tickets import TicketService
from ggbot.core.settings.store import SettingsStore
from ggbot.core.status import read_status

log = get_logger(__name__)


def create_app(
    config: AppConfig | None = None, *, transport: httpx.AsyncBaseTransport | None = None
) -> FastAPI:
    """`transport` ersetzt den Netzzugang zu Discord (Tests)."""
    config = config or get_config()
    register_secret(config.discord_token.get_secret_value())
    register_secret(config.discord_client_secret.get_secret_value())
    register_secret(config.session_secret.get_secret_value())
    register_secret(config.metrics_token.get_secret_value())
    configure_logging(config.log_level, config.log_format)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.db = Database(config.database_url)
        app.state.redis = create_redis(config.redis_url)
        app.state.http = httpx.AsyncClient(timeout=10, transport=transport)
        app.state.config = config
        app.state.clock = time.time
        app.state.settings = SettingsStore(app.state.db, app.state.redis)
        app.state.rolepanels = RolePanelService(app.state.db)
        app.state.tickets = TicketService(app.state.db, app.state.settings, app.state.redis)
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
    app.include_router(panels.router)
    app.include_router(preview.router)
    app.include_router(public.router)
    errors.install(app, config.default_locale)
    security.install(app)

    @app.get("/metrics", include_in_schema=False)
    async def metrics(request: Request) -> Response:
        """Prometheus. Nur mit `METRICS_TOKEN`; ohne gesetztes Token gibt es den Endpunkt nicht."""
        expected = config.metrics_token.get_secret_value()
        if not expected:
            raise api_error(404, "not_found")
        given = request.headers.get("authorization", "")
        if not hmac.compare_digest(given.encode(), f"Bearer {expected}".encode()):
            raise api_error(401, "unauthenticated")
        body = await collect(app.state.db, app.state.redis)
        return Response(body, media_type="text/plain; version=0.0.4; charset=utf-8")

    @app.get("/healthz")
    async def healthz(response: Response) -> dict[str, str]:
        """200 nur, wenn Datenbank und Redis antworten. Der Bot zählt nicht dazu: fällt er aus,
        soll das Dashboard (Einstellungen, Panels) trotzdem erreichbar bleiben."""
        status = {"db": "ok", "redis": "ok", "version": __version__}
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
        if "error" in (status["db"], status["redis"]):
            response.status_code = 503
        else:
            try:
                status["bot"] = "ok" if await read_status(app.state.redis) is not None else "down"
            except Exception:
                status["bot"] = "unknown"
        return status

    return app
