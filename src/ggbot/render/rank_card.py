"""Die Rank-Card als PNG.

Alle Vorlagen werden im Code gezeichnet, es gibt keine Bilddateien. Die Funktion ist rein und
darum cachebar: gleiche Eingabe → gleiche Bytes.
"""

import io
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

WIDTH, HEIGHT = 934, 282
PANEL_MARGIN = 20
AVATAR_SIZE = 176
AVATAR_X, AVATAR_Y = 48, (HEIGHT - AVATAR_SIZE) // 2
BAR_X, BAR_Y, BAR_W, BAR_H = 262, 200, 624, 34
MAX_AVATAR_BYTES = 8 * 1024 * 1024
TEXT_X = 262
NAME_SIZES = (40, 36, 32, 28)

Color = tuple[int, int, int]

_FONT_DIRS = (
    Path("/usr/share/fonts/truetype/dejavu"),
    Path("/usr/share/fonts/dejavu"),
    Path("/Library/Fonts"),
)


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


def _rgb(value: int) -> Color:
    return (value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF


def _mix(a: Color, b: Color, amount: float) -> Color:
    return (
        round(a[0] + (b[0] - a[0]) * amount),
        round(a[1] + (b[1] - a[1]) * amount),
        round(a[2] + (b[2] - a[2]) * amount),
    )


@lru_cache(maxsize=8)
def _font(size: int, bold: bool) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """DejaVu, wenn vorhanden (Umlaute, viele Sprachen), sonst Pillows eingebaute Schrift."""
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    for directory in _FONT_DIRS:
        candidate = directory / name
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default(size=size)


def format_number(value: int) -> str:
    """12345 → "12 345": neutral für Deutsch und Englisch."""
    return f"{value:,}".replace(",", " ")


def _fit(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    width: int,
) -> str:
    """Kürzt mit "…", bis der Text in `width` passt."""
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text.rstrip() + "…"


def _background(template: str, base: Color, accent: Color) -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), base)
    draw = ImageDraw.Draw(image)
    if template == "gradient":
        tint = _mix(base, accent, 0.45)
        for y in range(HEIGHT):
            draw.line([(0, y), (WIDTH, y)], fill=_mix(base, tint, y / HEIGHT))
    elif template == "stripes":
        stripe = _mix(base, (255, 255, 255), 0.06)
        for x in range(-HEIGHT, WIDTH, 48):
            draw.polygon(
                [(x, HEIGHT), (x + 24, HEIGHT), (x + 24 + HEIGHT, 0), (x + HEIGHT, 0)], fill=stripe
            )
    elif template == "dots":
        dot = _mix(base, accent, 0.4)
        for y in range(14, HEIGHT, 28):
            for x in range(14, WIDTH, 28):
                draw.ellipse([x - 3, y - 3, x + 3, y + 3], fill=dot)
    return image


def _circle_avatar(raw: bytes | None, name: str, accent: Color) -> Image.Image:
    """Runder Avatar mit glattem Rand; fehlt er oder ist er kaputt, ein Platzhalter mit Initial."""
    scale = 4  # Zeichnen in vierfacher Grösse und verkleinern: glatte Kanten
    size = AVATAR_SIZE * scale
    tile: Image.Image | None = None
    if raw is not None and len(raw) <= MAX_AVATAR_BYTES:
        try:
            tile = (
                Image.open(io.BytesIO(raw))
                .convert("RGB")
                .resize((size, size), Image.Resampling.LANCZOS)
            )
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
            tile = None
    if tile is None:
        tile = Image.new("RGB", (size, size), _mix(accent, (0, 0, 0), 0.4))
        draw = ImageDraw.Draw(tile)
        initial = ((" ".join(name.split()))[:1] or "?").upper()
        font = _font(size // 2, True)
        draw.text((size / 2, size / 2), initial, font=font, fill=(255, 255, 255), anchor="mm")
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, size - 1, size - 1], fill=255)
    ring = Image.new("RGBA", (size + 24 * scale, size + 24 * scale), (0, 0, 0, 0))
    ImageDraw.Draw(ring).ellipse([0, 0, ring.width - 1, ring.height - 1], fill=(*accent, 255))
    ring.paste(tile, (12 * scale, 12 * scale), mask)
    return ring.resize((AVATAR_SIZE + 24, AVATAR_SIZE + 24), Image.Resampling.LANCZOS)


def header_left(data: RankCardData) -> float:
    """Linke Kante des Zahlenblocks (Rang/Level). Der Name muss davor enden; auch für Tests."""
    draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    big, label = _font(46, True), _font(20, False)
    right = WIDTH - 52
    level_w = draw.textlength(str(data.level), font=big)
    rank_w = draw.textlength(f"#{data.position}", font=big)
    level_label_w = draw.textlength(data.level_label, font=label)
    rank_label_w = draw.textlength(data.rank_label, font=label)
    return right - level_w - level_label_w - 40 - rank_w - rank_label_w - 8


@lru_cache(maxsize=64)
def render_rank_card(data: RankCardData) -> bytes:
    accent, base = _rgb(data.accent), _rgb(data.background)
    image = _background(data.template, base, accent).convert("RGBA")

    panel = Image.new("RGBA", image.size, (0, 0, 0, 0))
    ImageDraw.Draw(panel).rounded_rectangle(
        [PANEL_MARGIN, PANEL_MARGIN, WIDTH - PANEL_MARGIN, HEIGHT - PANEL_MARGIN],
        radius=28,
        fill=(0, 0, 0, 120),
    )
    image = Image.alpha_composite(image, panel)

    avatar = _circle_avatar(data.avatar, data.name, accent)
    image.paste(avatar, (AVATAR_X - 12, AVATAR_Y - 12), avatar)

    draw = ImageDraw.Draw(image)
    white, muted = (255, 255, 255), (200, 205, 212)

    name_font, big, label, small = (
        _font(40, True),
        _font(46, True),
        _font(20, False),
        _font(24, False),
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
        name_font = _font(size, True)
        if draw.textlength(name, font=name_font) <= name_width:
            break
    draw.text((TEXT_X, 58), _fit(draw, name, name_font, name_width), font=name_font, fill=white)

    xp_text = f"{format_number(data.into_level)} / {format_number(data.needed)} XP"
    draw.text((BAR_X + BAR_W, BAR_Y - 14), xp_text, font=small, fill=muted, anchor="rs")

    track = _mix(base, (0, 0, 0), 0.55)
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
