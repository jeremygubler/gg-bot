import pytest
from structlog.testing import capture_logs

from tests.api.conftest import CSRF, GUILD_A, Api, login


async def test_antworten_tragen_sicherheitskoepfe_und_sind_nicht_cachebar(api: Api) -> None:
    await login(api)

    response = await api.client.get("/me")

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["cache-control"] == "no-store"


async def test_auch_fehlerantworten_sind_nicht_cachebar(api: Api) -> None:
    response = await api.client.get("/me")

    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_eigene_cache_koepfe_bleiben(api: Api) -> None:
    from ggbot.core.publicboard import save_board
    from tests.api.test_public import board, enable

    await enable(api)
    await save_board(api.redis, GUILD_A, board())

    public = await api.client.get(f"/public/{GUILD_A}/leaderboard")
    await login(api)
    preview = await api.client.post(f"/guilds/{GUILD_A}/preview/rank", json={}, headers=CSRF)

    assert public.headers["cache-control"] == "public, max-age=60"
    assert preview.headers["cache-control"] == "private, max-age=60"


@pytest.mark.parametrize("secret", ["abc-geheimer-code", "state-wert"])
async def test_zugriffsprotokoll_enthaelt_nie_die_query(api: Api, secret: str) -> None:
    with capture_logs() as logs:
        await api.client.get("/auth/callback", params={"code": secret, "state": secret})

    entries = [e for e in logs if e["event"] == "request"]
    assert entries
    assert entries[0]["path"] == "/auth/callback"
    assert secret not in str(entries)


async def test_zugriffsprotokoll_nennt_methode_status_und_dauer(api: Api) -> None:
    with capture_logs() as logs:
        await api.client.get("/healthz")

    entry = next(e for e in logs if e["event"] == "request")
    assert (entry["method"], entry["path"], entry["status"]) == ("GET", "/healthz", 200)
    assert entry["ms"] >= 0
