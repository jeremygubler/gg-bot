import asyncio
import itertools
import json

import httpx
import pytest

from ggbot.api.routers import guilds as guilds_router
from ggbot.core import events
from ggbot.core.settings.registry import MODELS
from ggbot.core.settings.starboard import StarboardSettings
from ggbot.core.settings.welcome import WelcomeSettings
from ggbot.core.snapshot import delete_snapshot
from tests.api.conftest import (
    BIG_CHANNEL,
    CSRF,
    GUILD_A,
    GUILD_B,
    GUILD_C,
    USER_ID,
    Api,
    FakeDiscord,
    login,
)

URL = f"/guilds/{GUILD_A}/settings/starboard"


# --- Zugriff ---


async def test_ohne_anmeldung_kein_zugriff_auf_server_routen(api: Api) -> None:
    for path in (f"/guilds/{GUILD_A}/snapshot", URL, f"/guilds/{GUILD_A}/audit", "/modules"):
        assert (await api.client.get(path)).status_code == 401, path


async def test_server_ohne_verwalten_recht_ist_404(api: Api) -> None:
    await login(api)

    response = await api.client.get(f"/guilds/{GUILD_B}/settings/starboard")

    assert response.status_code == 404
    assert response.json()["detail"] == {"error": "guild_not_found"}


async def test_unbekannter_server_ist_404(api: Api) -> None:
    await login(api)

    assert (await api.client.get("/guilds/999/settings/starboard")).status_code == 404


async def test_bot_nicht_auf_dem_server_ist_404_auch_fuer_den_eigentuemer(api: Api) -> None:
    await login(api)

    assert (await api.client.get(f"/guilds/{GUILD_C}/settings/starboard")).status_code == 404


async def test_bot_verlaesst_server_zugriff_endet(api: Api) -> None:
    await login(api)
    assert (await api.client.get(URL)).status_code == 200

    await delete_snapshot(api.redis, GUILD_A)

    assert (await api.client.get(URL)).status_code == 404


async def test_administrator_darf_auch_ohne_manage_guild(api: Api, discord: FakeDiscord) -> None:
    discord.guilds[0]["permissions"] = str(0x8)
    await login(api)

    assert (await api.client.get(URL)).status_code == 200


async def test_andere_rechte_reichen_nicht(api: Api, discord: FakeDiscord) -> None:
    # Kanäle verwalten (0x10), Nachrichten verwalten (0x2000): kein Server-Recht.
    discord.guilds[0]["permissions"] = str(0x10 | 0x2000)
    await login(api)

    assert (await api.client.get(URL)).status_code == 404


async def test_entzogenes_recht_wirkt_nach_hoechstens_60_sekunden(
    api: Api, discord: FakeDiscord
) -> None:
    await login(api)
    discord.guilds[0]["permissions"] = "0"  # in Discord entzogen

    api.clock.now += 30
    assert (await api.client.get(URL)).status_code == 200  # noch gecacht

    api.clock.now += 31
    assert (await api.client.get(URL)).status_code == 404


async def test_neu_geladene_liste_wird_60_sekunden_wiederverwendet(
    api: Api, discord: FakeDiscord
) -> None:
    await login(api)
    discord.calls.clear()

    for _ in range(3):
        await api.client.get(URL)
    assert discord.calls == []

    api.clock.now += 61
    await api.client.get(URL)
    await api.client.get(URL)
    assert discord.calls == [("GET", "/api/v10/users/@me/guilds")]


async def test_widerrufenes_token_beendet_die_sitzung(api: Api, discord: FakeDiscord) -> None:
    await login(api)
    discord.guilds_status = 401
    api.clock.now += 61

    assert (await api.client.get(URL)).status_code == 401

    discord.guilds_status = 200
    assert (await api.client.get(URL)).status_code == 401  # Sitzung ist gelöscht


async def test_discord_stoerung_bei_der_rechtepruefung_lehnt_ab(
    api: Api, discord: FakeDiscord
) -> None:
    await login(api)
    discord.guilds_status = 500
    api.clock.now += 61

    response = await api.client.get(URL)

    assert response.status_code == 503
    assert response.json()["detail"] == {"error": "discord_unavailable"}


async def test_aktualisierung_verlaengert_die_sitzung_nicht(api: Api) -> None:
    await login(api)
    sid = api.client.cookies["gg_session"].rpartition(".")[0]
    await api.redis.expire(f"gg:session:{sid}", 1000)
    api.clock.now += 61

    await api.client.get(URL)

    assert 0 < await api.redis.ttl(f"gg:session:{sid}") <= 1000


# --- Module und Schema ---


async def test_jedes_modul_hat_ein_schema(api: Api) -> None:
    await login(api)

    listed = (await api.client.get("/modules")).json()

    assert {m["module"] for m in listed} == set(MODELS)
    for name in MODELS:
        schema = (await api.client.get(f"/modules/{name}/schema")).json()
        assert schema["type"] == "object", name


