import pytest
from sqlalchemy import text

from ggbot.core.db.engine import Database
from ggbot.core.errors import UserError
from ggbot.core.services.rolepanels import MAX_ITEMS, MAX_PANELS, RolePanelService

G = 100


@pytest.fixture
def svc(db: Database) -> RolePanelService:
    return RolePanelService(db)


async def make(svc: RolePanelService, kind: str = "button", guild: int = G, **kw: object) -> int:
    panel = await svc.create(guild, kind=kind, mode="normal", title="Rollen", **kw)  # type: ignore[arg-type]
    return panel.id


async def test_anlegen_und_lesen(svc: RolePanelService) -> None:
    panel = await svc.create(
        G, kind="select", mode="unique", title="  Farbe  ", description="Wähle", channel_id=5
    )
    assert (panel.kind, panel.mode, panel.title, panel.description) == (
        "select",
        "unique",
        "Farbe",
        "Wähle",
    )
    assert (panel.channel_id, panel.message_id, panel.published, panel.external) == (
        5,
        None,
        False,
        False,
    )
    assert await svc.get(G, panel.id) == panel
    assert [p.id for p in await svc.list(G)] == [panel.id]


async def test_panels_sind_serverscoped(svc: RolePanelService) -> None:
    panel_id = await make(svc)
    other = await make(svc, guild=G + 1)
    assert [p.id for p in await svc.list(G)] == [panel_id]
    with pytest.raises(UserError) as exc:
        await svc.get(G + 1, panel_id)
    assert exc.value.key == "error.panel_not_found"
    with pytest.raises(UserError):
        await svc.add_item(G, other, role_id=1, label="x")  # fremdes Panel
    with pytest.raises(UserError):
        await svc.delete(G, other)
    await svc.delete(G + 1, other)


@pytest.mark.parametrize(
    ("kwargs", "key"),
    [
        ({"title": "  "}, "error.panel_title"),
        ({"title": "x" * 257}, "error.panel_title"),
        ({"title": "ok", "description": "x" * 1001}, "error.panel_description"),
        ({"title": "ok", "message_id": 5, "kind": "button"}, "error.panel_external_only_reactions"),
    ],
)
async def test_ungueltiges_panel(
    svc: RolePanelService, kwargs: dict[str, object], key: str
) -> None:
    data: dict[str, object] = {"kind": "button", "mode": "normal", "title": "t", **kwargs}
    with pytest.raises(UserError) as exc:
        await svc.create(G, **data)  # type: ignore[arg-type]
    assert exc.value.key == key


async def test_panel_limit(svc: RolePanelService) -> None:
    for _ in range(MAX_PANELS):
        await make(svc)
    with pytest.raises(UserError) as exc:
        await make(svc)
    assert exc.value.key == "error.panel_limit"
    await make(svc, guild=G + 1)  # anderer Server hat sein eigenes Limit


async def test_eintraege_hinzufuegen_in_reihenfolge(svc: RolePanelService) -> None:
    panel_id = await make(svc)
    await svc.add_item(G, panel_id, role_id=11, label="Eins")
    await svc.add_item(G, panel_id, role_id=12, emoji="🎮")
    panel = await svc.add_item(
        G, panel_id, role_id=13, label="Drei", emoji="<:x1:123456789012345678>"
    )
    assert [i.role_id for i in panel.items] == [11, 12, 13]
    assert [i.position for i in panel.items] == [0, 1, 2]
    assert panel.role_ids == (11, 12, 13)
    assert panel.item_by_role(12) is not None
    assert panel.item_by_role(12).emoji == "🎮"  # type: ignore[union-attr]
    assert panel.item_by_emoji("c:123456789012345678") is not None


@pytest.mark.parametrize(
    ("kind", "kwargs", "key"),
    [
        ("button", {}, "error.panel_label_needed"),
        ("select", {}, "error.panel_label_needed"),
        ("reaction", {"label": "nur Text"}, "error.panel_emoji_needed"),
        ("button", {"label": "x" * 81}, "error.panel_label"),
        ("select", {"label": "ok", "description": "x" * 101}, "error.panel_item_description"),
    ],
)
async def test_ungueltiger_eintrag(
    svc: RolePanelService, kind: str, kwargs: dict[str, object], key: str
) -> None:
    panel_id = await make(svc, kind)
    with pytest.raises(UserError) as exc:
        await svc.add_item(G, panel_id, role_id=1, **kwargs)  # type: ignore[arg-type]
    assert exc.value.key == key


async def test_doppelte_rolle_und_doppeltes_emoji(svc: RolePanelService) -> None:
    panel_id = await make(svc, "reaction")
    await svc.add_item(G, panel_id, role_id=1, emoji="✔️")
    with pytest.raises(UserError) as exc:
        await svc.add_item(G, panel_id, role_id=1, emoji="🎮")
    assert exc.value.key == "error.panel_role_duplicate"
    with pytest.raises(UserError) as exc:
        await svc.add_item(G, panel_id, role_id=2, emoji="✔")  # gleiches Emoji ohne Selektor
    assert exc.value.key == "error.panel_emoji_duplicate"
    other = await make(svc, "reaction")
    await svc.add_item(G, other, role_id=1, emoji="✔️")  # in einem anderen Panel erlaubt


