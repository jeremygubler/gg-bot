import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from redis.asyncio import Redis
from sqlalchemy import text

from ggbot.core.db.engine import Database
from ggbot.core.errors import UserError
from ggbot.core.services.tickets import (
    MAX_CATEGORIES,
    MAX_PANELS,
    MAX_SUPPORT_ROLES,
    TicketService,
    TicketView,
)
from ggbot.core.settings.store import SettingsStore
from ggbot.core.settings.tickets import TicketSettings

G = 100
USER = 500


@pytest.fixture
def svc(db: Database, redis: Redis) -> TicketService:
    return TicketService(db, SettingsStore(db, redis), redis)


async def category(
    svc: TicketService, guild: int = G, name: str = "Hilfe", **kw: object
) -> tuple[int, int]:
    panel = await svc.create_panel(guild, title="Support")
    view = await svc.add_category(guild, panel.id, name=name, support_role_id=9, **kw)  # type: ignore[arg-type]
    return panel.id, view.categories[0].id


async def open_ticket(
    svc: TicketService,
    category_id: int,
    *,
    guild: int = G,
    user: int = USER,
    channel: int | None = None,
) -> TicketView:
    reservation = await svc.reserve(guild, user, category_id)
    return await svc.register(
        guild,
        number=reservation.number,
        creator_id=user,
        channel_id=channel or guild * 100_000 + reservation.number,
        category=reservation.category,
        answers=[("Frage", "Antwort")],
    )


async def set_settings(svc: TicketService, db: Database, redis: Redis, **kw: object) -> None:
    await SettingsStore(db, redis).save(G, TicketSettings.model_validate(kw))
    svc.invalidate(G)


# --- Panels und Kategorien ---


async def test_panel_anlegen_und_pruefen(svc: TicketService) -> None:
    view = await svc.create_panel(G, title="  Support  ", description="Hilfe")
    assert (view.title, view.description, view.published) == ("Support", "Hilfe", False)
    assert [p.id for p in await svc.list_panels(G)] == [view.id]
    assert await svc.list_panels(G + 1) == []
    for kwargs, key in (
        ({"title": " "}, "error.tpanel_title"),
        ({"title": "x" * 257}, "error.tpanel_title"),
        ({"title": "t", "description": "x" * 1001}, "error.tpanel_description"),
    ):
        with pytest.raises(UserError) as exc:
            await svc.create_panel(G, **kwargs)
        assert exc.value.key == key


async def test_panel_limit_pro_server(svc: TicketService) -> None:
    for _ in range(MAX_PANELS):
        await svc.create_panel(G, title="p")
    with pytest.raises(UserError) as exc:
        await svc.create_panel(G, title="p")
    assert exc.value.key == "error.tpanel_limit"
    await svc.create_panel(G + 1, title="p")


async def test_fremde_panels_sind_nicht_erreichbar(svc: TicketService) -> None:
    other = await svc.create_panel(G + 1, title="x")
    for call in (
        svc.get_panel(G, other.id),
        svc.delete_panel(G, other.id),
        svc.set_panel_message(G, other.id, 1, 2),
        svc.add_category(G, other.id, name="a", support_role_id=1),
        svc.remove_category(G, other.id, "a"),
        svc.change_support_role(G, other.id, "a", 1, add=True),
    ):
        with pytest.raises(UserError) as exc:
            await call
        assert exc.value.key == "error.tpanel_not_found"
    assert (await svc.get_panel(G + 1, other.id)).title == "x"


async def test_kategorien(svc: TicketService) -> None:
    panel = await svc.create_panel(G, title="p")
    view = await svc.add_category(
        G, panel.id, name="Hilfe", support_role_id=9, emoji="🛟", description="d",
        parent_id=77, welcome_message="Hi {user} ({category} #{number})", questions=["Was?", "Wo?"],
    )  # fmt: skip
    cat = view.categories[0]
    assert (cat.name, cat.emoji, cat.parent_id, cat.support_role_ids, cat.questions) == (
        "Hilfe",
        "🛟",
        77,
        (9,),
        ("Was?", "Wo?"),
    )
    view = await svc.add_category(G, panel.id, name="Bug", support_role_id=8)
    assert [c.name for c in view.categories] == ["Hilfe", "Bug"]  # Reihenfolge bleibt
    assert view.category_by_name("hILFE") is not None
    with pytest.raises(UserError) as exc:
        await svc.add_category(G, panel.id, name="hilfe", support_role_id=9)
    assert exc.value.key == "error.tcat_duplicate"
    view = await svc.remove_category(G, panel.id, "BUG")
    assert [c.name for c in view.categories] == ["Hilfe"]
    with pytest.raises(UserError) as exc:
        await svc.remove_category(G, panel.id, "Bug")
    assert exc.value.key == "error.tcat_not_found"


