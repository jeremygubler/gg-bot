"""Anmeldung und Zugriffsprüfung als FastAPI-Abhängigkeiten."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from ggbot.api.discord import DiscordError, DiscordOAuth, UserGuild
from ggbot.api.sessions import COOKIE_NAME, Session, SessionStore
from ggbot.core.logging import get_logger
from ggbot.core.snapshot import GuildSnapshot, load_snapshot

log = get_logger(__name__)

ADMINISTRATOR = 0x8
MANAGE_GUILD = 0x20
GUILD_REFRESH_SECONDS = 60
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "ggbot"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def api_error(status: int, code: str) -> HTTPException:
    """Fehler als Schlüssel; Text und Übersetzung gehören der Oberfläche."""
    return HTTPException(status_code=status, detail={"error": code})


def can_manage(guild: UserGuild) -> bool:
    return guild.owner or bool(guild.permissions & (ADMINISTRATOR | MANAGE_GUILD))


def is_administrator(guild: UserGuild) -> bool:
    return guild.owner or bool(guild.permissions & ADMINISTRATOR)


@dataclass(frozen=True)
class Authed:
    sid: str
    session: Session


@dataclass(frozen=True)
class GuildAccess:
    sid: str
    session: Session
    guild: UserGuild
    snapshot: GuildSnapshot


async def require_csrf(request: Request) -> None:
    """Zustandsändernde Anfragen brauchen einen eigenen Header.

    Ein fremdes Formular oder `<img>` kann ihn nicht setzen, `fetch` von einer anderen
    Origin löst dafür einen CORS-Preflight aus, den die API nicht freigibt. Zusammen
    mit SameSite=Lax genügt das ohne Token-Verwaltung.
    """
    if request.method not in SAFE_METHODS and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        raise api_error(403, "csrf")


Csrf = Depends(require_csrf)


async def current_session(request: Request) -> Authed:
    store: SessionStore | None = request.app.state.sessions
    if store is None:
        raise api_error(503, "dashboard_disabled")
    loaded = await store.load(request.cookies.get(COOKIE_NAME))
    if loaded is None:
        raise api_error(401, "unauthenticated")
    return Authed(*loaded)


async def _refresh_guilds(request: Request, auth: Authed) -> Session:
    clock: Callable[[], float] = request.app.state.clock
    if clock() - auth.session.guilds_at < GUILD_REFRESH_SECONDS:
        return auth.session
    oauth: DiscordOAuth = request.app.state.oauth
    try:
        guilds = await oauth.guilds(auth.session.access_token)
    except DiscordError as exc:
        if exc.status == 401:
            # Token widerrufen oder abgelaufen: Sitzung ist wertlos.
            await request.app.state.sessions.delete(auth.sid)
            raise api_error(401, "unauthenticated") from exc
        # Lieber ablehnen als mit einer womöglich veralteten Rechteliste arbeiten.
        log.warning("guild_refresh_failed", status=exc.status)
        raise api_error(503, "discord_unavailable") from exc
    session = auth.session.model_copy(update={"guilds": guilds, "guilds_at": clock()})
    await request.app.state.sessions.save(auth.sid, session)
    return session


async def current_user(
    request: Request, auth: Annotated[Authed, Depends(current_session)]
) -> Authed:
    """Angemeldet und mit höchstens 60 s alter Server-Liste."""
    return Authed(auth.sid, await _refresh_guilds(request, auth))


async def guild_access(
    guild_id: int, request: Request, auth: Annotated[Authed, Depends(current_user)]
) -> GuildAccess:
    """Nutzer darf den Server verwalten UND der Bot ist dort.

    Beides führt zu 404 statt 403: wer den Server nicht verwalten darf, erfährt nicht,
    ob der Bot dort ist.
    """
    guild = next((g for g in auth.session.guilds if g.id == guild_id), None)
    if guild is None or not can_manage(guild):
        raise api_error(404, "guild_not_found")
    snapshot = await load_snapshot(request.app.state.redis, guild_id)
    if snapshot is None:
        raise api_error(404, "guild_not_found")
    return GuildAccess(auth.sid, auth.session, guild, snapshot)
