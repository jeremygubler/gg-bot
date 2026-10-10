import json
from typing import Any

import httpx
import pytest

from ggbot.api.routers import panels as panels_router
from ggbot.core import events
from ggbot.core.panelstatus import PanelStatus, set_status
from tests.api.conftest import (
    BIG_CHANNEL,
    CATEGORY,
    CSRF,
    GUILD_A,
    GUILD_B,
    VOICE,
    Api,
    FakeDiscord,
    login,
)

ROLES = f"/guilds/{GUILD_A}/panels/roles"
TICKETS = f"/guilds/{GUILD_A}/panels/tickets"
ADMINISTRATOR = "8"


@pytest.fixture
def admin(discord: FakeDiscord) -> None:
    discord.guilds[0]["permissions"] = ADMINISTRATOR


async def post(api: Api, path: str, body: Any = None, locale: str | None = None) -> httpx.Response:
    headers = {**CSRF, **({"X-Locale": locale} if locale else {})}
    return await api.client.post(path, json=body, headers=headers)


async def role_panel(api: Api, kind: str = "button", **extra: Any) -> dict[str, Any]:
    response = await post(api, ROLES, {"title": "Rollen", "kind": kind, **extra})
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


async def listen(api: Api) -> Any:
    pubsub = api.redis.pubsub()
    await pubsub.subscribe(events.CHANNEL)
    await pubsub.get_message(timeout=1)
    return pubsub


async def next_event(pubsub: Any) -> dict[str, Any] | None:
    message = await pubsub.get_message(timeout=1.5)
    return json.loads(message["data"]) if message else None


# --- Berechtigung ---


async def test_rollen_panels_lesen_darf_wer_server_verwaltet(api: Api) -> None:
    await login(api)

    assert (await api.client.get(ROLES)).json() == []
    assert (await api.client.get(TICKETS)).json() == []


@pytest.mark.parametrize(
    ("method", "suffix", "body"),
    [
        ("POST", "", {"title": "x", "kind": "button"}),
        ("PUT", "/1", {"title": "x", "mode": "normal"}),
        ("DELETE", "/1", None),
        ("POST", "/1/items", {"role_id": "5", "label": "x"}),
        ("DELETE", "/1/items/5", None),
        ("POST", "/1/publish", {"channel_id": "1"}),
    ],
)
async def test_rollen_panels_aendern_braucht_administrator(
    api: Api, method: str, suffix: str, body: Any
) -> None:
    """Wer nur *Server verwalten* darf, könnte über ein Panel sich selbst Rollen geben."""
    await login(api)

    response = await api.client.request(method, ROLES + suffix, json=body, headers=CSRF)

    assert response.status_code == 403
    assert response.json()["detail"] == {"error": "needs_administrator"}


async def test_eigentuemer_ohne_administrator_recht_darf_rollen_panels_aendern(
    api: Api, discord: FakeDiscord
) -> None:
    discord.guilds[0]["owner"] = True
    discord.guilds[0]["permissions"] = "0"
    await login(api)

    assert (await post(api, ROLES, {"title": "x", "kind": "button"})).status_code == 201


async def test_ticket_panels_aendern_reicht_server_verwalten(api: Api) -> None:
    await login(api)

    assert (await post(api, TICKETS, {"title": "Support"})).status_code == 201


async def test_panel_aenderungen_brauchen_den_csrf_header(api: Api, admin: None) -> None:
    await login(api)

    for path in (ROLES, TICKETS):
        response = await api.client.post(path, json={"title": "x", "kind": "button"})
        assert response.status_code == 403, path


async def test_panels_fremder_server_ist_404(api: Api, admin: None) -> None:
    await login(api)

    assert (await api.client.get(f"/guilds/{GUILD_B}/panels/roles")).status_code == 404


async def test_panel_eines_anderen_servers_ist_nicht_erreichbar(
    api: Api, discord: FakeDiscord
) -> None:
    discord.guilds[0]["permissions"] = ADMINISTRATOR
    discord.guilds[1]["permissions"] = ADMINISTRATOR
    await login(api)
    panel = await role_panel(api)

    other = await api.client.get(f"/guilds/{GUILD_B}/panels/roles/{panel['id']}/status")
    deleted = await api.client.delete(f"/guilds/{GUILD_B}/panels/roles/{panel['id']}", headers=CSRF)

    assert other.status_code == 422
    assert deleted.status_code == 422
    assert len((await api.client.get(ROLES)).json()) == 1  # unangetastet


# --- Rollen-Panels ---


