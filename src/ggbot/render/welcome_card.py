"""Die Willkommens- bzw. Abschieds-Card als PNG. Reine Funktion, darum cachebar."""

import io
from dataclasses import dataclass
from functools import lru_cache

from PIL import Image, ImageDraw

from ggbot.render.common import (
    AVATAR_X,
    AVATAR_Y,
    HEIGHT,
    PANEL_MARGIN,
    TEXT_X,
    WIDTH,
    background,
    circle_avatar,
    fit,
    font,
    rgb,
)

NAME_SIZES = (56, 48, 42, 36, 30)
TEXT_RIGHT = WIDTH - 52


@dataclass(frozen=True)
class WelcomeCardData:
    title: str  # z. B. "WILLKOMMEN"
    name: str
    subtitle: str  # z. B. "Mitglied #1234"
    avatar: bytes | None
    accent: int
    background: int
    template: str


@lru_cache(maxsize=64)
def render_welcome_card(data: WelcomeCardData) -> bytes:
    accent, base = rgb(data.accent), rgb(data.background)
    image = background(data.template, base, accent).convert("RGBA")

    panel = Image.new("RGBA", image.size, (0, 0, 0, 0))
    ImageDraw.Draw(panel).rounded_rectangle(
        [PANEL_MARGIN, PANEL_MARGIN, WIDTH - PANEL_MARGIN, HEIGHT - PANEL_MARGIN],
        radius=28,
        fill=(0, 0, 0, 120),
    )
    image = Image.alpha_composite(image, panel)

    avatar = circle_avatar(data.avatar, data.name, accent)
    image.paste(avatar, (AVATAR_X - 12, AVATAR_Y - 12), avatar)

    draw = ImageDraw.Draw(image)
    width = TEXT_RIGHT - TEXT_X
    title_font, sub_font = font(30, True), font(28, False)
    draw.text(
        (TEXT_X, 50),
        fit(draw, " ".join(data.title.split()), title_font, width),
        font=title_font,
        fill=accent,
    )

    name = " ".join(data.name.split())  # Zeilenumbrüche und Mehrfach-Leerzeichen glätten
    for size in NAME_SIZES:
        name_font = font(size, True)
        if draw.textlength(name, font=name_font) <= width:
            break
    draw.text(
        (TEXT_X, 100), fit(draw, name, name_font, width), font=name_font, fill=(255, 255, 255)
    )

    subtitle = " ".join(data.subtitle.split())
    draw.text(
        (TEXT_X, 196), fit(draw, subtitle, sub_font, width), font=sub_font, fill=(200, 205, 212)
    )

    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PNG", optimize=False)
    return buffer.getvalue()
