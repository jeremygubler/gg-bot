"""Öffentliche Rangliste ohne Anmeldung.

Nur für Server, die sie im Dashboard ausdrücklich freigegeben haben. Ein Server ohne Freigabe und
ein Server, den es nicht gibt, sehen für Besucher gleich aus (404).
"""

import ipaddress
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, field_validator

from ggbot.api.access import api_error
from ggbot.api.ids import Id
from ggbot.api.ratelimit import hit
from ggbot.core.publicboard import load_board
from ggbot.core.settings.leveling import LevelingSettings
from ggbot.core.settings.store import SettingsStore

router = APIRouter()

PAGE_SIZE = 25
REQUESTS_PER_MINUTE = 60


AVATAR_PREFIX = "https://cdn.discordapp.com/"


class PublicRow(BaseModel):
    position: int
    name: str | None
    avatar_url: str | None
    level: int
    xp: int

    @field_validator("avatar_url")
    @classmethod
    def _only_discord_cdn(cls, value: str | None) -> str | None:
        """Der Zwischenspeicher stammt vom Bot, aber ins Bild gehört nur, was von Discords CDN
        kommt: sonst könnte ein manipulierter Eintrag Besucher auf fremde Server schauen lassen."""
        return value if value is not None and value.startswith(AVATAR_PREFIX) else None


class PublicLeaderboard(BaseModel):
    guild_name: str
    page: int
    pages: int
    rows: list[PublicRow]


def client_key(request: Request, hops: int) -> str:
    """Wer fragt? Jeder vertrauenswürdige Proxy hängt die Adresse, die er gesehen hat, ans Ende
    von X-Forwarded-For. Von rechts gezählt sind `hops` Einträge echt; was davor steht, stammt vom
    Client und ist frei erfunden. Ohne Proxy-Kopf zählt die Verbindung selbst."""
    entries = [
        e.strip() for e in request.headers.get("x-forwarded-for", "").split(",") if e.strip()
    ]
    if entries:
        candidate = entries[max(len(entries) - hops, 0)]
        try:
            # Keine beliebigen Zeichenketten als Redis-Schlüssel: nur echte Adressen zählen.
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            pass
    return request.client.host if request.client else "unknown"


@router.get("/public/{guild_id}/leaderboard")
async def leaderboard(
    guild_id: Id,
    request: Request,
    response: Response,
    page: Annotated[int, Query(ge=1, le=1000)] = 1,
) -> PublicLeaderboard:
    await hit(
        request.app.state.redis,
        "public_board",
        client_key(request, request.app.state.config.proxy_hops),
        limit=REQUESTS_PER_MINUTE,
        window=60,
    )
    store: SettingsStore = request.app.state.settings
    settings = await store.get(guild_id, LevelingSettings)
    if not (settings.enabled and settings.public_leaderboard):
        raise api_error(404, "not_found")
    board = await load_board(request.app.state.redis, guild_id)
    if board is None:
        # Freigegeben, aber der Bot hat die Liste noch nicht gebaut (oder ist aus).
        raise api_error(503, "board_unavailable")
    pages = max(1, -(-len(board.rows) // PAGE_SIZE))
    page = min(page, pages)
    start = (page - 1) * PAGE_SIZE
    response.headers["Cache-Control"] = "public, max-age=60"
    return PublicLeaderboard(
        guild_name=board.guild_name,
        page=page,
        pages=pages,
        rows=[PublicRow(**row.model_dump()) for row in board.rows[start : start + PAGE_SIZE]],
    )
