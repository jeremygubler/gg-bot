import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from redis.asyncio import Redis
from sqlalchemy import text

from ggbot.core.db.engine import Database
from ggbot.core.db.models import ScheduledJob
from ggbot.core.duration import DAY, HOUR, MAX_TEMPBAN, MAX_TIMEOUT
from ggbot.core.errors import UserError
from ggbot.core.scheduler import Scheduler
from ggbot.core.services.guilds import GuildService
from ggbot.core.services.moderation import MAX_REASON, UNBAN_JOB, ModerationService
from ggbot.core.settings.schema import EscalationRule, ModerationSettings
from ggbot.core.settings.store import SettingsStore
from tests.fakes import BOT, FakePort

T0 = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
G = 100
MOD = 5
TARGET = 42


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


@dataclass
class Env:
    svc: ModerationService
    store: SettingsStore
    db: Database
    clock: Clock
    scheduler: Scheduler
    guilds: GuildService


@pytest.fixture
async def env(db: Database, redis: Redis) -> Env:
    clock = Clock()
    guilds = GuildService(db, redis, default_locale="de")
    store = SettingsStore(db, redis)
    scheduler = Scheduler(db, clock=clock)
    svc = ModerationService(db, store, scheduler, guilds, clock=clock)
    return Env(svc, store, db, clock, scheduler, guilds)


async def jobs(db: Database) -> list[ScheduledJob]:
    async with db.session() as session:
        rows = await session.execute(text("SELECT id FROM scheduled_jobs ORDER BY id"))
        ids = [r[0] for r in rows]
        return [j for j in [await session.get(ScheduledJob, i) for i in ids] if j]


async def scalar(db: Database, sql: str) -> object:
    async with db.session() as session:
        return (await session.execute(text(sql))).scalar()


# --- Case-Nummern ---


async def test_case_nummern_zaehlen_pro_server(env: Env) -> None:
    port = FakePort()
    a1 = await env.svc.warn(port, G, MOD, TARGET, "x")
    a2 = await env.svc.warn(port, G, MOD, TARGET, "y")
    b1 = await env.svc.warn(port, G + 1, MOD, TARGET, "z")
    assert (a1.case.case_no, a2.case.case_no, b1.case.case_no) == (1, 2, 1)


async def test_parallele_aktionen_bekommen_eindeutige_nummern(env: Env) -> None:
    results = await asyncio.gather(
        *(env.svc.warn(FakePort(), G, MOD, TARGET + i, "x") for i in range(20))
    )
    assert sorted(r.case.case_no for r in results) == list(range(1, 21))


async def test_get_case_ist_nach_server_gescoped(env: Env) -> None:
    await env.svc.warn(FakePort(), G, MOD, TARGET, "x")
    assert (await env.svc.get_case(G, 1)).target_id == TARGET
    with pytest.raises(UserError) as exc:
        await env.svc.get_case(G + 1, 1)
    assert exc.value.key == "error.mod_case_not_found"


# --- DM und Fehlerpfade ---


async def test_dm_kommt_in_serversprache_mit_grund(env: Env) -> None:
    port = FakePort()
    result = await env.svc.warn(port, G, MOD, TARGET, "Spam")
    dm = next(c for c in port.calls if c[0] == "dm")
    assert dm[1] == TARGET
    assert "Testserver" in str(dm[2])
    assert "Spam" in str(dm[2])
    assert "verwarnt" in str(dm[2])  # Standardsprache de
    assert result.dm_sent is True

    await env.guilds.set_locale(G, "en")
    port = FakePort()
    await env.svc.warn(port, G, MOD, TARGET, None)
    dm = next(c for c in port.calls if c[0] == "dm")
    assert "warned" in str(dm[2])
    assert "No reason given" in str(dm[2])


async def test_dm_kann_abgeschaltet_werden(env: Env) -> None:
    await env.store.save(G, ModerationSettings(dm_on_action=False))
    port = FakePort()
    result = await env.svc.kick(port, G, MOD, TARGET, "x")
    assert "dm" not in port.names()
    assert result.dm_sent is None


async def test_fehlgeschlagene_dm_verhindert_die_aktion_nicht(env: Env) -> None:
    port = FakePort(dm_ok=False)
    result = await env.svc.kick(port, G, MOD, TARGET, "x")
    assert result.dm_sent is False
    assert result.case.type == "kick"
    assert "kick" in port.names()


