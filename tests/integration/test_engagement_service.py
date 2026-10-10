import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from ggbot.core.db.engine import Database
from ggbot.core.errors import UserError
from ggbot.core.scheduler import Scheduler
from ggbot.core.services.engagement import (
    GIVEAWAY_JOB,
    MAX_COMMANDS,
    MAX_REMINDERS,
    REMINDER_JOB,
    EngagementService,
)

G = 100
U = 500


@pytest.fixture
def svc(db: Database) -> EngagementService:
    return EngagementService(db, Scheduler(db))


async def jobs(db: Database) -> list[tuple[str, dict[str, object], datetime]]:
    async with db.session() as session:
        rows = await session.execute(
            text("SELECT kind, payload, run_at FROM scheduled_jobs ORDER BY id")
        )
        return [(r.kind, r.payload, r.run_at) for r in rows]


# --- Eigene Befehle ---


async def test_befehl_anlegen_lesen_zaehlen(svc: EngagementService) -> None:
    view = await svc.add_command(
        G,
        "  Regeln ",
        "Sei nett, {user}!",
        U,
        ephemeral=True,
        required_role_id=9,
        cooldown_seconds=30,
    )
    assert (view.name, view.ephemeral, view.required_role_id, view.cooldown_seconds, view.uses) == (
        "regeln",
        True,
        9,
        30,
        0,
    )
    assert (await svc.get_command(G, "REGELN")).response == "Sei nett, {user}!"
    assert (await svc.use_command(G, "regeln")).uses == 1
    assert (await svc.use_command(G, "Regeln")).uses == 2
    assert [c.name for c in await svc.list_commands(G)] == ["regeln"]
    assert await svc.list_commands(G + 1) == []


async def test_parallele_aufrufe_zaehlen_alle(svc: EngagementService) -> None:
    await svc.add_command(G, "zaehler", "{uses}", U)
    results = await asyncio.gather(*(svc.use_command(G, "zaehler") for _ in range(12)))
    assert sorted(r.uses for r in results) == list(range(1, 13))  # kein Aufruf geht verloren


@pytest.mark.parametrize(
    ("name", "response", "cooldown", "key"),
    [
        ("", "x", 5, "error.cc_name"),
        ("hat leerzeichen", "x", 5, "error.cc_name"),
        ("ä", "x", 5, "error.cc_name"),
        ("a" * 33, "x", 5, "error.cc_name"),
        ("na\nme", "x", 5, "error.cc_name"),
        ("../x", "x", 5, "error.cc_name"),
        ("ok", "x", -1, "error.cc_cooldown"),
        ("ok", "x", 3601, "error.cc_cooldown"),
        ("ok", "   ", 5, "error.cc_empty"),
        ("ok", "{unbekannt}", 5, "error.cc_invalid"),
        ("ok", "{user.__class__}", 5, "error.cc_invalid"),
        ("ok", "offen {", 5, "error.cc_invalid"),
    ],
)
async def test_ungueltiger_befehl(
    svc: EngagementService, name: str, response: str, cooldown: int, key: str
) -> None:
    with pytest.raises(UserError) as exc:
        await svc.add_command(G, name, response, U, cooldown_seconds=cooldown)
    assert exc.value.key == key
    assert await svc.list_commands(G) == []


async def test_doppelt_und_grenze(svc: EngagementService) -> None:
    await svc.add_command(G, "eins", "a", U)
    with pytest.raises(UserError) as exc:
        await svc.add_command(G, "EINS", "b", U)
    assert exc.value.key == "error.cc_exists"
    await svc.add_command(G + 1, "eins", "a", U)  # anderer Server darf denselben Namen
    for i in range(MAX_COMMANDS - 1):
        await svc.add_command(G, f"cmd{i}", "a", U)
    with pytest.raises(UserError) as exc:
        await svc.add_command(G, "zuviel", "a", U)
    assert exc.value.key == "error.cc_limit"


async def test_befehl_aendern(svc: EngagementService) -> None:
    await svc.add_command(G, "x", "alt", U, required_role_id=9)
    view = await svc.edit_command(G, "X", response="neu {name}", ephemeral=True, cooldown_seconds=0)
    assert (view.response, view.ephemeral, view.cooldown_seconds, view.required_role_id) == (
        "neu {name}",
        True,
        0,
        9,
    )
    assert (await svc.edit_command(G, "x", required_role_id=7)).required_role_id == 7
    assert (await svc.edit_command(G, "x", clear_role=True)).required_role_id is None
    with pytest.raises(UserError) as exc:
        await svc.edit_command(G, "x", response="{kaputt}")
    assert exc.value.key == "error.cc_invalid"
    assert (await svc.get_command(G, "x")).response == "neu {name}"  # unverändert
    with pytest.raises(UserError) as exc:
        await svc.edit_command(G, "x", cooldown_seconds=99999)
    assert exc.value.key == "error.cc_cooldown"