@pytest.mark.parametrize(
    ("kwargs", "key"),
    [
        ({"name": " "}, "error.tcat_name"),
        ({"name": "x" * 81}, "error.tcat_name"),
        ({"name": "a", "description": "x" * 101}, "error.tcat_description"),
        ({"name": "a", "welcome_message": "x" * 1001}, "error.tcat_welcome"),
        ({"name": "a", "welcome_message": "{level}"}, "error.tcat_welcome_invalid"),
        ({"name": "a", "welcome_message": "{user.__class__}"}, "error.tcat_welcome_invalid"),
    ],
)
async def test_ungueltige_kategorie(
    svc: TicketService, kwargs: dict[str, object], key: str
) -> None:
    panel = await svc.create_panel(G, title="p")
    with pytest.raises(UserError) as exc:
        await svc.add_category(G, panel.id, support_role_id=9, **kwargs)  # type: ignore[arg-type]
    assert exc.value.key == key
    assert (await svc.get_panel(G, panel.id)).categories == ()


async def test_kategorie_limit(svc: TicketService) -> None:
    panel = await svc.create_panel(G, title="p")
    for i in range(MAX_CATEGORIES):
        await svc.add_category(G, panel.id, name=f"k{i}", support_role_id=9)
    with pytest.raises(UserError) as exc:
        await svc.add_category(G, panel.id, name="zu viel", support_role_id=9)
    assert exc.value.key == "error.tcat_limit"


async def test_support_rollen(svc: TicketService) -> None:
    panel_id, _ = await category(svc)
    view = await svc.change_support_role(G, panel_id, "hilfe", 10, add=True)
    assert view.categories[0].support_role_ids == (9, 10)
    with pytest.raises(UserError) as exc:
        await svc.change_support_role(G, panel_id, "Hilfe", 10, add=True)
    assert exc.value.key == "error.tcat_role_duplicate"
    view = await svc.change_support_role(G, panel_id, "Hilfe", 9, add=False)
    assert view.categories[0].support_role_ids == (10,)
    with pytest.raises(UserError) as exc:
        await svc.change_support_role(G, panel_id, "Hilfe", 10, add=False)  # die letzte
    assert exc.value.key == "error.tcat_last_role"
    with pytest.raises(UserError) as exc:
        await svc.change_support_role(G, panel_id, "Hilfe", 77, add=False)
    assert exc.value.key == "error.tcat_role_missing"
    for r in range(11, 9 + MAX_SUPPORT_ROLES + 1):  # bis die Grenze erreicht ist
        await svc.change_support_role(G, panel_id, "Hilfe", r, add=True)
    with pytest.raises(UserError) as exc:
        await svc.change_support_role(G, panel_id, "Hilfe", 999, add=True)
    assert exc.value.key == "error.tcat_roles_limit"


async def test_panel_nachricht(svc: TicketService) -> None:
    panel_id, _ = await category(svc)
    await svc.set_panel_message(G, panel_id, 7, 555)
    view = await svc.get_panel(G, panel_id)
    assert (view.channel_id, view.message_id, view.published) == (7, 555, True)
    assert await svc.panel_message_deleted(G + 1, 555) == 0
    assert await svc.panel_message_deleted(G, 555) == 1
    assert (await svc.get_panel(G, panel_id)).published is False


async def test_panel_loeschen_laesst_tickets_bestehen(svc: TicketService) -> None:
    panel_id, cat_id = await category(svc)
    ticket = await open_ticket(svc, cat_id)
    await svc.delete_panel(G, panel_id)
    kept = await svc.by_channel(G, ticket.channel_id)
    assert kept is not None
    assert kept.category_name == "Hilfe"
    assert kept.support_role_ids == (9,)  # Rollen stehen im Ticket, nicht nur in der Kategorie
    assert kept.category_id is None
    with pytest.raises(UserError) as exc:
        await svc.get_category(G, cat_id)
    assert exc.value.key == "error.tcat_gone"