async def test_rollen_panel_anlegen_und_listen_mit_string_ids(api: Api, admin: None) -> None:
    await login(api)

    created = await role_panel(api, "select", mode="unique", description="Wähle")
    listed = (await api.client.get(ROLES)).json()

    assert listed == [created]
    assert created["kind"] == "select"
    assert created["mode"] == "unique"
    assert isinstance(created["id"], str)
    assert created["published"] is False
    assert created["items"] == []


async def test_dienstfehler_kommen_uebersetzt_in_der_sprache_der_oberflaeche(
    api: Api, admin: None
) -> None:
    await login(api)
    body = {"title": "   ", "kind": "button"}

    de = await post(api, ROLES, body, locale="de")
    en = await post(api, ROLES, body, locale="en")
    unknown = await post(api, ROLES, body, locale="xx")

    assert de.status_code == en.status_code == 422
    assert de.json()["detail"]["error"] == "user_error"
    assert de.json()["detail"]["message"] != en.json()["detail"]["message"]
    assert unknown.json()["detail"]["message"] == de.json()["detail"]["message"]  # Standard: de


async def test_eingabegrenzen_werden_geprueft(api: Api, admin: None) -> None:
    await login(api)

    too_long = await post(api, ROLES, {"title": "x" * 300, "kind": "button"})
    bad_kind = await post(api, ROLES, {"title": "x", "kind": "magic"})
    bad_mode = await post(api, ROLES, {"title": "x", "kind": "button", "mode": "all"})

    assert too_long.status_code == bad_kind.status_code == bad_mode.status_code == 422


async def test_eintrag_hinzufuegen_und_entfernen(api: Api, admin: None) -> None:
    await login(api)
    panel = await role_panel(api)

    added = await post(api, f"{ROLES}/{panel['id']}/items", {"role_id": "5", "label": "Mod"})
    assert added.status_code == 201
    item = added.json()["items"][0]
    assert (item["role_id"], item["label"]) == ("5", "Mod")

    removed = await api.client.delete(f"{ROLES}/{panel['id']}/items/5", headers=CSRF)
    assert removed.status_code == 200
    assert removed.json()["items"] == []


@pytest.mark.parametrize(
    ("role_id", "why"),
    [
        (GUILD_A, "@everyone"),
        (6, "verwaltet"),
        (7, "über der Rolle des Bots"),
        (999, "gibt es nicht"),
    ],
)
async def test_nicht_vergebbare_rollen_werden_abgelehnt(
    api: Api, admin: None, role_id: int, why: str
) -> None:
    await login(api)
    panel = await role_panel(api)

    response = await post(
        api, f"{ROLES}/{panel['id']}/items", {"role_id": str(role_id), "label": "x"}
    )

    assert response.status_code == 422, why
    assert response.json()["detail"]["error"] == "user_error"
    assert (await api.client.get(ROLES)).json()[0]["items"] == []


async def test_rolle_genau_auf_hoehe_des_bots_ist_nicht_vergebbar(api: Api, admin: None) -> None:
    from ggbot.core.snapshot import load_snapshot, save_snapshot

    await login(api)
    snapshot = await load_snapshot(api.redis, GUILD_A)
    assert snapshot is not None
    snapshot.bot_top_position = 1  # "Mod" steht auf Position 1: nicht darunter
    await save_snapshot(api.redis, snapshot)
    panel = await role_panel(api)

    response = await post(api, f"{ROLES}/{panel['id']}/items", {"role_id": "5", "label": "x"})

    assert response.status_code == 422


async def test_unbekannte_botposition_vergibt_nichts(api: Api, admin: None) -> None:
    """Ein alter Snapshot ohne Botposition (0) darf nicht alles freigeben."""
    from ggbot.core.snapshot import load_snapshot, save_snapshot

    await login(api)
    snapshot = await load_snapshot(api.redis, GUILD_A)
    assert snapshot is not None
    snapshot.bot_top_position = 0
    await save_snapshot(api.redis, snapshot)
    panel = await role_panel(api)

    response = await post(api, f"{ROLES}/{panel['id']}/items", {"role_id": "5", "label": "x"})

    assert response.status_code == 422


async def test_ungueltiges_emoji_und_fehlende_beschriftung(api: Api, admin: None) -> None:
    await login(api)
    panel = await role_panel(api)
    url = f"{ROLES}/{panel['id']}/items"

    assert (
        await post(api, url, {"role_id": "5", "emoji": "kein emoji", "label": "x"})
    ).status_code == 422
    assert (await post(api, url, {"role_id": "5"})).status_code == 422  # Button ohne Text/Emoji
    assert (await post(api, url, {"role_id": "5", "emoji": "🎮"})).status_code == 201