async def test_befehl_fremder_server_und_loeschen(svc: EngagementService) -> None:
    await svc.add_command(G + 1, "geheim", "x", U)
    for call in (
        svc.get_command(G, "geheim"),
        svc.use_command(G, "geheim"),
        svc.remove_command(G, "geheim"),
        svc.edit_command(G, "geheim", response="y"),
    ):
        with pytest.raises(UserError) as exc:
            await call
        assert exc.value.key == "error.cc_unknown"
    assert (await svc.get_command(G + 1, "geheim")).uses == 0
    await svc.remove_command(G + 1, "geheim")
    assert await svc.list_commands(G + 1) == []


# --- Giveaways ---


async def start(svc: EngagementService, **kw: object) -> int:
    args = {
        "channel_id": 7,
        "prize": "Preis",
        "winner_count": 1,
        "host_id": U,
        "duration": timedelta(hours=1),
    } | kw
    return (await svc.create_giveaway(G, **args)).id  # type: ignore[arg-type]


async def test_giveaway_anlegen_plant_das_ende(svc: EngagementService, db: Database) -> None:
    before = datetime.now(UTC)
    giveaway_id = await start(svc, required_role_id=9, winner_count=3)
    view = await svc.get_giveaway(G, giveaway_id)
    assert (view.prize, view.winner_count, view.required_role_id, view.ended, view.winners) == (
        "Preis",
        3,
        9,
        False,
        (),
    )
    assert before + timedelta(minutes=59) < view.ends_at < before + timedelta(minutes=61)
    [(kind, payload, run_at)] = await jobs(db)
    assert (kind, payload, run_at) == (GIVEAWAY_JOB, {"giveaway_id": giveaway_id}, view.ends_at)


@pytest.mark.parametrize(
    ("kwargs", "key"),
    [
        ({"prize": " "}, "error.gw_prize"),
        ({"prize": "x" * 201}, "error.gw_prize"),
        ({"winner_count": 0}, "error.gw_winners"),
        ({"winner_count": 21}, "error.gw_winners"),
        ({"duration": timedelta(seconds=59)}, "error.gw_duration"),
        ({"duration": timedelta(days=61)}, "error.gw_duration"),
    ],
)
async def test_ungueltiges_giveaway(
    svc: EngagementService, db: Database, kwargs: dict[str, object], key: str
) -> None:
    with pytest.raises(UserError) as exc:
        await start(svc, **kwargs)
    assert exc.value.key == key
    assert await jobs(db) == []  # nichts geplant


async def test_giveaway_grenze(svc: EngagementService) -> None:
    for _ in range(10):
        await start(svc)
    with pytest.raises(UserError) as exc:
        await start(svc)
    assert exc.value.key == "error.gw_limit"


async def test_teilnahme_umschalten(svc: EngagementService) -> None:
    giveaway_id = await start(svc)
    assert await svc.toggle_entry(G, giveaway_id, 1) == (True, 1)
    assert await svc.toggle_entry(G, giveaway_id, 2) == (True, 2)
    assert await svc.toggle_entry(G, giveaway_id, 1) == (False, 1)  # zurückgezogen
    assert await svc.toggle_entry(G, giveaway_id, 1) == (True, 2)


async def test_parallele_teilnahmen_zaehlen_richtig(svc: EngagementService) -> None:
    giveaway_id = await start(svc)
    await asyncio.gather(*(svc.toggle_entry(G, giveaway_id, u) for u in range(1, 21)))
    _, entrants = await svc.end_giveaway(G, giveaway_id)
    assert entrants == list(range(1, 21))


async def test_teilnahme_nach_ende_oder_fremd(svc: EngagementService) -> None:
    giveaway_id = await start(svc)
    with pytest.raises(UserError) as exc:
        await svc.toggle_entry(G, giveaway_id, 1, now=datetime.now(UTC) + timedelta(hours=2))
    assert exc.value.key == "error.gw_over"
    with pytest.raises(UserError) as exc:
        await svc.toggle_entry(G + 1, giveaway_id, 1)
    assert exc.value.key == "error.gw_unknown"
    await svc.end_giveaway(G, giveaway_id)
    with pytest.raises(UserError) as exc:
        await svc.toggle_entry(G, giveaway_id, 1)
    assert exc.value.key == "error.gw_over"


async def test_beenden_genau_einmal(svc: EngagementService) -> None:
    giveaway_id = await start(svc)
    await svc.toggle_entry(G, giveaway_id, 1)
    outcomes = await asyncio.gather(
        *(svc.end_giveaway(G, giveaway_id) for _ in range(6)), return_exceptions=True
    )
    assert sum(not isinstance(o, Exception) for o in outcomes) == 1
    assert all(
        isinstance(o, UserError) and o.key == "error.gw_over"
        for o in outcomes
        if isinstance(o, Exception)
    )
    with pytest.raises(UserError) as exc:
        await svc.end_giveaway(G + 1, giveaway_id)
    assert exc.value.key == "error.gw_unknown"