async def test_kategorie_fremder_server(svc: TicketService) -> None:
    _, cat_id = await category(svc)
    with pytest.raises(UserError):
        await svc.get_category(G + 1, cat_id)
    with pytest.raises(UserError):
        await svc.reserve(G + 1, USER, cat_id)


# --- Tickets ---


async def test_nummern_pro_server(svc: TicketService) -> None:
    _, c1 = await category(svc)
    _, c2 = await category(svc, guild=G + 1)
    a, b = await open_ticket(svc, c1), await open_ticket(svc, c1, user=USER + 1)
    other = await open_ticket(svc, c2, guild=G + 1)
    assert (a.number, b.number, other.number) == (1, 2, 1)
    assert (await svc.by_number(G, 2)).creator_id == USER + 1
    with pytest.raises(UserError) as exc:
        await svc.by_number(G, 3)
    assert exc.value.key == "error.ticket_unknown"
    with pytest.raises(UserError):
        await svc.by_number(G + 1, 2)  # Nummer 2 gibt es nur auf Server G


async def test_parallele_reservierungen_geben_verschiedene_nummern(svc: TicketService) -> None:
    _, cat_id = await category(svc)
    results = await asyncio.gather(*(svc.reserve(G, USER + i, cat_id) for i in range(10)))
    assert sorted(r.number for r in results) == list(range(1, 11))


async def test_grenze_offener_tickets(svc: TicketService, db: Database, redis: Redis) -> None:
    await set_settings(svc, db, redis, max_open_per_user=2)
    _, cat_id = await category(svc)
    first = await open_ticket(svc, cat_id)
    await open_ticket(svc, cat_id)
    with pytest.raises(UserError) as exc:
        await svc.reserve(G, USER, cat_id)
    assert exc.value.key == "error.ticket_limit"
    assert exc.value.params["limit"] == 2
    await open_ticket(svc, cat_id, user=USER + 1)  # andere Person
    await svc.close(G, first.channel_id, closed_by=1, reason=None, html="<p>x</p>", message_count=1)
    await open_ticket(svc, cat_id)  # geschlossene zählen nicht mehr


async def test_grenze_haelt_bei_gleichzeitigen_klicks(
    svc: TicketService, db: Database, redis: Redis
) -> None:
    await set_settings(svc, db, redis, max_open_per_user=1)
    _, cat_id = await category(svc)
    reservations = [
        await svc.reserve(G, USER, cat_id) for _ in range(5)
    ]  # alle vor dem ersten Eintrag
    outcomes = await asyncio.gather(
        *(
            svc.register(
                G,
                number=r.number,
                creator_id=USER,
                channel_id=2000 + r.number,
                category=r.category,
                answers=[],
            )
            for r in reservations
        ),
        return_exceptions=True,
    )
    assert sum(not isinstance(o, Exception) for o in outcomes) == 1
    assert all(
        isinstance(o, UserError) and o.key == "error.ticket_limit"
        for o in outcomes
        if isinstance(o, Exception)
    )
    assert len(await svc.open_tickets(G)) == 1


async def test_ticket_felder_und_antworten(svc: TicketService) -> None:
    _, cat_id = await category(svc)
    ticket = await open_ticket(svc, cat_id, channel=4242)
    assert (ticket.status, ticket.is_open, ticket.claimed_by) == ("open", True, None)
    assert ticket.answers == (("Frage", "Antwort"),)
    assert ticket.category_name == "Hilfe"
    assert (await svc.require_channel(G, 4242)).id == ticket.id
    assert await svc.by_channel(G + 1, 4242) is None  # anderer Server
    with pytest.raises(UserError) as exc:
        await svc.require_channel(G, 1)
    assert exc.value.key == "error.ticket_not_here"


async def test_doppelter_kanal_wird_abgelehnt(svc: TicketService) -> None:
    _, cat_id = await category(svc)
    await open_ticket(svc, cat_id, channel=4242)
    with pytest.raises(Exception):  # noqa: B017,PT011 - Unique-Verletzung
        await open_ticket(svc, cat_id, user=USER + 1, channel=4242)


# --- Übernehmen ---