async def test_mehrere_eintraege_ohne_emoji_sind_erlaubt(svc: RolePanelService) -> None:
    panel_id = await make(svc)
    await svc.add_item(G, panel_id, role_id=1, label="a")
    await svc.add_item(G, panel_id, role_id=2, label="b")
    assert len((await svc.get(G, panel_id)).items) == 2


@pytest.mark.parametrize("kind", ["button", "select", "reaction"])
async def test_grenzen_pro_art(svc: RolePanelService, kind: str) -> None:
    panel_id = await make(svc, kind)
    limit = MAX_ITEMS[kind]
    for i in range(limit):
        await svc.add_item(G, panel_id, role_id=1000 + i, label=f"r{i}", emoji=chr(0x1F600 + i))
    with pytest.raises(UserError) as exc:
        await svc.add_item(G, panel_id, role_id=9999, label="zu viel", emoji=chr(0x1F700))
    assert exc.value.key == "error.panel_items_limit"


async def test_beschreibung_nur_beim_auswahlmenue(svc: RolePanelService) -> None:
    select = await make(svc, "select")
    button = await make(svc, "button")
    s = await svc.add_item(G, select, role_id=1, label="a", description="Info")
    b = await svc.add_item(G, button, role_id=1, label="a", description="Info")
    assert s.items[0].description == "Info"
    assert b.items[0].description is None


async def test_eintrag_entfernen(svc: RolePanelService) -> None:
    panel_id = await make(svc)
    await svc.add_item(G, panel_id, role_id=1, label="a")
    await svc.add_item(G, panel_id, role_id=2, label="b")
    panel, removed = await svc.remove_item(G, panel_id, 1)
    assert [i.role_id for i in panel.items] == [2]
    assert removed.role_id == 1
    with pytest.raises(UserError) as exc:
        await svc.remove_item(G, panel_id, 1)
    assert exc.value.key == "error.panel_role_not_in_panel"
    again = await svc.add_item(G, panel_id, role_id=1, label="a")  # wieder hinzufügen geht
    assert again.items[-1].position == 2


async def test_panel_loeschen_nimmt_die_eintraege_mit(svc: RolePanelService, db: Database) -> None:
    panel_id = await make(svc)
    await svc.add_item(G, panel_id, role_id=1, label="a")
    deleted = await svc.delete(G, panel_id)
    assert len(deleted.items) == 1
    async with db.session() as session:
        count = (await session.execute(text("SELECT count(*) FROM role_panel_items"))).scalar()
    assert count == 0  # per Fremdschlüssel mit gelöscht
    with pytest.raises(UserError):
        await svc.get(G, panel_id)


async def test_nachricht_setzen_und_gelöscht_melden(svc: RolePanelService) -> None:
    panel_id = await make(svc)
    await svc.set_message(G, panel_id, 7, 555)
    panel = await svc.get(G, panel_id)
    assert (panel.channel_id, panel.message_id, panel.published) == (7, 555, True)
    assert await svc.message_deleted(G + 1, 555) == 0  # anderer Server
    assert await svc.message_deleted(G, 555) == 1
    assert (await svc.get(G, panel_id)).published is False
    assert await svc.message_deleted(G, 555) == 0
    with pytest.raises(UserError):
        await svc.set_message(G + 1, panel_id, 1, 2)


async def test_reaktions_panel_ueber_nachricht_finden_mit_cache(
    svc: RolePanelService, db: Database
) -> None:
    panel = await svc.create(
        G, kind="reaction", mode="normal", title="r", channel_id=7, message_id=555
    )
    assert panel.external is True
    found = await svc.panel_for_message(G, 555)
    assert found is not None
    assert found.id == panel.id
    assert await svc.panel_for_message(G, 556) is None
    assert await svc.panel_for_message(G + 1, 555) is None  # anderer Server

    # Der Cache verhindert eine DB-Abfrage pro Reaktion ...
    async with db.session() as session:
        await session.execute(text("UPDATE role_panels SET message_id = 999"))
    assert (await svc.panel_for_message(G, 555)) is not None  # alt, noch gecacht
    # ... bis jemand invalidiert (eigene Änderungen tun das von selbst).
    svc.invalidate(G)
    assert await svc.panel_for_message(G, 555) is None


async def test_button_panels_zaehlen_nicht_als_reaktions_panels(svc: RolePanelService) -> None:
    panel_id = await make(svc, "button")
    await svc.set_message(G, panel_id, 7, 777)
    assert await svc.panel_for_message(G, 777) is None


async def test_eigene_aenderungen_leeren_den_cache(svc: RolePanelService) -> None:
    panel = await svc.create(G, kind="reaction", mode="normal", title="r", channel_id=7)
    assert await svc.panel_for_message(G, 555) is None
    await svc.set_message(G, panel.id, 7, 555)
    assert (await svc.panel_for_message(G, 555)) is not None
