import pytest

from ggbot.api.routers import public as public_router
from ggbot.api.routers.public import client_key
from ggbot.core.publicboard import BoardRow, PublicBoard, save_board
from ggbot.core.settings.leveling import LevelingSettings
from tests.api.conftest import GUILD_A, GUILD_B, Api


def board(count: int = 3) -> PublicBoard:
    return PublicBoard(
        guild_name="Alpha",
        rows=[
            BoardRow(
                position=i + 1,
                name=f"Mitglied{i + 1}" if i != 1 else None,
                avatar_url=f"https://cdn.example/{i}.png",
                level=10 - i % 10,
                xp=1000 - i,
            )
            for i in range(count)
        ],
    )


async def enable(api: Api, guild: int = GUILD_A, *, public: bool = True, on: bool = True) -> None:
    await api.app.state.settings.save(
        guild, LevelingSettings(enabled=on, public_leaderboard=public)
    )


def url(guild: int = GUILD_A, page: int = 1) -> str:
    return f"/public/{guild}/leaderboard?page={page}"


async def test_freigegebene_rangliste_ist_ohne_anmeldung_lesbar(api: Api) -> None:
    await enable(api)
    await save_board(api.redis, GUILD_A, board())

    response = await api.client.get(url())

    assert response.status_code == 200
    data = response.json()
    assert data["guild_name"] == "Alpha"
    assert [r["position"] for r in data["rows"]] == [1, 2, 3]
    assert data["rows"][1]["name"] is None
    assert response.headers["cache-control"] == "public, max-age=60"


async def test_antwort_enthaelt_keine_nutzer_ids(api: Api) -> None:
    await enable(api)
    await save_board(api.redis, GUILD_A, board())

    data = (await api.client.get(url())).json()

    for row in data["rows"]:
        assert set(row) == {"position", "name", "avatar_url", "level", "xp"}


@pytest.mark.parametrize(
    ("public", "on"),
    [(False, True), (True, False), (False, False)],
    ids=["nicht-frei", "leveling-aus", "beides-aus"],
)
async def test_ohne_freigabe_ist_es_404(api: Api, public: bool, on: bool) -> None:
    await enable(api, public=public, on=on)
    await save_board(api.redis, GUILD_A, board())

    response = await api.client.get(url())

    assert response.status_code == 404
    assert response.json()["detail"] == {"error": "not_found"}


async def test_unbekannter_server_sieht_aus_wie_nicht_freigegeben(api: Api) -> None:
    unknown = await api.client.get(url(999))
    disabled = await api.client.get(url())

    assert unknown.status_code == disabled.status_code == 404
    assert unknown.json() == disabled.json()


async def test_freigegeben_aber_noch_nicht_gebaut_ist_503(api: Api) -> None:
    await enable(api)

    response = await api.client.get(url())

    assert response.status_code == 503
    assert response.json()["detail"] == {"error": "board_unavailable"}


async def test_andere_server_bleiben_getrennt(api: Api) -> None:
    await enable(api, GUILD_A)
    await save_board(api.redis, GUILD_A, board())
    await save_board(api.redis, GUILD_B, board())  # B ist nicht freigegeben

    assert (await api.client.get(url(GUILD_B))).status_code == 404


async def test_blaettern_und_begrenzen(api: Api) -> None:
    await enable(api)
    await save_board(api.redis, GUILD_A, board(60))

    first = (await api.client.get(url(page=1))).json()
    third = (await api.client.get(url(page=3))).json()
    beyond = (await api.client.get(url(page=99))).json()

    assert (first["pages"], len(first["rows"])) == (3, 25)
    assert [r["position"] for r in third["rows"]] == list(range(51, 61))
    assert beyond["page"] == 3
    assert (await api.client.get(url(page=0))).status_code == 422
    assert (await api.client.get(url(page=1001))).status_code == 422


