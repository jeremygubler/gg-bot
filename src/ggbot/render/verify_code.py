"""Bild mit dem Code der Verifizierung.

Kein Schutz gegen gezielte Bilderkennung, sondern gegen Skripte, die nur Text auslesen: ein
Mensch liest es in Sekunden, `message.content` enthält es nie. Der Zufall (Lage, Neigung,
Störlinien) kommt als Startwert von aussen, damit die Zeichnung im Test reproduzierbar ist.
"""

import io
import random

from PIL import Image, ImageDraw

from ggbot.render.common import font

WIDTH, HEIGHT = 260, 96
BACKGROUND = (30, 33, 40)
INK = (235, 238, 245)
NOISE = (95, 102, 120)


def render_code(code: str, seed: int) -> bytes:
    rng = random.Random(seed)  # noqa: S311  # Zeichnung, kein Sicherheitszufall: der Code selbst kommt von secrets
    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)
    for _ in range(7):  # Störlinien hinter den Zeichen
        draw.line(
            [
                (rng.randrange(WIDTH), rng.randrange(HEIGHT)),
                (rng.randrange(WIDTH), rng.randrange(HEIGHT)),
            ],
            fill=NOISE,
            width=rng.choice((1, 2)),
        )
    face = font(52, True)
    step = (WIDTH - 40) / max(len(code), 1)
    for index, char in enumerate(code):
        tile = Image.new("RGBA", (64, 72), (0, 0, 0, 0))
        ImageDraw.Draw(tile).text((8, 4), char, font=face, fill=(*INK, 255))
        tile = tile.rotate(rng.uniform(-18, 18), resample=Image.Resampling.BICUBIC, expand=False)
        image.paste(tile, (int(20 + index * step - 6), rng.randrange(2, 22)), tile)
    for _ in range(90):  # Punkte darüber
        x, y = rng.randrange(WIDTH), rng.randrange(HEIGHT)
        draw.point((x, y), fill=NOISE)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
