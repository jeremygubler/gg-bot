import io

import pytest
from PIL import Image

from ggbot.api.routers import preview as preview_router
from tests.api.conftest import CSRF, GUILD_A, GUILD_B, Api, login

PNG = b"\x89PNG\r\n\x1a\n"


def url(card: str, guild: int = GUILD_A) -> str:
    return f"/guilds/{guild}/preview/{card}"


def pixels(png: bytes) -> Image.Image:
    return Image.open(io.BytesIO(png)).convert("RGB")


@pytest.mark.parametrize("card", ["rank", "welcome"])
async def test_vorschau_liefert_ein_png(api: Api, card: str) -> None:
    await login(api)

    response = await api.client.post(url(card), json={}, headers=CSRF)

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(PNG)
    assert pixels(response.content).size[0] > 100


@pytest.mark.parametrize("card", ["rank", "welcome"])
async def test_vorschau_folgt_dem_stil(api: Api, card: str) -> None:
    await login(api)
    red = {"background": 0xFF0000, "accent": 0xFF0000, "template": "solid"}
    blue = {"background": 0x0000FF, "accent": 0x0000FF, "template": "solid"}

    a = pixels((await api.client.post(url(card), json=red, headers=CSRF)).content)
    b = pixels((await api.client.post(url(card), json=blue, headers=CSRF)).content)

    red_r, _, red_b = a.getpixel((2, 2))  # type: ignore[misc]
    blue_r, _, blue_b = b.getpixel((2, 2))  # type: ignore[misc]
    assert red_r > red_b  # Rotanteil überwiegt
    assert blue_b > blue_r


async def test_vorschau_prueft_den_stil(api: Api) -> None:
    await login(api)

    for bad in ({"template": "nope"}, {"accent": -1}, {"background": 0x1000000}, {"accent": "rot"}):
        response = await api.client.post(url("rank"), json=bad, headers=CSRF)
        assert response.status_code == 422, bad


async def test_unbekannte_kartenart_ist_422(api: Api) -> None:
    await login(api)

    assert (await api.client.post(url("other"), json={}, headers=CSRF)).status_code == 422


async def test_vorschau_braucht_anmeldung_csrf_und_zugriff(api: Api) -> None:
    assert (await api.client.post(url("rank"), json={}, headers=CSRF)).status_code == 401
    await login(api)
    assert (await api.client.post(url("rank"), json={})).status_code == 403
    assert (await api.client.post(url("rank", GUILD_B), json={}, headers=CSRF)).status_code == 404


async def test_vorschau_hat_ein_limit(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(preview_router, "PREVIEWS_PER_MINUTE", 2)
    await login(api)

    codes = [
        (await api.client.post(url("rank"), json={}, headers=CSRF)).status_code for _ in range(3)
    ]

    assert codes == [200, 200, 429]


async def test_vorschau_sprache_folgt_dem_kopf(api: Api) -> None:
    await login(api)
    de = await api.client.post(url("rank"), json={}, headers={**CSRF, "X-Locale": "de"})
    en = await api.client.post(url("rank"), json={}, headers={**CSRF, "X-Locale": "en"})

    assert de.content != en.content  # "Beispiel" und "Rang" gegen "Example" und "Rank"