async def test_discord_fehler_erzeugt_keinen_case(env: Env) -> None:
    with pytest.raises(UserError) as exc:
        await env.svc.kick(FakePort(fail_on={"kick"}), G, MOD, TARGET, "x")
    assert exc.value.key == "error.mod_forbidden"
    assert await scalar(env.db, "SELECT count(*) FROM mod_cases") == 0
    ok = await env.svc.kick(FakePort(), G, MOD, TARGET, "x")
    assert ok.case.case_no == 1  # keine Nummer verbraucht


async def test_kein_dm_an_den_bot_selbst_und_keine_dm_bei_aufhebungen(env: Env) -> None:
    port = FakePort()
    await env.svc.untimeout(port, G, MOD, TARGET, None)
    await env.svc.unban(port, G, MOD, TARGET, None)
    assert "dm" not in port.names()


async def test_grund_wird_bereinigt_und_begrenzt(env: Env) -> None:
    blank = await env.svc.warn(FakePort(), G, MOD, TARGET, "   ")
    assert blank.case.reason is None
    trimmed = await env.svc.warn(FakePort(), G, MOD, TARGET, "  Spam  ")
    assert trimmed.case.reason == "Spam"
    with pytest.raises(UserError) as exc:
        await env.svc.warn(FakePort(), G, MOD, TARGET, "x" * (MAX_REASON + 1))
    assert exc.value.key == "error.reason_too_long"
    await env.svc.warn(FakePort(), G, MOD, TARGET, "x" * MAX_REASON)


# --- Timeout ---


async def test_timeout_setzt_ablauf(env: Env) -> None:
    port = FakePort()
    result = await env.svc.timeout(port, G, MOD, TARGET, HOUR, "x")
    call = next(c for c in port.calls if c[0] == "timeout")
    assert call[2] == T0 + timedelta(hours=1)
    assert result.case.expires_at == T0 + timedelta(hours=1)
    assert result.case.duration_seconds == HOUR


@pytest.mark.parametrize("seconds", [0, -5, MAX_TIMEOUT + 1])
async def test_timeout_dauer_ausserhalb_des_limits(env: Env, seconds: int) -> None:
    port = FakePort()
    with pytest.raises(UserError) as exc:
        await env.svc.timeout(port, G, MOD, TARGET, seconds, None)
    assert exc.value.key == "error.mod_timeout_range"
    assert port.calls == []  # nichts passiert, auch keine DM


async def test_timeout_maximum_ist_erlaubt(env: Env) -> None:
    await env.svc.timeout(FakePort(), G, MOD, TARGET, MAX_TIMEOUT, None)


async def test_untimeout_hebt_auf(env: Env) -> None:
    port = FakePort()
    result = await env.svc.untimeout(port, G, MOD, TARGET, None)
    assert ("timeout", TARGET, None, str(MOD)) in port.calls
    assert result.case.type == "untimeout"


# --- Ban, Tempban, Softban, Unban ---


async def test_ban_mit_nachrichtenloeschung(env: Env) -> None:
    port = FakePort()
    await env.svc.ban(port, G, MOD, TARGET, "x", delete_seconds=2 * DAY)
    assert ("ban", TARGET, f"{MOD}: x", 2 * DAY) in port.calls


async def test_tempban_legt_case_und_job_an(env: Env) -> None:
    result = await env.svc.tempban(FakePort(), G, MOD, TARGET, 2 * HOUR, "x")
    assert result.case.active is True
    assert result.case.expires_at == T0 + timedelta(hours=2)
    (job,) = await jobs(env.db)
    assert (job.kind, job.guild_id, job.run_at) == (UNBAN_JOB, G, T0 + timedelta(hours=2))
    assert job.payload == {"case_no": result.case.case_no, "user_id": TARGET}


@pytest.mark.parametrize("seconds", [59, 0, MAX_TEMPBAN + 1])
async def test_tempban_dauer_ausserhalb_des_limits(env: Env, seconds: int) -> None:
    with pytest.raises(UserError) as exc:
        await env.svc.tempban(FakePort(), G, MOD, TARGET, seconds, None)
    assert exc.value.key == "error.mod_tempban_range"
    assert await jobs(env.db) == []