async def test_reaktions_panel_braucht_ein_emoji(api: Api, admin: None) -> None:
    await login(api)
    panel = await role_panel(api, "reaction")

    response = await post(api, f"{ROLES}/{panel['id']}/items", {"role_id": "5", "label": "x"})

    assert response.status_code == 422


async def test_doppelte_rolle_wird_abgelehnt(api: Api, admin: None) -> None:
    await login(api)
    panel = await role_panel(api)
    url = f"{ROLES}/{panel['id']}/items"
    await post(api, url, {"role_id": "5", "label": "a"})

    assert (await post(api, url, {"role_id": "5", "label": "b"})).status_code == 422


async def test_panel_bearbeiten(api: Api, admin: None) -> None:
    await login(api)
    panel = await role_panel(api)

    response = await api.client.put(
        f"{ROLES}/{panel['id']}",
        json={"title": "Neu", "description": "Text", "mode": "verify"},
        headers=CSRF,
    )

    assert response.status_code == 200
    assert (response.json()["title"], response.json()["mode"]) == ("Neu", "verify")
    assert response.json()["kind"] == "button"  # die Art bleibt


async def test_unveroeffentlichtes_panel_erzeugt_beim_bearbeiten_kein_ereignis(
    api: Api, admin: None
) -> None:
    await login(api)
    panel = await role_panel(api)
    pubsub = await listen(api)

    await api.client.put(
        f"{ROLES}/{panel['id']}", json={"title": "Neu", "mode": "normal"}, headers=CSRF
    )
    await post(api, f"{ROLES}/{panel['id']}/items", {"role_id": "5", "label": "x"})

    assert await next_event(pubsub) is None
    await pubsub.aclose()


async def test_veroeffentlichtes_panel_wird_nach_aenderungen_aktualisiert(
    api: Api, admin: None
) -> None:
    from ggbot.core.services.rolepanels import RolePanelService

    await login(api)
    panel = await role_panel(api)
    service: RolePanelService = api.app.state.rolepanels
    await service.set_message(GUILD_A, int(panel["id"]), BIG_CHANNEL, 900)
    pubsub = await listen(api)

    await post(api, f"{ROLES}/{panel['id']}/items", {"role_id": "5", "label": "x"})

    event = await next_event(pubsub)
    assert event is not None
    assert (event["action"], event["kind"], event["panel_id"]) == (
        "refresh",
        "role",
        int(panel["id"]),
    )
    await pubsub.aclose()


async def test_veroeffentlichen_prueft_kanal_und_schickt_den_auftrag(api: Api, admin: None) -> None:
    await login(api)
    panel = await role_panel(api)
    await post(api, f"{ROLES}/{panel['id']}/items", {"role_id": "5", "label": "x"})
    url = f"{ROLES}/{panel['id']}/publish"
    pubsub = await listen(api)

    for bad in (VOICE, CATEGORY, 999):
        assert (await post(api, url, {"channel_id": str(bad)})).status_code == 422, bad
    assert await next_event(pubsub) is None  # nichts verschickt

    response = await post(api, url, {"channel_id": str(BIG_CHANNEL)})

    assert response.status_code == 202
    assert response.json()["state"] == "pending"
    event = await next_event(pubsub)
    assert event is not None
    assert event == {
        "type": "panel.publish",
        "guild_id": GUILD_A,
        "panel_id": int(panel["id"]),
        "kind": "role",
        "action": "publish",
        "channel_id": BIG_CHANNEL,
        "message_id": None,
    }
    status = (await api.client.get(f"{ROLES}/{panel['id']}/status")).json()
    assert status["state"] == "pending"
    await pubsub.aclose()


async def test_leeres_panel_laesst_sich_nicht_veroeffentlichen(api: Api, admin: None) -> None:
    await login(api)
    panel = await role_panel(api)

    response = await post(api, f"{ROLES}/{panel['id']}/publish", {"channel_id": str(BIG_CHANNEL)})

    assert response.status_code == 422


async def test_status_zeigt_ergebnis_des_bots_uebersetzt(api: Api, admin: None) -> None:
    await login(api)
    panel = await role_panel(api)
    url = f"{ROLES}/{panel['id']}/status"
    assert (await api.client.get(url)).json() == {"state": "none", "message": None, "notice": None}

    await set_status(
        api.redis,
        "role",
        GUILD_A,
        int(panel["id"]),
        PanelStatus(
            state="error",
            error_key="error.missing_bot_permissions",
            error_params={"permissions": "Send Messages"},
        ),
    )
    en = (await api.client.get(url, headers={"X-Locale": "en"})).json()
    de = (await api.client.get(url, headers={"X-Locale": "de"})).json()

    assert en["state"] == "error"
    assert "Send Messages" in en["message"]
    assert de["message"] != en["message"]

    await set_status(
        api.redis,
        "role",
        GUILD_A,
        int(panel["id"]),
        PanelStatus(
            state="done", notice_key="roles.reaction_failed", notice_params={"emojis": "🎮"}
        ),
    )
    done = (await api.client.get(url)).json()
    assert done["state"] == "done"
    assert "🎮" in done["notice"]


