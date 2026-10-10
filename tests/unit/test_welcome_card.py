import io

import pytest
from PIL import Image

from ggbot.render.common import HEIGHT, WIDTH
from ggbot.render.welcome_card import WelcomeCardData, render_welcome_card


def card(**overrides: object) -> WelcomeCardData:
    data: dict[str, object] = {
        "title": "WILLKOMMEN",
        "name": "Alice",
        "subtitle": "Mitglied #1234",
        "avatar": None,
        "accent": 0x3BA55D,
        "background": 0x23272A,
        "template": "gradient",
    }
    data.update(overrides)
    return WelcomeCardData(**data)  # type: ignore[arg-type]


def decode(png: bytes) -> Image.Image:
    return Image.open(io.BytesIO(png)).convert("RGB")


def avatar_png(color: tuple[int, int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), color).save(buffer, format="PNG")
    return buffer.getvalue()


def test_png_in_der_erwarteten_groesse() -> None:
    png = render_welcome_card(card())
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert decode(png).size == (WIDTH, HEIGHT)


@pytest.mark.parametrize("template", ["solid", "gradient", "stripes", "dots"])
def test_alle_vorlagen(template: str) -> None:
    assert decode(render_welcome_card(card(template=template))).size == (WIDTH, HEIGHT)
    images = {
        t: render_welcome_card(card(template=t)) for t in ("solid", "gradient", "stripes", "dots")
    }
    assert len(set(images.values())) == 4


def test_deterministisch_und_eingabeabhaengig() -> None:
    assert render_welcome_card(card()) == render_welcome_card(card())
    base = render_welcome_card(card())
    for change in ({"name": "Bob"}, {"title": "AUF WIEDERSEHEN"}, {"subtitle": "x"}, {"accent": 1}):
        assert render_welcome_card(card(**change)) != base


def test_avatar_wird_eingebaut_und_kaputtes_faellt_zurueck() -> None:
    centre = (48 + 88, 141)
    assert decode(render_welcome_card(card(avatar=avatar_png((200, 30, 30))))).getpixel(centre) == (
        200,
        30,
        30,
    )
    for broken in (b"kein bild", b""):
        assert decode(render_welcome_card(card(avatar=broken))).size == (WIDTH, HEIGHT)


@pytest.mark.parametrize(
    "name", ["", "Ärger Übel ß", "x" * 500, "日本語", "👾", "a\nb", "<@123> `code`"]
)
def test_beliebige_texte(name: str) -> None:
    assert decode(render_welcome_card(card(name=name, subtitle=name, title=name))).size == (
        WIDTH,
        HEIGHT,
    )


def test_langer_name_schrumpft_und_bleibt_im_bild() -> None:
    short = decode(render_welcome_card(card(name="W")))
    long = decode(render_welcome_card(card(name="W" * 200)))
    # Rechts der Textspalte (ausserhalb des Namens) bleibt das Bild gleich.
    box = (WIDTH - 50, 30, WIDTH - 20, 260)
    assert long.crop(box).tobytes() == short.crop(box).tobytes()