async def test_fehlgeschlagener_tempban_legt_keinen_job_an(env: Env) -> None:
    with pytest.raises(UserError):
        await env.svc.tempban(FakePort(fail_on={"ban"}), G, MOD, TARGET, HOUR, None)
    assert await jobs(env.db) == []
    assert await scalar(env.db, "SELECT count(*) FROM mod_cases") == 0


async def test_abgelaufener_tempban_wird_vom_scheduler_aufgehoben(env: Env) -> None:
    await env.svc.tempban(FakePort(), G, MOD, TARGET, HOUR, "x")
    port = FakePort()

    async def handler(job: ScheduledJob) -> None:
        assert await env.svc.finish_tempban(port, job.guild_id, job.payload["case_no"])

    env.scheduler.register(UNBAN_JOB, handler)
    assert await env.scheduler.run_once() == 0  # noch nicht fällig
    env.clock.now = T0 + timedelta(hours=1, seconds=1)
    assert await env.scheduler.run_once() == 1

    assert [c for c in port.calls if c[0] == "unban"]
    original = await env.svc.get_case(G, 1)
    assert original.active is False
    auto = await env.svc.get_case(G, 2)
    assert (auto.type, auto.moderator_id, auto.ref_case_no) == ("unban", BOT, 1)


async def test_manuell_entbannter_tempban_laeuft_ins_leere(env: Env) -> None:
    await env.svc.tempban(FakePort(), G, MOD, TARGET, HOUR, "x")
    await env.svc.unban(FakePort(), G, MOD, TARGET, "Gnade")
    assert (await env.svc.get_case(G, 1)).active is False

    port = FakePort()
    assert await env.svc.finish_tempban(port, G, 1) is False
    assert port.calls == []
    assert await scalar(env.db, "SELECT count(*) FROM mod_cases") == 2  # kein weiterer Case


async def test_neuer_tempban_ersetzt_den_alten(env: Env) -> None:
    await env.svc.tempban(FakePort(), G, MOD, TARGET, HOUR, "erst")
    await env.svc.tempban(FakePort(), G, MOD, TARGET, 5 * HOUR, "dann")
    assert (await env.svc.get_case(G, 1)).active is False
    assert (await env.svc.get_case(G, 2)).active is True
    assert await env.svc.finish_tempban(FakePort(), G, 1) is False


async def test_tempban_ist_nach_server_getrennt(env: Env) -> None:
    await env.svc.tempban(FakePort(), G, MOD, TARGET, HOUR, None)
    await env.svc.unban(FakePort(), G + 1, MOD, TARGET, None)
    assert (await env.svc.get_case(G, 1)).active is True


async def test_unban_eines_nicht_gebannten_ist_ein_fehler(env: Env) -> None:
    with pytest.raises(UserError) as exc:
        await env.svc.unban(FakePort(banned=False), G, MOD, TARGET, None)
    assert exc.value.key == "error.mod_not_banned"
    assert await scalar(env.db, "SELECT count(*) FROM mod_cases") == 0


async def test_automatischer_unban_toleriert_bereits_entbannten(env: Env) -> None:
    await env.svc.tempban(FakePort(), G, MOD, TARGET, HOUR, None)
    assert await env.svc.finish_tempban(FakePort(banned=False), G, 1) is True


async def test_automatischer_unban_fehler_laesst_den_case_aktiv_fuer_retry(env: Env) -> None:
    await env.svc.tempban(FakePort(), G, MOD, TARGET, HOUR, None)
    with pytest.raises(UserError):
        await env.svc.finish_tempban(FakePort(fail_on={"unban"}), G, 1)
    assert (await env.svc.get_case(G, 1)).active is True


async def test_softban_bannt_und_entbannt_in_dieser_reihenfolge(env: Env) -> None:
    port = FakePort()
    result = await env.svc.softban(port, G, MOD, TARGET, "x")
    assert [n for n in port.names() if n in ("ban", "unban")] == ["ban", "unban"]
    assert result.case.type == "softban"
    assert result.case.active is False


async def test_softban_mit_fehlgeschlagenem_unban_wird_als_ban_protokolliert(env: Env) -> None:
    port = FakePort(unban_fail_after_first=False, fail_on={"unban"})
    with pytest.raises(UserError):
        await env.svc.softban(port, G, MOD, TARGET, "x")
    case = await env.svc.get_case(G, 1)
    assert case.type == "ban"  # der Nutzer ist wirklich gebannt


