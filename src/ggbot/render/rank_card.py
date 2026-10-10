"""Die Rank-Card als PNG.

Alle Vorlagen werden im Code gezeichnet, es gibt keine Bilddateien. Die Funktion ist rein und
darum cachebar: gleiche Eingabe → gleiche Bytes.
"""

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
    format_number,
    mix,
    rgb,
)

BAR_X, BAR_Y, BAR_W, BAR_H = 262, 200, 624, 34
NAME_SIZES = (40, 36, 32, 28)


@dataclass(frozen=True)
class RankCardData:
    name: str
    level: int
    into_level: int
    needed: int
    total_xp: int
    position: int
    avatar: bytes | None
    accent: int
    background: int
    template: str
    rank_label: str
    level_label: str


def header_left(data: RankCardData) -> float:
    """Linke Kante des Zahlenblocks (Rang/Level). Der Name muss davor enden; auch für Tests."""
    draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    big, label = font(46, True), font(20, False)
    right = WIDTH - 52
    level_w = draw.textlength(str(data.level), font=big)
    rank_w = draw.textlength(f"#{data.position}", font=big)
    level_label_w = draw.textlength(data.level_label, font=label)
    rank_label_w = draw.textlength(data.rank_label, font=label)
    return right - level_w - level_label_w - 40 - rank_w - rank_label_w - 8


@lru_cache(maxsize=64)
def render_rank_card(data: RankCardData) -> bytes:
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
    white, muted = (255, 255, 255), (200, 205, 212)

    name_font, big, label, small = (
        font(40, True),
        font(46, True),
        font(20, False),
        font(24, False),
    )

    # Erst der Zahlenblock rechts oben (Rang, Level): seine Breite bestimmt den Platz für den Namen.
    right = WIDTH - 52
    level_text, rank_text = str(data.level), f"#{data.position}"
    level_w = draw.textlength(level_text, font=big)
    rank_w = draw.textlength(rank_text, font=big)
    level_label_w = draw.textlength(data.level_label, font=label)
    rank_label_w = draw.textlength(data.rank_label, font=label)
    rank_right = right - level_w - level_label_w - 40
    block_left = rank_right - rank_w - rank_label_w - 8
    draw.text((right - level_w, 40), level_text, font=big, fill=accent)
    draw.text((right - level_w - level_label_w - 8, 62), data.level_label, font=label, fill=muted)
    draw.text((rank_right - rank_w, 40), rank_text, font=big, fill=white)
    draw.text((block_left, 62), data.rank_label, font=label, fill=muted)

    name = " ".join(data.name.split())  # Zeilenumbrüche und Mehrfach-Leerzeichen glätten
    name_width = max(80, int(block_left) - 24 - TEXT_X)
    # Erst kleiner schreiben, dann erst kürzen: so bleibt möglichst viel vom Namen lesbar.
    for size in NAME_SIZES:
        name_font = font(size, True)
        if draw.textlength(name, font=name_font) <= name_width:
            break
    draw.text((TEXT_X, 58), fit(draw, name, name_font, name_width), font=name_font, fill=white)

    xp_text = f"{format_number(data.into_level)} / {format_number(data.needed)} XP"
    draw.text((BAR_X + BAR_W, BAR_Y - 14), xp_text, font=small, fill=muted, anchor="rs")

    track = mix(base, (0, 0, 0), 0.55)
    draw.rounded_rectangle(
        [BAR_X, BAR_Y, BAR_X + BAR_W, BAR_Y + BAR_H], radius=BAR_H // 2, fill=track
    )
    fraction = 0.0 if data.needed <= 0 else max(0.0, min(1.0, data.into_level / data.needed))
    fill_w = round(BAR_W * fraction)
    if fill_w > 0:
        # Mindestbreite, damit auch ein kleiner Fortschritt als runde Kappe sichtbar ist.
        fill_w = max(fill_w, BAR_H)
        draw.rounded_rectangle(
            [BAR_X, BAR_Y, BAR_X + fill_w, BAR_Y + BAR_H], radius=BAR_H // 2, fill=accent
        )

    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PNG", optimize=False)
    return buffer.getvalue()
