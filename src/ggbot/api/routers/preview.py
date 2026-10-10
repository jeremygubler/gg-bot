"""Kartenvorschau: dieselbe Zeichnung wie im Bot (`ggbot.render`), mit Beispieldaten.

Die Vorschau zeigt das Aussehen, nicht echte Mitglieder: Name und Zahlen sind Platzhalter und es
wird kein Avatar von Discord geladen (der Bot zeichnet in dem Fall den Platzhalter-Kreis).
"""

import asyncio
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response

from ggbot.api.access import Csrf, GuildAccess, guild_access
from ggbot.api.errors import request_locale
from ggbot.api.ratelimit import hit
from ggbot.core.i18n import translator
from ggbot.core.settings.card import CardStyle
from ggbot.render.rank_card import RankCardData, render_rank_card
from ggbot.render.welcome_card import WelcomeCardData, render_welcome_card

router = APIRouter()

PREVIEWS_PER_MINUTE = 60
SAMPLE_NAME = {"de": "Beispiel", "en": "Example"}


@router.post("/guilds/{guild_id}/preview/{card}", dependencies=[Csrf])
async def preview(
    card: Literal["rank", "welcome"],
    style: CardStyle,
    request: Request,
    access: Annotated[GuildAccess, Depends(guild_access)],
) -> Response:
    await hit(
        request.app.state.redis,
        "preview",
        access.session.user_id,
        limit=PREVIEWS_PER_MINUTE,
        window=60,
    )
    locale = request_locale(request, request.app.state.config.default_locale)
    t = translator.t
    name = SAMPLE_NAME[locale]
    if card == "rank":
        rank = RankCardData(
            name=name,
            level=12,
            into_level=340,
            needed=1000,
            total_xp=8420,
            position=3,
            avatar=None,
            accent=style.accent,
            background=style.background,
            template=style.template,
            rank_label=t(locale, "leveling.card.rank"),
            level_label=t(locale, "leveling.card.level"),
        )
        png = await asyncio.to_thread(render_rank_card, rank)
    else:
        welcome = WelcomeCardData(
            title=t(locale, "welcome.card.title"),
            name=name,
            subtitle=t(locale, "welcome.card.member", count=1234),
            avatar=None,
            accent=style.accent,
            background=style.background,
            template=style.template,
        )
        png = await asyncio.to_thread(render_welcome_card, welcome)
    # Die Bilder hängen nur vom Stil ab; der Browser darf sie kurz behalten.
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=60"})