# --- Eskalation ---


async def with_rules(env: Env) -> None:
    await env.store.save(
        G,
        ModerationSettings(
            escalation=[
                EscalationRule(warns=3, action="timeout", duration_seconds=HOUR),
                EscalationRule(warns=5, action="kick"),
            ]
        ),
    )


async def test_eskalation_feuert_genau_am_schwellwert(env: Env) -> None:
    await with_rules(env)
    port = FakePort()
    results = [await env.svc.warn(port, G, MOD, TARGET, f"w{i}") for i in range(1, 6)]
    escalations = [r.escalation for r in results]
    assert [e.type if e else None for e in escalations] == [None, None, "timeout", None, "kick"]
    timeout = escalations[2]
    assert timeout is not None
    assert (timeout.moderator_id, timeout.ref_case_no, timeout.duration_seconds) == (BOT, 3, HOUR)
    assert "3" in (timeout.reason or "")
    assert ("timeout", TARGET, T0 + timedelta(hours=1), f"{BOT}: {timeout.reason}") in port.calls


async def test_eskalation_zaehlt_pro_nutzer(env: Env) -> None:
    await with_rules(env)
    for _ in range(2):
        await env.svc.warn(FakePort(), G, MOD, TARGET, None)
    other = await env.svc.warn(FakePort(), G, MOD, TARGET + 1, None)
    assert other.escalation is None
    third = await env.svc.warn(FakePort(), G, MOD, TARGET, None)
    assert third.escalation is not None


async def test_eskalation_zaehlt_pro_server(env: Env) -> None:
    await with_rules(env)
    await env.store.save(
        G + 1, ModerationSettings(escalation=[EscalationRule(warns=3, action="kick")])
    )
    for _ in range(2):
        await env.svc.warn(FakePort(), G, MOD, TARGET, None)
    # Zählte man serverübergreifend, wäre das hier die dritte Verwarnung und löste den Kick aus.
    elsewhere = await env.svc.warn(FakePort(), G + 1, MOD, TARGET, None)
    assert elsewhere.escalation is None


async def test_entfernte_verwarnung_zaehlt_nicht_mehr(env: Env) -> None:
    await with_rules(env)
    await env.svc.warn(FakePort(), G, MOD, TARGET, None)
    await env.svc.warn(FakePort(), G, MOD, TARGET, None)
    await env.svc.unwarn(G, 1)
    third = await env.svc.warn(FakePort(), G, MOD, TARGET, None)
    assert third.escalation is None  # nur 2 aktive
    fourth = await env.svc.warn(FakePort(), G, MOD, TARGET, None)
    assert fourth.escalation is not None  # wieder 3 aktive


async def test_abgelaufene_verwarnungen_zaehlen_nicht(env: Env) -> None:
    await env.store.save(
        G,
        ModerationSettings(
            warn_expiry_days=30, escalation=[EscalationRule(warns=2, action="kick")]
        ),
    )
    await env.svc.warn(FakePort(), G, MOD, TARGET, "alt")
    async with env.db.session() as session:
        await session.execute(
            text("UPDATE mod_cases SET created_at = :t"), {"t": T0 - timedelta(days=40)}
        )
    second = await env.svc.warn(FakePort(), G, MOD, TARGET, "neu")
    assert second.escalation is None  # die alte zählt nicht mehr
    third = await env.svc.warn(FakePort(), G, MOD, TARGET, "neuer")
    assert third.escalation is not None


async def test_eskalation_zum_tempban_legt_job_an(env: Env) -> None:
    await env.store.save(
        G,
        ModerationSettings(
            escalation=[EscalationRule(warns=1, action="tempban", duration_seconds=DAY)]
        ),
    )
    result = await env.svc.warn(FakePort(), G, MOD, TARGET, None)
    assert result.escalation is not None
    assert result.escalation.type == "tempban"
    (job,) = await jobs(env.db)
    assert job.run_at == T0 + timedelta(days=1)