async def test_schema_traegt_picker_hinweise_und_string_ids(api: Api) -> None:
    await login(api)

    props = (await api.client.get("/modules/welcome/schema")).json()["properties"]

    channel = next(x for x in props["join_channel_id"]["anyOf"] if x.get("type") == "string")
    assert channel["x-picker"] == "channel"
    assert props["autoroles"]["items"]["x-picker"] == "role"


async def test_unbekanntes_modul_ist_404(api: Api) -> None:
    await login(api)

    assert (await api.client.get("/modules/gibtsnicht/schema")).status_code == 404
    assert (await api.client.get(f"/guilds/{GUILD_A}/settings/gibtsnicht")).status_code == 404


# --- Einstellungen lesen/schreiben ---


async def test_lesen_ohne_gespeicherte_werte_liefert_standard(api: Api) -> None:
    await login(api)

    data = (await api.client.get(URL)).json()

    assert data == StarboardSettings().model_dump(mode="json")


async def test_schreiben_speichert_meldet_und_protokolliert(api: Api) -> None:
    await login(api)
    pubsub = api.redis.pubsub()
    await pubsub.subscribe(events.CHANNEL)
    await pubsub.get_message(timeout=1)  # Abo-Bestätigung

    response = await api.client.put(
        URL, json={"channel_id": str(BIG_CHANNEL), "threshold": 3}, headers=CSRF
    )

    assert response.status_code == 200
    assert response.json()["channel_id"] == str(BIG_CHANNEL)
    assert (await api.client.get(URL)).json()["threshold"] == 3
    # Der Bot sieht die ID als int.
    store = api.app.state.settings
    assert (await store.get(GUILD_A, StarboardSettings)).channel_id == BIG_CHANNEL
    message = await pubsub.get_message(timeout=2)
    assert message is not None
    assert json.loads(message["data"]) == {
        "type": "settings.changed",
        "guild_id": GUILD_A,
        "module": "starboard",
    }
    audit = (await api.client.get(f"/guilds/{GUILD_A}/audit")).json()
    assert len(audit) == 1
    assert audit[0]["user_id"] == str(USER_ID)
    assert audit[0]["module"] == "starboard"
    assert audit[0]["before"] is None
    assert audit[0]["after"]["threshold"] == 3
    await pubsub.aclose()  # type: ignore[no-untyped-call]


async def test_zweite_aenderung_protokolliert_vorher_und_nachher(api: Api) -> None:
    await login(api)
    await api.client.put(URL, json={"threshold": 4}, headers=CSRF)
    await api.client.put(URL, json={"threshold": 7}, headers=CSRF)

    newest = (await api.client.get(f"/guilds/{GUILD_A}/audit")).json()[0]

    assert newest["before"]["threshold"] == 4
    assert newest["after"]["threshold"] == 7


async def test_unveraenderte_werte_erzeugen_weder_protokoll_noch_event(api: Api) -> None:
    await login(api)
    await api.client.put(URL, json={"threshold": 4}, headers=CSRF)
    pubsub = api.redis.pubsub()
    await pubsub.subscribe(events.CHANNEL)
    await pubsub.get_message(timeout=1)

    response = await api.client.put(URL, json={"threshold": 4}, headers=CSRF)

    assert response.status_code == 200
    assert len((await api.client.get(f"/guilds/{GUILD_A}/audit")).json()) == 1
    assert await pubsub.get_message(timeout=0.3) is None
    await pubsub.aclose()  # type: ignore[no-untyped-call]


async def test_schreiben_ohne_csrf_header_ist_403_und_aendert_nichts(api: Api) -> None:
    await login(api)

    response = await api.client.put(URL, json={"threshold": 9})

    assert response.status_code == 403
    assert (await api.client.get(URL)).json()["threshold"] == StarboardSettings().threshold


async def test_schreiben_mit_falschem_csrf_wert_ist_403(api: Api) -> None:
    await login(api)

    response = await api.client.put(URL, json={"threshold": 9}, headers={"X-Requested-With": "x"})

    assert response.status_code == 403