async def test_ausschalten_wirkt_sofort_auch_bei_vorhandenem_zwischenspeicher(api: Api) -> None:
    await enable(api)
    await save_board(api.redis, GUILD_A, board())
    assert (await api.client.get(url())).status_code == 200

    await enable(api, public=False)

    assert (await api.client.get(url())).status_code == 404


async def test_limit_pro_besucher_und_nicht_pro_server(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(public_router, "REQUESTS_PER_MINUTE", 2)
    await enable(api)
    await save_board(api.redis, GUILD_A, board())
    one = {"X-Forwarded-For": "203.0.113.1"}
    two = {"X-Forwarded-For": "203.0.113.2"}

    codes = [(await api.client.get(url(), headers=one)).status_code for _ in range(3)]
    other = (await api.client.get(url(), headers=two)).status_code

    assert codes == [200, 200, 429]
    assert other == 200


async def test_gefaelschter_vorderer_eintrag_umgeht_das_limit_nicht(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(public_router, "REQUESTS_PER_MINUTE", 2)
    await enable(api)
    await save_board(api.redis, GUILD_A, board())

    codes = [
        (
            await api.client.get(url(), headers={"X-Forwarded-For": f"6.6.6.{n}, 203.0.113.9"})
        ).status_code
        for n in range(3)
    ]

    assert codes == [200, 200, 429]


class _Req:
    def __init__(self, forwarded: str | None) -> None:
        self.headers = {} if forwarded is None else {"x-forwarded-for": forwarded}
        self.client = type("C", (), {"host": "10.0.0.5"})()


@pytest.mark.parametrize(
    ("header", "hops", "expected"),
    [
        (None, 1, "10.0.0.5"),
        ("1.1.1.1", 1, "1.1.1.1"),
        ("9.9.9.9, 1.1.1.1", 1, "1.1.1.1"),
        ("9.9.9.9, 2.2.2.2, 1.1.1.1", 2, "2.2.2.2"),
        ("1.1.1.1", 3, "1.1.1.1"),
        ("  , ,", 1, "10.0.0.5"),
    ],
)
def test_client_key(header: str | None, hops: int, expected: str) -> None:
    assert client_key(_Req(header), hops) == expected  # type: ignore[arg-type]


async def test_riesige_ids_sind_404_nicht_500(api: Api) -> None:
    """Postgres lehnt Zahlen über int64 ab; das darf auf einer offenen Route kein 500 werden."""
    for huge in (2**63, 99999999999999999999999):
        assert (await api.client.get(f"/public/{huge}/leaderboard")).status_code == 422


async def test_avatar_nur_von_discords_cdn(api: Api) -> None:
    await enable(api)
    rows = [
        BoardRow(
            position=1,
            name="a",
            avatar_url="https://cdn.discordapp.com/avatars/1/x.png",
            level=1,
            xp=1,
        ),
        BoardRow(position=2, name="b", avatar_url="https://evil.example/x.png", level=1, xp=1),
        BoardRow(position=3, name="c", avatar_url="javascript:alert(1)", level=1, xp=1),
        BoardRow(
            position=4,
            name="d",
            avatar_url="https://cdn.discordapp.com.evil.example/x.png",
            level=1,
            xp=1,
        ),
    ]
    await save_board(api.redis, GUILD_A, PublicBoard(guild_name="Alpha", rows=rows))

    avatars = [r["avatar_url"] for r in (await api.client.get(url())).json()["rows"]]

    assert avatars == ["https://cdn.discordapp.com/avatars/1/x.png", None, None, None]


def test_client_key_ignoriert_keine_ip_eintraege() -> None:
    assert client_key(_Req("x" * 5000), 1) == "10.0.0.5"  # type: ignore[arg-type]
    assert client_key(_Req("not-an-ip"), 1) == "10.0.0.5"  # type: ignore[arg-type]
    assert client_key(_Req("::1"), 1) == "::1"  # type: ignore[arg-type]
