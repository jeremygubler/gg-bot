import io
import time

import pytest
from PIL import Image

from ggbot.render import common, rank_card
from ggbot.render.common import HEIGHT, WIDTH, format_number
from ggbot.render.rank_card import (
    BAR_H,
    BAR_W,
    BAR_X,
    BAR_Y,
    RankCardData,
    render_rank_card,
)

ACCENT = 0xFF8800


def card(**overrides: object) -> RankCardData:
    data: dict[str, object] = {
        "name": "Alice",
        "level": 12,
        "into_level": 500,
        "needed": 1000,
        "total_xp": 9000,
        "position": 3,
        "avatar": None,
        "accent": ACCENT,
        "background": 0x23272A,
        "template": "gradient",
        "rank_label": "RANG",
        "level_label": "LEVEL",
    }
    data.update(overrides)
    return RankCardData(**data)  # type: ignore[arg-type]


def decode(png: bytes) -> Image.Image:
    return Image.open(io.BytesIO(png)).convert("RGB")


def avatar_png(color: tuple[int, int, int] = (200, 30, 30)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), color).save(buffer, format="PNG")
    return buffer.getvalue()


def test_ergebnis_ist_ein_png_in_der_erwarteten_groesse() -> None:
    png = render_rank_card(card())
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert decode(png).size == (WIDTH, HEIGHT)


@pytest.mark.parametrize("template", ["solid", "gradient", "stripes", "dots"])
def test_jede_vorlage_rendert_und_sie_sehen_verschieden_aus(template: str) -> None:
    images = {
        t: render_rank_card(card(template=t)) for t in ("solid", "gradient", "stripes", "dots")
    }
    assert len(set(images.values())) == 4
    assert decode(images[template]).size == (WIDTH, HEIGHT)


def test_gleiche_eingabe_gibt_gleiche_bytes() -> None:
    assert render_rank_card(card()) == render_rank_card(card())
    assert render_rank_card.__wrapped__(card()) == render_rank_card.__wrapped__(card())


def test_andere_eingabe_gibt_anderes_bild() -> None:
    base = render_rank_card(card())
    for change in ({"name": "Bob"}, {"level": 13}, {"accent": 0x00FF00}, {"position": 99}):
        assert render_rank_card(card(**change)) != base


def pixel(image: Image.Image, fraction: float) -> tuple[int, int, int]:
    return image.getpixel((BAR_X + round(BAR_W * fraction), BAR_Y + BAR_H // 2))  # type: ignore[return-value]


def test_fortschrittsbalken_zeigt_den_anteil() -> None:
    accent = (0xFF, 0x88, 0x00)
    half = decode(render_rank_card(card(into_level=500, needed=1000)))
    assert pixel(half, 0.25) == accent
    assert pixel(half, 0.75) != accent
    empty = decode(render_rank_card(card(into_level=0, needed=1000)))
    assert pixel(empty, 0.05) != accent
    full = decode(render_rank_card(card(into_level=1000, needed=1000)))
    assert pixel(full, 0.95) == accent


def test_balken_grenzfaelle_ohne_absturz() -> None:
    for into, needed in ((0, 0), (5, 0), (2000, 1000), (-5, 100)):
        assert decode(render_rank_card(card(into_level=into, needed=needed))).size == (
            WIDTH,
            HEIGHT,
        )


def test_kleiner_fortschritt_ist_sichtbar() -> None:
    accent = (0xFF, 0x88, 0x00)
    tiny = decode(render_rank_card(card(into_level=1, needed=1000)))
    assert pixel(tiny, BAR_H / BAR_W / 2) == accent  # Mindestbreite: eine runde Kappe


def test_avatar_wird_eingebaut() -> None:
    plain = decode(render_rank_card(card()))
    with_avatar = decode(render_rank_card(card(avatar=avatar_png((200, 30, 30)))))
    centre = (48 + 88, 141)
    assert with_avatar.getpixel(centre) == (200, 30, 30)
    assert plain.getpixel(centre) != (200, 30, 30)


def test_kaputter_oder_riesiger_avatar_faellt_auf_den_platzhalter_zurueck() -> None:
    for broken in (b"kein bild", b"\x89PNG\r\n\x1a\nkaputt", b""):
        assert decode(render_rank_card(card(avatar=broken))).size == (WIDTH, HEIGHT)
    huge = b"\x00" * (common.MAX_AVATAR_BYTES + 1)
    assert render_rank_card(card(avatar=huge)) == render_rank_card(card(avatar=None))


@pytest.mark.parametrize(
    "name",
    ["", " ", "Ärger Übel ß", "x" * 500, "日本語の名前", "👾 Emoji", "<@123> `code`", "a\nb"],
)
def test_beliebige_namen_rendern(name: str) -> None:
    assert decode(render_rank_card(card(name=name))).size == (WIDTH, HEIGHT)


@pytest.mark.parametrize(("level", "position"), [(5, 3), (1000, 123456), (1, 1)])
def test_lange_namen_ueberdecken_rang_und_level_nicht(level: int, position: int) -> None:
    long = decode(render_rank_card(card(name="W" * 200, level=level, position=position)))
    short = decode(render_rank_card(card(name="W", level=level, position=position)))
    # Rechts neben dem Namen (Rang/Level) darf der Name nichts verändern. Der Zahlenblock beginnt je
    # nach Grösse der Zahlen woanders, darum prüfen wir erst ab dort, wo er sicher beginnt.
    block_left = rank_card.header_left(card(level=level, position=position))
    assert block_left > common.TEXT_X + 80  # der Name behält Platz
    box = (int(block_left), 30, WIDTH - 40, 100)
    assert long.crop(box).tobytes() == short.crop(box).tobytes()


def test_name_endet_vor_dem_zahlenblock() -> None:
    data = card(name="W" * 200, level=1000, position=123456)
    image = decode(render_rank_card(data))
    block_left = int(rank_card.header_left(data))
    # Zwischen Namensende und Zahlenblock bleibt eine Lücke ohne Namens-Pixel (weisser Text).
    gap = image.crop((block_left - 20, 55, block_left - 2, 105))
    colors = {color for _, color in gap.getcolors(100_000) or []}
    assert (255, 255, 255) not in colors


def test_sehr_grosse_werte() -> None:
    big = card(level=1000, total_xp=1_700_000_000, position=123456, into_level=10**9, needed=10**10)
    assert decode(render_rank_card(big)).size == (WIDTH, HEIGHT)


def test_zahlenformat() -> None:
    assert format_number(0) == "0"
    assert format_number(1234) == "1 234"
    assert format_number(1234567) == "1 234 567"


def test_rendern_ist_schnell_genug_fuer_einen_befehl() -> None:
    started = time.perf_counter()
    render_rank_card.__wrapped__(card(avatar=avatar_png(), name="Zeitmessung"))
    assert time.perf_counter() - started < 1.0


def test_schrift_hat_einen_fallback() -> None:
    common.font.cache_clear()
    font = common.font(20, False)
    assert font is not None