async def test_loeschen_veroeffentlichten_panels_beauftragt_den_bot(api: Api, admin: None) -> None:
    from ggbot.core.services.rolepanels import RolePanelService

    await login(api)
    panel = await role_panel(api)
    service: RolePanelService = api.app.state.rolepanels
    await service.set_message(GUILD_A, int(panel["id"]), BIG_CHANNEL, 900)
    pubsub = await listen(api)

    response = await api.client.delete(f"{ROLES}/{panel['id']}", headers=CSRF)

    assert response.status_code == 204
    event = await next_event(pubsub)
    assert event is not None
    assert (event["action"], event["channel_id"], event["message_id"]) == (
        "delete",
        BIG_CHANNEL,
        900,
    )
    assert (await api.client.get(ROLES)).json() == []
    await pubsub.aclose()


async def test_loeschen_unveroeffentlichten_panels_braucht_keinen_bot(
    api: Api, admin: None
) -> None:
    await login(api)
    panel = await role_panel(api)
    pubsub = await listen(api)

    assert (await api.client.delete(f"{ROLES}/{panel['id']}", headers=CSRF)).status_code == 204

    assert await next_event(pubsub) is None
    await pubsub.aclose()


async def test_panel_schreib_limit(api: Api, admin: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(panels_router, "WRITES_PER_MINUTE", 2)
    await login(api)

    codes = [
        (await post(api, ROLES, {"title": f"p{n}", "kind": "button"})).status_code for n in range(3)
    ]

    assert codes == [201, 201, 429]


# --- Ticket-Panels ---


async def ticket_panel(api: Api) -> dict[str, Any]:
    response = await post(api, TICKETS, {"title": "Support", "description": "Hilfe"})
    assert response.status_code == 201
    return response.json()  # type: ignore[no-any-return]


def category(**extra: Any) -> dict[str, Any]:
    return {"name": "Hilfe", "support_role_id": "5", **extra}


async def test_kategorie_anlegen_und_entfernen(api: Api) -> None:
    await login(api)
    panel = await ticket_panel(api)

    added = await post(
        api,
        f"{TICKETS}/{panel['id']}/categories",
        category(
            emoji="🛟", parent_id=str(CATEGORY), questions=["Was ist los?", "  ", "Seit wann?"]
        ),
    )

    assert added.status_code == 201
    created = added.json()["categories"][0]
    assert created["support_role_ids"] == ["5"]
    assert created["parent_id"] == str(CATEGORY)
    assert created["questions"] == ["Was ist los?", "Seit wann?"]
    removed = await api.client.delete(
        f"{TICKETS}/{panel['id']}/categories/{created['id']}", headers=CSRF
    )
    assert removed.json()["categories"] == []


async def test_kategorienamen_mit_schraegstrich_funktionieren(api: Api) -> None:
    await login(api)
    panel = await ticket_panel(api)
    added = await post(api, f"{TICKETS}/{panel['id']}/categories", category(name="Bugs/Feedback"))
    cid = added.json()["categories"][0]["id"]

    role = await post(api, f"{TICKETS}/{panel['id']}/categories/{cid}/roles", {"role_id": str(7)})
    removed = await api.client.delete(f"{TICKETS}/{panel['id']}/categories/{cid}", headers=CSRF)

    assert role.status_code == 201
    assert removed.status_code == 200


@pytest.mark.parametrize(
    "extra",
    [
        {"support_role_id": str(GUILD_A)},  # @everyone
        {"support_role_id": "999"},  # unbekannt
        {"parent_id": str(BIG_CHANNEL)},  # kein Kanal-Container
        {"parent_id": "999"},
        {"emoji": "kein emoji"},
        {"questions": ["a", "b", "c", "d", "e", "f"]},
        {"questions": ["x" * 46]},
        {"questions": ["a;b"]},
        {"welcome_message": "Hallo {unbekannt}"},
    ],
)
async def test_ungueltige_kategorien_werden_abgelehnt(api: Api, extra: dict[str, Any]) -> None:
    await login(api)
    panel = await ticket_panel(api)

    response = await post(api, f"{TICKETS}/{panel['id']}/categories", category(**extra))

    assert response.status_code == 422, extra
    assert (await api.client.get(TICKETS)).json()[0]["categories"] == []


async def test_support_rollen_hinzufuegen_entfernen_und_letzte_behalten(api: Api) -> None:
    await login(api)
    panel = await ticket_panel(api)
    cid = (await post(api, f"{TICKETS}/{panel['id']}/categories", category())).json()["categories"][
        0
    ]["id"]
    base = f"{TICKETS}/{panel['id']}/categories/{cid}/roles"

    added = await post(api, base, {"role_id": "7"})
    assert added.json()["categories"][0]["support_role_ids"] == ["5", "7"]
    assert (await post(api, base, {"role_id": str(GUILD_A)})).status_code == 422
    assert (await api.client.delete(f"{base}/7", headers=CSRF)).status_code == 200
    assert (await api.client.delete(f"{base}/5", headers=CSRF)).status_code == 422  # die letzte


async def test_unbekannte_kategorie_ist_422(api: Api) -> None:
    await login(api)
    panel = await ticket_panel(api)

    response = await api.client.delete(f"{TICKETS}/{panel['id']}/categories/99999", headers=CSRF)

    assert response.status_code == 422


async def test_ticket_panel_veroeffentlichen_und_aktualisieren(api: Api) -> None:
    await login(api)
    panel = await ticket_panel(api)
    url = f"{TICKETS}/{panel['id']}"
    assert (await post(api, f"{url}/publish", {"channel_id": str(BIG_CHANNEL)})).status_code == 422
    await post(api, f"{url}/categories", category())
    pubsub = await listen(api)

    assert (await post(api, f"{url}/publish", {"channel_id": str(VOICE)})).status_code == 422
    ok = await post(api, f"{url}/publish", {"channel_id": str(BIG_CHANNEL)})

    assert ok.status_code == 202
    event = await next_event(pubsub)
    assert event is not None
    assert (event["kind"], event["action"], event["channel_id"]) == (
        "ticket",
        "publish",
        BIG_CHANNEL,
    )

    await api.app.state.tickets.set_panel_message(GUILD_A, int(panel["id"]), BIG_CHANNEL, 900)
    await api.client.put(url, json={"title": "Neu"}, headers=CSRF)
    event = await next_event(pubsub)
    assert event is not None
    assert (event["kind"], event["action"]) == ("ticket", "refresh")
    await pubsub.aclose()


async def test_ticket_panel_loeschen_beauftragt_den_bot(api: Api) -> None:
    await login(api)
    panel = await ticket_panel(api)
    await api.app.state.tickets.set_panel_message(GUILD_A, int(panel["id"]), BIG_CHANNEL, 900)
    pubsub = await listen(api)

    response = await api.client.delete(f"{TICKETS}/{panel['id']}", headers=CSRF)

    assert response.status_code == 204
    event = await next_event(pubsub)
    assert event is not None
    assert (event["kind"], event["action"], event["message_id"]) == ("ticket", "delete", 900)
    await pubsub.aclose()


async def test_ticket_panel_status(api: Api) -> None:
    await login(api)
    panel = await ticket_panel(api)
    url = f"{TICKETS}/{panel['id']}/status"
    assert (await api.client.get(url)).json()["state"] == "none"

    await set_status(api.redis, "ticket", GUILD_A, int(panel["id"]), PanelStatus(state="done"))

    assert (await api.client.get(url)).json()["state"] == "done"


@pytest.mark.parametrize(
    "path",
    [
        f"{ROLES}/99999999999999999999999/status",
        f"{ROLES}/99999999999999999999999/items/5",
        f"{ROLES}/1/items/99999999999999999999999",
        f"{TICKETS}/1/categories/99999999999999999999999",
        f"{TICKETS}/99999999999999999999999/status",
        f"/guilds/{GUILD_A}/audit?before=99999999999999999999999",
    ],
)
async def test_zu_grosse_ids_geben_422_statt_500(api: Api, admin: None, path: str) -> None:
    await login(api)

    response = await api.client.request(
        "DELETE" if "/items/" in path or "/categories/" in path else "GET", path, headers=CSRF
    )

    assert response.status_code == 422, path


async def test_zu_grosse_id_im_koerper_ist_422(api: Api, admin: None) -> None:
    await login(api)
    panel = await role_panel(api)

    response = await post(
        api, f"{ROLES}/{panel['id']}/items", {"role_id": "99999999999999999999999", "label": "x"}
    )

    assert response.status_code == 422