async def test_ungueltige_werte_sind_422_mit_ort_und_ohne_eingabe(api: Api) -> None:
    await login(api)

    response = await api.client.put(
        URL, json={"threshold": -5, "channel_id": "geheim"}, headers=CSRF
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["error"] == "invalid_settings"
    locs = {tuple(f["loc"]) for f in detail["fields"]}
    assert ("threshold",) in locs
    assert "geheim" not in response.text  # Eingaben werden nicht gespiegelt
    assert (await api.client.get(f"/guilds/{GUILD_A}/audit")).json() == []


async def test_ungueltige_id_formen_werden_abgelehnt(api: Api) -> None:
    await login(api)
    url = f"/guilds/{GUILD_A}/settings/welcome"

    for bad in ("12 34", "１２３", "123\n", "-1", "1" * 21, "abc", 1.5):
        response = await api.client.put(url, json={"join_channel_id": bad}, headers=CSRF)
        assert response.status_code == 422, bad


async def test_ids_als_zahl_und_als_string_werden_gleich_gelesen(api: Api) -> None:
    await login(api)
    url = f"/guilds/{GUILD_A}/settings/welcome"

    as_number = await api.client.put(url, json={"join_channel_id": 1234}, headers=CSRF)
    as_string = await api.client.put(url, json={"join_channel_id": "1234"}, headers=CSRF)

    assert as_number.json()["join_channel_id"] == "1234"
    assert as_string.json()["join_channel_id"] == "1234"
    assert (
        len((await api.client.get(f"/guilds/{GUILD_A}/audit")).json()) == 1
    )  # zweites: keine Änderung


async def test_rollenlisten_bleiben_exakt(api: Api) -> None:
    await login(api)
    url = f"/guilds/{GUILD_A}/settings/welcome"

    response = await api.client.put(url, json={"autoroles": [str(BIG_CHANNEL), "5"]}, headers=CSRF)

    assert response.json()["autoroles"] == [str(BIG_CHANNEL), "5"]
    stored = await api.app.state.settings.get(GUILD_A, WelcomeSettings)
    assert stored.autoroles == [BIG_CHANNEL, 5]


async def test_schreiben_auf_fremden_server_ist_404_und_aendert_nichts(api: Api) -> None:
    await login(api)

    response = await api.client.put(
        f"/guilds/{GUILD_B}/settings/starboard", json={"threshold": 9}, headers=CSRF
    )

    assert response.status_code == 404
    store = api.app.state.settings
    assert (await store.get(GUILD_B, StarboardSettings)).threshold == StarboardSettings().threshold


async def test_einstellungen_sind_pro_server_getrennt(api: Api, discord: FakeDiscord) -> None:
    discord.guilds[1]["permissions"] = "32"  # Nutzer verwaltet jetzt auch B
    await login(api)

    await api.client.put(URL, json={"threshold": 9}, headers=CSRF)

    other = (await api.client.get(f"/guilds/{GUILD_B}/settings/starboard")).json()
    assert other["threshold"] == StarboardSettings().threshold
    assert (await api.client.get(f"/guilds/{GUILD_B}/audit")).json() == []


async def test_schreib_limit_pro_nutzer(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(guilds_router, "WRITES_PER_MINUTE", 3)
    await login(api)

    codes = [
        (await api.client.put(URL, json={"threshold": n}, headers=CSRF)).status_code
        for n in (2, 3, 4, 5)
    ]

    assert codes == [200, 200, 200, 429]


async def test_parallele_schreibvorgaenge_ergeben_eine_lueckenlose_protokollkette(
    api: Api,
) -> None:
    await login(api)

    results = await asyncio.gather(
        *(api.client.put(URL, json={"threshold": n}, headers=CSRF) for n in range(4, 12))
    )

    assert all(r.status_code == 200 for r in results)
    audit = (await api.client.get(f"/guilds/{GUILD_A}/audit")).json()
    chronological = list(reversed(audit))
    assert len(chronological) == 8
    assert chronological[0]["before"] is None
    for previous, entry in itertools.pairwise(chronological):
        assert entry["before"] == previous["after"]
    final = (await api.client.get(URL)).json()
    assert final == chronological[-1]["after"]


# --- Audit ---


async def test_audit_blaettert_und_begrenzt(api: Api) -> None:
    await login(api)
    for n in range(4, 9):
        await api.client.put(URL, json={"threshold": n}, headers=CSRF)

    first = (await api.client.get(f"/guilds/{GUILD_A}/audit?limit=2")).json()
    second = (
        await api.client.get(f"/guilds/{GUILD_A}/audit?limit=2&before={first[-1]['id']}")
    ).json()

    assert [e["after"]["threshold"] for e in first] == [8, 7]
    assert [e["after"]["threshold"] for e in second] == [6, 5]
    assert (await api.client.get(f"/guilds/{GUILD_A}/audit?limit=0")).status_code == 422
    assert (await api.client.get(f"/guilds/{GUILD_A}/audit?limit=101")).status_code == 422


async def test_audit_fremder_server_ist_404(api: Api) -> None:
    await login(api)

    assert (await api.client.get(f"/guilds/{GUILD_B}/audit")).status_code == 404


async def test_sitzung_bleibt_ueber_neuen_client_bestehen(api: Api) -> None:
    """Die Sitzung liegt in Redis, nicht im Prozess."""
    await login(api)
    cookie = api.client.cookies["gg_session"]

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api.app),
        base_url="https://bot.example.org",
        cookies={"gg_session": cookie},
    ) as other:
        assert (await other.get("/me")).status_code == 200
