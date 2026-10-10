"""Discord-Login, Abmeldung und „wer bin ich"."""

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from ggbot.api.access import (
    Authed,
    Csrf,
    api_error,
    can_manage,
    current_session,
    current_user,
)
from ggbot.api.discord import DiscordError, DiscordOAuth
from ggbot.api.sessions import (
    COOKIE_NAME,
    STATE_COOKIE,
    Session,
    SessionStore,
    sign_value,
    verify_value,
)
from ggbot.core.config import AppConfig
from ggbot.core.logging import get_logger
from ggbot.core.snapshot import snapshot_exists

log = get_logger(__name__)
router = APIRouter()

STATE_MAX_AGE = 600


class MeGuild(BaseModel):
    # Snowflakes als String: JavaScript würde 64-Bit-Zahlen verfälschen.
    id: str
    name: str
    icon: str | None
    owner: bool
    bot_present: bool


class Me(BaseModel):
    id: str
    username: str
    avatar: str | None
    guilds: list[MeGuild]


def _cookie_args(config: AppConfig, max_age: int) -> dict[str, object]:
    return {
        "max_age": max_age,
        "httponly": True,
        "samesite": "lax",
        "secure": config.cookie_secure,
        "path": "/",
    }


@router.get("/auth/login")
async def login(request: Request) -> Response:
    config: AppConfig = request.app.state.config
    if request.app.state.sessions is None:
        raise api_error(503, "dashboard_disabled")
    oauth: DiscordOAuth = request.app.state.oauth
    state = secrets.token_urlsafe(24)
    response = RedirectResponse(oauth.authorize_url(state), status_code=302)
    # Der State liegt im Cookie, nicht in Redis: er bindet den Rücksprung an genau diesen
    # Browser. Ein untergeschobener Code (Login-CSRF) trägt den State des Angreifers nicht.
    response.set_cookie(
        STATE_COOKIE,
        sign_value(config.session_secret.get_secret_value(), state),
        **_cookie_args(config, STATE_MAX_AGE),  # type: ignore[arg-type]
    )
    return response


@router.get("/auth/callback")
async def callback(request: Request, code: str | None = None, state: str | None = None) -> Response:
    config: AppConfig = request.app.state.config
    store: SessionStore | None = request.app.state.sessions
    if store is None:
        raise api_error(503, "dashboard_disabled")
    cookie = request.cookies.get(STATE_COOKIE)
    expected = verify_value(config.session_secret.get_secret_value(), cookie) if cookie else None
    if not code or not state or expected is None or not secrets.compare_digest(state, expected):
        # Auch wenn der Nutzer bei Discord abgebrochen hat (`error=access_denied`).
        raise api_error(400, "oauth_state")

    oauth: DiscordOAuth = request.app.state.oauth
    try:
        token = await oauth.exchange(code)
        user = await oauth.user(token.access_token)
        guilds = await oauth.guilds(token.access_token)
    except DiscordError as exc:
        log.warning("oauth_login_failed", status=exc.status)
        raise api_error(502, "discord_unavailable") from exc

    session = Session(
        user_id=user.id,
        username=user.global_name or user.username,
        avatar=user.avatar,
        access_token=token.access_token,
        guilds=guilds,
        guilds_at=request.app.state.clock(),
    )
    cookie_value = await store.create(session)
    log.info("dashboard_login", user_id=user.id)
    response = RedirectResponse(config.dashboard_url.rstrip("/") + "/", status_code=302)
    response.set_cookie(COOKIE_NAME, cookie_value, **_cookie_args(config, 7 * 24 * 3600))  # type: ignore[arg-type]
    response.delete_cookie(STATE_COOKIE, path="/")
    return response


@router.post("/auth/logout", status_code=204, dependencies=[Csrf])
async def logout(request: Request, auth: Annotated[Authed, Depends(current_session)]) -> Response:
    await request.app.state.sessions.delete(auth.sid)
    try:
        await request.app.state.oauth.revoke(auth.session.access_token)
    except DiscordError as exc:
        # Die Sitzung ist weg; das Token läuft bei Discord von selbst ab.
        log.warning("token_revoke_failed", status=exc.status)
    response = Response(status_code=204)
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


@router.get("/me")
async def me(request: Request, auth: Annotated[Authed, Depends(current_user)]) -> Me:
    redis = request.app.state.redis
    guilds = [
        MeGuild(
            id=str(g.id),
            name=g.name,
            icon=g.icon,
            owner=g.owner,
            bot_present=await snapshot_exists(redis, g.id),
        )
        for g in auth.session.guilds
        if can_manage(g)
    ]
    return Me(
        id=str(auth.session.user_id),
        username=auth.session.username,
        avatar=auth.session.avatar,
        guilds=guilds,
    )