async def test_uebernehmen(svc: TicketService) -> None:
    _, cat_id = await category(svc)
    ticket = await open_ticket(svc, cat_id)
    claimed = await svc.claim(G, ticket.channel_id, 7)
    assert claimed.claimed_by == 7
    with pytest.raises(UserError) as exc:
        await svc.claim(G, ticket.channel_id, 7)
    assert exc.value.key == "error.ticket_claimed_by_you"
    with pytest.raises(UserError) as exc:
        await svc.claim(G, ticket.channel_id, 8)
    assert exc.value.key == "error.ticket_claimed"
    assert exc.value.params["user"] == "<@7>"


async def test_gleichzeitiges_uebernehmen_gewinnt_genau_einer(svc: TicketService) -> None:
    _, cat_id = await category(svc)
    ticket = await open_ticket(svc, cat_id)
    outcomes = await asyncio.gather(
        *(svc.claim(G, ticket.channel_id, 10 + i) for i in range(8)), return_exceptions=True
    )
    assert sum(not isinstance(o, Exception) for o in outcomes) == 1
    assert all(isinstance(o, UserError) for o in outcomes if isinstance(o, Exception))


async def test_abgeben(svc: TicketService) -> None:
    _, cat_id = await category(svc)
    ticket = await open_ticket(svc, cat_id)
    with pytest.raises(UserError) as exc:
        await svc.unclaim(G, ticket.channel_id, 7, is_manager=False)
    assert exc.value.key == "error.ticket_not_claimed"
    await svc.claim(G, ticket.channel_id, 7)
    with pytest.raises(UserError) as exc:
        await svc.unclaim(G, ticket.channel_id, 8, is_manager=False)  # fremdes Ticket
    assert exc.value.key == "error.ticket_claimed"
    assert (
        await svc.unclaim(G, ticket.channel_id, 8, is_manager=True)
    ).claimed_by is None  # Verwalter darf
    await svc.claim(G, ticket.channel_id, 7)
    assert (await svc.unclaim(G, ticket.channel_id, 7, is_manager=False)).claimed_by is None


async def test_geschlossenes_ticket_laesst_sich_nicht_uebernehmen(svc: TicketService) -> None:
    _, cat_id = await category(svc)
    ticket = await open_ticket(svc, cat_id)
    await svc.close(G, ticket.channel_id, closed_by=1, reason=None, html="h", message_count=0)
    with pytest.raises(UserError) as exc:
        await svc.claim(G, ticket.channel_id, 7)
    assert exc.value.key == "error.ticket_closed"
    with pytest.raises(UserError) as exc:
        await svc.unclaim(G, ticket.channel_id, 7, is_manager=True)
    assert exc.value.key == "error.ticket_closed"


# --- Schliessen und Transkript ---


async def test_schliessen_speichert_transkript_atomar(svc: TicketService, db: Database) -> None:
    _, cat_id = await category(svc)
    ticket = await open_ticket(svc, cat_id)
    closed = await svc.close(
        G, ticket.channel_id, closed_by=7, reason="erledigt", html="<p>t</p>", message_count=3
    )
    assert (closed.status, closed.closed_by, closed.close_reason) == ("closed", 7, "erledigt")
    assert closed.closed_at is not None
    found, html = await svc.transcript(G, ticket.number)
    assert (found.id, html) == (ticket.id, "<p>t</p>")
    assert await svc.open_tickets(G) == []


async def test_zweites_schliessen_scheitert_ohne_zweites_transkript(
    svc: TicketService, db: Database
) -> None:
    _, cat_id = await category(svc)
    ticket = await open_ticket(svc, cat_id)
    await svc.close(G, ticket.channel_id, closed_by=7, reason=None, html="erstes", message_count=1)
    with pytest.raises(UserError) as exc:
        await svc.close(
            G, ticket.channel_id, closed_by=8, reason="zu spät", html="zweites", message_count=1
        )
    assert exc.value.key == "error.ticket_closed"
    _, html = await svc.transcript(G, ticket.number)
    assert html == "erstes"
    closed = await svc.by_channel(G, ticket.channel_id)
    assert closed is not None
    assert closed.closed_by == 7  # der erste Schliesser bleibt


async def test_gleichzeitiges_schliessen_genau_einmal(svc: TicketService) -> None:
    _, cat_id = await category(svc)
    ticket = await open_ticket(svc, cat_id)
    outcomes = await asyncio.gather(
        *(
            svc.close(G, ticket.channel_id, closed_by=i, reason=None, html=f"h{i}", message_count=0)
            for i in range(6)
        ),
        return_exceptions=True,
    )
    assert sum(not isinstance(o, Exception) for o in outcomes) == 1


