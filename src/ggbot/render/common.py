"""Gemeinsame Zeichenhilfen für Rank- und Willkommens-Card."""

import io
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

WIDTH, HEIGHT = 934, 282
PANEL_MARGIN = 20
AVATAR_SIZE = 176
AVATAR_X, AVATAR_Y = 48, (HEIGHT - AVATAR_SIZE) // 2
MAX_AVATAR_BYTES = 8 * 1024 * 1024
TEXT_X = 262

Color = tuple[int, int, int]

_FONT_DIRS = (
    Path("/usr/share/fonts/truetype/dejavu"),
    Path("/usr/share/fonts/dejavu"),
    Path("/Library/Fonts"),
)


def rgb(value: int) -> Color:
    return (value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF


def mix(a: Color, b: Color, amount: float) -> Color:
    return (
        round(a[0] + (b[0] - a[0]) * amount),
        round(a[1] + (b[1] - a[1]) * amount),
        round(a[2] + (b[2] - a[2]) * amount),
    )


@lru_cache(maxsize=8)
def font(size: int, bold: bool) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
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


def fit(
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


def background(template: str, base: Color, accent: Color) -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), base)
    draw = ImageDraw.Draw(image)
    if template == "gradient":
        tint = mix(base, accent, 0.45)
        for y in range(HEIGHT):
            draw.line([(0, y), (WIDTH, y)], fill=mix(base, tint, y / HEIGHT))
    elif template == "stripes":
        stripe = mix(base, (255, 255, 255), 0.06)
        for x in range(-HEIGHT, WIDTH, 48):
            draw.polygon(
                [(x, HEIGHT), (x + 24, HEIGHT), (x + 24 + HEIGHT, 0), (x + HEIGHT, 0)], fill=stripe
            )
    elif template == "dots":
        dot = mix(base, accent, 0.4)
        for y in range(14, HEIGHT, 28):
            for x in range(14, WIDTH, 28):
                draw.ellipse([x - 3, y - 3, x + 3, y + 3], fill=dot)
    return image


def circle_avatar(raw: bytes | None, name: str, accent: Color) -> Image.Image:
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
        tile = Image.new("RGB", (size, size), mix(accent, (0, 0, 0), 0.4))
        draw = ImageDraw.Draw(tile)
        initial = ((" ".join(name.split()))[:1] or "?").upper()
        initial_font = font(size // 2, True)
        draw.text(
            (size / 2, size / 2), initial, font=initial_font, fill=(255, 255, 255), anchor="mm"
        )
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, size - 1, size - 1], fill=255)
    ring = Image.new("RGBA", (size + 24 * scale, size + 24 * scale), (0, 0, 0, 0))
    ImageDraw.Draw(ring).ellipse([0, 0, ring.width - 1, ring.height - 1], fill=(*accent, 255))
    ring.paste(tile, (12 * scale, 12 * scale), mask)
    return ring.resize((AVATAR_SIZE + 24, AVATAR_SIZE + 24), Image.Resampling.LANCZOS)
