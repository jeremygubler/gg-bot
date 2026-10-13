from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select

from ggbot.core.db.models import DashboardAudit, ScheduledJob
from tests.api.conftest import BIG_CHANNEL, CSRF, GUILD_A, GUILD_B, VOICE, Api, login

URL = f"/guilds/{GUILD_A}/announcements"
BODY = {"name": "Eröffnung", "content": "Hallo", "embed": {"title": "Willkommen", "color": 255}}


async def post(api: Api, path: str, body: Any = None, locale: str | None = None) -> httpx.Response:
    headers = {**CSRF, **({"X-Locale": locale} if locale else {})}
    return await api.client.post(path, json=body, headers=headers)


async def created(api: Api, **extra: Any) -> dict[str, Any]:
    response = await post(api, URL, BODY | extra)
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


async def test_ohne_anmeldung_und_ohne_recht_kein_zugriff(api: Api) -> None:
    assert (await api.client.get(URL)).status_code == 401
    await login(api)
    assert (await api.client.get(f"/guilds/{GUILD_B}/announcements")).status_code == 404
    response = await api.client.post(f"/guilds/{GUILD_B}/announcements", json=BODY, headers=CSRF)
    assert response.status_code == 404


async def test_aendern_braucht_den_csrf_header(api: Api) -> None:
    await login(api)
    entry = await created(api)

    for method, path, body in (
        ("POST", URL, BODY),
        ("PUT", f"{URL}/{entry['id']}", BODY),
        ("DELETE", f"{URL}/{entry['id']}", None),
        ("POST", f"{URL}/{entry['id']}/send", {"channel_id": str(BIG_CHANNEL)}),
        ("POST", f"{URL}/{entry['id']}/cancel", None),
    ):
        response = await api.client.request(method, path, json=body)
        assert response.status_code == 403, (method, path)


async def test_anlegen_lesen_aendern_loeschen(api: Api) -> None:
    await login(api)

    entry = await created(api)
    assert entry["state"] == "draft"
    assert entry["embed"]["title"] == "Willkommen"
    assert (await api.client.get(f"{URL}/{entry['id']}")).json()["content"] == "Hallo"
    assert [e["id"] for e in (await api.client.get(URL)).json()] == [entry["id"]]

    response = await api.client.put(
        f"{URL}/{entry['id']}", json={"name": "Neu", "content": "Anders"}, headers=CSRF
    )
    assert (response.json()["name"], response.json()["embed"]) == ("Neu", None)
    assert (await api.client.delete(f"{URL}/{entry['id']}", headers=CSRF)).status_code == 200
    assert (await api.client.get(f"{URL}/{entry['id']}")).status_code == 422


async def test_ungueltige_nachrichten(api: Api) -> None:
    await login(api)

    cases = [
        {"name": "x"},  # leer
        {"name": "x", "content": "a" * 2001},
        {"name": "x", "embed": {"title": "t" * 257}},
        {"name": "x", "embed": {"title": "t", "color": 0x1000000}},
        {"name": "x", "embed": {"title": "t", "image_url": "http://x.org/a.png"}},
        {"name": "x", "embed": {"title": "t", "unbekannt": 1}},
        {"name": "", "content": "a"},
    ]
    for body in cases:
        assert (await post(api, URL, body)).status_code == 422, body
    assert (await api.client.get(URL)).json() == []


async def test_fehlermeldung_ist_uebersetzt(api: Api) -> None:
    await login(api)

    response = await post(api, URL, {"name": "x", "content": "  "}, locale="en")

    assert response.status_code == 422
    assert "empty" in response.text


async def test_senden_plant_einen_job_und_schreibt_audit(api: Api) -> None:
    await login(api)
    entry = await created(api)

    response = await post(api, f"{URL}/{entry['id']}/send", {"channel_id": str(BIG_CHANNEL)})

    assert response.status_code == 202
    assert response.json()["state"] == "scheduled"
    assert response.json()["channel_id"] == str(BIG_CHANNEL)  # String, nicht Zahl
    async with api.db.session() as session:
        job = (await session.execute(select(ScheduledJob))).scalar_one()
        audit = (await session.execute(select(DashboardAudit))).scalar_one()
    assert (job.kind, job.guild_id, job.payload) == (
        "announce.sync",
        GUILD_A,
        {"id": int(entry["id"]), "rev": 1},
    )
    assert (audit.module, audit.after["action"]) == ("announcements", "send")


async def test_geplantes_senden_mit_zeitzone(api: Api) -> None:
    await login(api)
    entry = await created(api)
    when = (datetime.now(UTC) + timedelta(days=1)).replace(microsecond=0)

    response = await post(
        api,
        f"{URL}/{entry['id']}/send",
        {"channel_id": str(BIG_CHANNEL), "scheduled_at": when.isoformat()},
    )

    assert response.status_code == 202
    assert datetime.fromisoformat(response.json()["scheduled_at"]) == when
    # Ohne Zeitzone weiss der Server nicht, wann gemeint ist.
    naive = when.replace(tzinfo=None).isoformat()
    bad = await post(
        api, f"{URL}/{entry['id']}/send", {"channel_id": str(BIG_CHANNEL), "scheduled_at": naive}
    )
    assert bad.status_code == 422


async def test_senden_in_ungueltigen_kanal(api: Api) -> None:
    await login(api)
    entry = await created(api)

    for channel in (VOICE, 999, 555):  # Sprachkanal, unbekannt, Kategorie
        response = await post(api, f"{URL}/{entry['id']}/send", {"channel_id": str(channel)})
        assert response.status_code == 422, channel
    async with api.db.session() as session:
        assert (await session.execute(select(ScheduledJob))).first() is None


async def test_absagen(api: Api) -> None:
    await login(api)
    entry = await created(api)
    when = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    await post(
        api, f"{URL}/{entry['id']}/send", {"channel_id": str(BIG_CHANNEL), "scheduled_at": when}
    )

    response = await post(api, f"{URL}/{entry['id']}/cancel")
    assert response.json()["state"] == "draft"
    assert (await post(api, f"{URL}/{entry['id']}/cancel")).status_code == 422  # nicht mehr geplant


async def test_fremde_ankuendigung_ist_unbekannt(api: Api, discord: Any) -> None:
    await login(api)
    entry = await created(api)
    # Dieselbe Nummer unter einem anderen Server (Nutzer verwaltet ihn auch).
    discord.guilds[1]["permissions"] = "32"
    api.client.cookies.clear()

    await login(api)
    response = await api.client.get(f"/guilds/{GUILD_B}/announcements/{entry['id']}")
    assert response.status_code == 422