async def test_fehlgeschlagene_eskalation_behaelt_die_verwarnung(env: Env) -> None:
    await env.store.save(G, ModerationSettings(escalation=[EscalationRule(warns=1, action="kick")]))
    result = await env.svc.warn(FakePort(fail_on={"kick"}), G, MOD, TARGET, "x")
    assert result.case.type == "warn"
    assert result.escalation is None
    assert result.escalation_error == "error.mod_forbidden"
    assert await scalar(env.db, "SELECT count(*) FROM mod_cases") == 1


async def test_ohne_regeln_keine_eskalation(env: Env) -> None:
    for _ in range(10):
        result = await env.svc.warn(FakePort(), G, MOD, TARGET, None)
    assert result.escalation is None


# --- Unwarn ---


async def test_unwarn_fehler(env: Env) -> None:
    await env.svc.kick(FakePort(), G, MOD, TARGET, None)
    await env.svc.warn(FakePort(), G, MOD, TARGET, None)
    for case_no, key in ((99, "error.mod_case_not_found"), (1, "error.mod_not_a_warn")):
        with pytest.raises(UserError) as exc:
            await env.svc.unwarn(G, case_no)
        assert exc.value.key == key
    await env.svc.unwarn(G, 2)
    with pytest.raises(UserError) as exc:
        await env.svc.unwarn(G, 2)
    assert exc.value.key == "error.mod_warn_inactive"


async def test_unwarn_ist_nach_server_gescoped(env: Env) -> None:
    await env.svc.warn(FakePort(), G, MOD, TARGET, None)
    with pytest.raises(UserError) as exc:
        await env.svc.unwarn(G + 1, 1)
    assert exc.value.key == "error.mod_case_not_found"
    assert (await env.svc.get_case(G, 1)).active is True


# --- Historie ---


async def test_historie_mit_seiten_und_zaehlern(env: Env) -> None:
    for i in range(12):
        await env.svc.warn(FakePort(), G, MOD, TARGET, f"w{i}")
    await env.svc.kick(FakePort(), G, MOD, TARGET, None)
    await env.svc.warn(FakePort(), G, MOD, TARGET + 1, "andere")
    await env.svc.unwarn(G, 1)
    await env.svc.add_note(G, TARGET, MOD, "beobachten")

    page1 = await env.svc.history(G, TARGET)
    assert (page1.total, page1.pages, page1.page) == (13, 2, 1)
    assert page1.active_warns == 11
    assert page1.notes == 1
    assert [c.case_no for c in page1.cases][:2] == [13, 12]  # neueste zuerst
    assert len(page1.cases) == 10

    page2 = await env.svc.history(G, TARGET, page=2)
    assert len(page2.cases) == 3
    assert (await env.svc.history(G, TARGET, page=99)).page == 2  # geklemmt
    assert (await env.svc.history(G, TARGET, page=-3)).page == 1


async def test_historie_leer_und_server_getrennt(env: Env) -> None:
    empty = await env.svc.history(G, TARGET)
    assert (empty.total, empty.pages, empty.cases) == (0, 1, [])
    await env.svc.warn(FakePort(), G, MOD, TARGET, None)
    assert (await env.svc.history(G + 1, TARGET)).total == 0


# --- Notizen ---


async def test_notizen_anlegen_listen_entfernen(env: Env) -> None:
    n1 = await env.svc.add_note(G, TARGET, MOD, "  erste  ")
    await env.svc.add_note(G, TARGET, MOD + 1, "zweite")
    await env.svc.add_note(G, TARGET + 1, MOD, "fremd")
    notes = await env.svc.list_notes(G, TARGET)
    assert [n.content for n in notes] == ["erste", "zweite"]
    await env.svc.remove_note(G, n1.id)
    assert [n.content for n in await env.svc.list_notes(G, TARGET)] == ["zweite"]


async def test_notizen_validierung_und_server_isolation(env: Env) -> None:
    for bad, key in (("   ", "error.note_empty"), ("x" * 1001, "error.note_too_long")):
        with pytest.raises(UserError) as exc:
            await env.svc.add_note(G, TARGET, MOD, bad)
        assert exc.value.key == key
    note = await env.svc.add_note(G, TARGET, MOD, "geheim")
    with pytest.raises(UserError) as exc:
        await env.svc.remove_note(G + 1, note.id)  # fremder Server darf nicht löschen
    assert exc.value.key == "error.note_not_found"
    assert await env.svc.list_notes(G + 1, TARGET) == []
    assert len(await env.svc.list_notes(G, TARGET)) == 1