async def test_schliessen_fehler(svc: TicketService) -> None:
    _, cat_id = await category(svc)
    ticket = await open_ticket(svc, cat_id)
    with pytest.raises(UserError) as exc:
        await svc.close(G, 999999, closed_by=1, reason=None, html="h", message_count=0)
    assert exc.value.key == "error.ticket_not_here"
    with pytest.raises(UserError) as exc:
        await svc.close(
            G + 1, ticket.channel_id, closed_by=1, reason=None, html="h", message_count=0
        )
    assert exc.value.key == "error.ticket_not_here"  # anderer Server
    with pytest.raises(UserError) as exc:
        await svc.close(
            G, ticket.channel_id, closed_by=1, reason="x" * 301, html="h", message_count=0
        )
    assert exc.value.key == "error.ticket_reason"
    assert (await svc.require_channel(G, ticket.channel_id)).is_open  # nichts wurde geändert


async def test_transkript_nur_im_eigenen_server(svc: TicketService) -> None:
    _, cat_id = await category(svc)
    ticket = await open_ticket(svc, cat_id)
    with pytest.raises(UserError) as exc:
        await svc.transcript(G, ticket.number)  # noch offen
    assert exc.value.key == "error.ticket_no_transcript"
    await svc.close(G, ticket.channel_id, closed_by=1, reason=None, html="h", message_count=0)
    with pytest.raises(UserError):
        await svc.transcript(G + 1, ticket.number)


async def test_aufbewahrung(svc: TicketService, db: Database, redis: Redis) -> None:
    _, cat_id = await category(svc)
    old, new = await open_ticket(svc, cat_id), await open_ticket(svc, cat_id, user=USER + 1)
    for t in (old, new):
        await svc.close(G, t.channel_id, closed_by=1, reason=None, html="h", message_count=0)
    async with db.session() as session:
        await session.execute(
            text("UPDATE ticket_transcripts SET created_at = :at WHERE ticket_id = :id"),
            {"at": datetime.now(UTC) - timedelta(days=40), "id": old.id},
        )
    assert await svc.purge_transcripts(G) == 0  # 0 = nie löschen
    await set_settings(svc, db, redis, retention_days=30)
    assert await svc.purge_transcripts(G + 1) == 0  # anderer Server
    assert await svc.purge_transcripts(G) == 1
    with pytest.raises(UserError):
        await svc.transcript(G, old.number)
    assert (await svc.transcript(G, new.number))[1] == "h"
    assert (await svc.by_number(G, old.number)).status == "closed"  # das Ticket selbst bleibt


# --- Bewertung ---


async def test_bewertung(svc: TicketService) -> None:
    _, cat_id = await category(svc)
    ticket = await open_ticket(svc, cat_id)
    with pytest.raises(UserError) as exc:
        await svc.rate(G, ticket.id, USER, 5)
    assert exc.value.key == "error.ticket_not_closed"
    await svc.close(G, ticket.channel_id, closed_by=1, reason=None, html="h", message_count=0)
    for rating in (0, 6, -1):
        with pytest.raises(UserError) as exc:
            await svc.rate(G, ticket.id, USER, rating)
        assert exc.value.key == "error.ticket_rating"
    with pytest.raises(UserError) as exc:
        await svc.rate(G, ticket.id, USER + 1, 5)  # nicht der Ersteller
    assert exc.value.key == "error.ticket_unknown"
    with pytest.raises(UserError):
        await svc.rate(G + 1, ticket.id, USER, 5)  # anderer Server
    await svc.rate(G, ticket.id, USER, 4)
    with pytest.raises(UserError) as exc:
        await svc.rate(G, ticket.id, USER, 5)
    assert exc.value.key == "error.ticket_rated"
    assert await svc.feedback_summary(G) == (1, 4.0)
    assert await svc.feedback_summary(G + 1) == (0, 0.0)


async def test_durchschnitt(svc: TicketService) -> None:
    _, cat_id = await category(svc)
    for i, rating in enumerate((5, 3, 4)):
        t = await open_ticket(svc, cat_id, user=USER + i)
        await svc.close(G, t.channel_id, closed_by=1, reason=None, html="h", message_count=0)
        await svc.rate(G, t.id, USER + i, rating)
    count, average = await svc.feedback_summary(G)
    assert count == 3
    assert average == pytest.approx(4.0)