async def test_gewinner_und_reroll(svc: EngagementService) -> None:
    giveaway_id = await start(svc, winner_count=2)
    for u in (1, 2, 3):
        await svc.toggle_entry(G, giveaway_id, u)
    with pytest.raises(UserError) as exc:
        await svc.entrants_for_reroll(G, giveaway_id)  # läuft noch
    assert exc.value.key == "error.gw_running"
    ended, entrants = await svc.end_giveaway(G, giveaway_id)
    assert (ended.ended, entrants) == (True, [1, 2, 3])
    await svc.set_winners(G, giveaway_id, [1, 2])
    view, entrants2 = await svc.entrants_for_reroll(G, giveaway_id)
    assert (view.winners, entrants2) == ((1, 2), [1, 2, 3])
    with pytest.raises(UserError):
        await svc.entrants_for_reroll(G + 1, giveaway_id)


async def test_giveaway_loeschen_nimmt_teilnahmen_mit(svc: EngagementService, db: Database) -> None:
    giveaway_id = await start(svc)
    await svc.toggle_entry(G, giveaway_id, 1)
    with pytest.raises(UserError):
        await svc.delete_giveaway(G + 1, giveaway_id)
    await svc.delete_giveaway(G, giveaway_id)
    async with db.session() as session:
        assert (await session.execute(text("SELECT count(*) FROM giveaway_entries"))).scalar() == 0
    assert await svc.active_giveaways(G) == []


async def test_aktive_giveaways_nach_ende_sortiert(svc: EngagementService) -> None:
    late = await start(svc, duration=timedelta(hours=5))
    soon = await start(svc, duration=timedelta(hours=1))
    done = await start(svc, duration=timedelta(hours=2))
    await svc.end_giveaway(G, done)
    assert [g.id for g in await svc.active_giveaways(G)] == [soon, late]
    assert await svc.active_giveaways(G + 1) == []


# --- Erinnerungen ---


async def test_erinnerung_anlegen_planen_ausliefern(svc: EngagementService, db: Database) -> None:
    view = await svc.create_reminder(G, U, 7, "  Müll rausbringen ", timedelta(hours=2))
    assert (view.text, view.channel_id, view.user_id) == ("Müll rausbringen", 7, U)
    [(kind, payload, run_at)] = await jobs(db)
    assert (kind, payload, run_at) == (REMINDER_JOB, {"reminder_id": view.id}, view.due_at)
    assert [r.id for r in await svc.pending_reminders(G, U)] == [view.id]
    fired = await svc.fire_reminder(G, view.id)
    assert fired is not None
    assert fired.text == "Müll rausbringen"
    assert await svc.fire_reminder(G, view.id) is None  # nur einmal
    assert await svc.pending_reminders(G, U) == []


async def test_gleichzeitiges_ausliefern_genau_einmal(svc: EngagementService) -> None:
    view = await svc.create_reminder(G, U, None, "x", timedelta(minutes=5))
    results = await asyncio.gather(*(svc.fire_reminder(G, view.id) for _ in range(6)))
    assert sum(r is not None for r in results) == 1


async def test_abbrechen(svc: EngagementService) -> None:
    mine = await svc.create_reminder(G, U, 7, "meins", timedelta(hours=1))
    other = await svc.create_reminder(G, U + 1, 7, "fremd", timedelta(hours=1))
    for who, rid in ((U + 1, mine.id), (U, other.id), (U, 99999)):
        with pytest.raises(UserError) as exc:
            await svc.cancel_reminder(G, who, rid)
        assert exc.value.key == "error.rm_unknown"
    with pytest.raises(UserError):
        await svc.cancel_reminder(G + 1, U, mine.id)
    await svc.cancel_reminder(G, U, mine.id)
    assert await svc.fire_reminder(G, mine.id) is None  # der geplante Job findet nichts mehr
    with pytest.raises(UserError):
        await svc.cancel_reminder(G, U, mine.id)  # zweimal abbrechen geht nicht
    assert [r.id for r in await svc.pending_reminders(G, U + 1)] == [other.id]


@pytest.mark.parametrize(
    ("text_", "delay", "key"),
    [
        (" ", timedelta(hours=1), "error.rm_text"),
        ("x" * 501, timedelta(hours=1), "error.rm_text"),
        ("x", timedelta(seconds=59), "error.rm_delay"),
        ("x", timedelta(days=366), "error.rm_delay"),
    ],
)
async def test_ungueltige_erinnerung(
    svc: EngagementService, db: Database, text_: str, delay: timedelta, key: str
) -> None:
    with pytest.raises(UserError) as exc:
        await svc.create_reminder(G, U, 7, text_, delay)
    assert exc.value.key == key
    assert await jobs(db) == []


async def test_erinnerungs_grenze_pro_nutzer(svc: EngagementService) -> None:
    for _ in range(MAX_REMINDERS):
        await svc.create_reminder(G, U, 7, "x", timedelta(hours=1))
    with pytest.raises(UserError) as exc:
        await svc.create_reminder(G, U, 7, "x", timedelta(hours=1))
    assert exc.value.key == "error.rm_limit"
    await svc.create_reminder(G, U + 1, 7, "x", timedelta(hours=1))  # andere Person
    await svc.create_reminder(G + 1, U, 7, "x", timedelta(hours=1))  # anderer Server
