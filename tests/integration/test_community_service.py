import asyncio
from datetime import UTC, date, datetime, timedelta

import pytest
from redis.asyncio import Redis
from sqlalchemy import text

from ggbot.core.db.engine import Database
from ggbot.core.errors import UserError
from ggbot.core.scheduler import Scheduler
from ggbot.core.services.community import (
    MAX_SCHEDULED,
    MAX_STAT_CHANNELS,
    SCHEDULE_JOB,
    BirthdayService,
    ScheduleService,
    StarboardService,
    StatChannelService,
)
from ggbot.core.settings.starboard import StarboardSettings
from ggbot.core.settings.store import SettingsStore

G = 100
M = 5000


# --- Starboard ---


@pytest.fixture
def stars(db: Database, redis: Redis) -> StarboardService:
    return StarboardService(db, SettingsStore(db, redis))


async def vote(svc: StarboardService, user: int, guild: int = G, message: int = M) -> int:
    return await svc.vote(guild, message, user, channel_id=10, author_id=1)


async def test_sterne_zaehlen_jeden_einmal(stars: StarboardService) -> None:
    assert await vote(stars, 2) == 1
    assert await vote(stars, 3) == 2
    assert await vote(stars, 2) == 2  # derselbe Nutzer zählt nicht doppelt
    assert await stars.count(G, M) == 2


async def test_parallele_sterne(stars: StarboardService) -> None:
    results = await asyncio.gather(*(vote(stars, u) for u in range(10, 30)))
    assert await stars.count(G, M) == 20  # keiner geht verloren
    # Jede Transaktion sieht nur ihren Stand; darum zählen die Cogs nach dem Commit neu.
    assert all(1 <= r <= 20 for r in results)


async def test_stern_zurueckziehen(stars: StarboardService) -> None:
    assert await stars.unvote(G, M, 2) is None  # nie Sterne gehabt
    await vote(stars, 2)
    await vote(stars, 3)
    assert await stars.unvote(G, M, 2) == 1
    assert await stars.unvote(G, M, 2) == 1  # zweimal ist harmlos
    assert await stars.unvote(G, M, 3) == 0


async def test_sterne_je_server_getrennt(stars: StarboardService) -> None:
    await vote(stars, 2)
    assert await stars.count(G + 1, M) == 0
    assert await stars.entry(G + 1, M) is None
    entry = await stars.entry(G, M)
    assert entry is not None
    assert (entry.channel_id, entry.author_id, entry.star_message_id) == (10, 1, None)


async def test_posten_darf_genau_einer(stars: StarboardService) -> None:
    await vote(stars, 2)
    results = await asyncio.gather(*(stars.claim_post(G, M) for _ in range(8)))
    assert sum(results) == 1
    await stars.finish_post(G, M, 777)
    entry = await stars.entry(G, M)
    assert entry is not None
    assert entry.star_message_id == 777
    assert await stars.claim_post(G, M) is False  # schon gepostet


async def test_fehlgeschlagenes_posten_gibt_frei(stars: StarboardService) -> None:
    await vote(stars, 2)
    assert await stars.claim_post(G, M)
    await stars.finish_post(G, M, None)  # Senden scheiterte
    assert await stars.claim_post(G, M) is True  # der nächste Stern darf es nochmal versuchen


async def test_vergessen_nimmt_sterne_mit(stars: StarboardService, db: Database) -> None:
    await vote(stars, 2)
    await vote(stars, 3)
    await stars.claim_post(G, M)
    await stars.finish_post(G, M, 777)
    assert await stars.forget(G + 1, M) is None  # anderer Server
    entry = await stars.forget(G, M)
    assert entry is not None
    assert entry.star_message_id == 777
    assert await stars.count(G, M) == 0
    assert await stars.entry(G, M) is None
    assert await stars.forget(G, M) is None


async def test_starboard_einstellungen_cache(
    stars: StarboardService, db: Database, redis: Redis
) -> None:
    assert (await stars.settings(G)).enabled is False
    await SettingsStore(db, redis).save(G, StarboardSettings(enabled=True, channel_id=1))
    assert (await stars.settings(G)).enabled is False  # noch im Prozess-Cache
    stars.invalidate(G)
    assert (await stars.settings(G)).enabled is True


# --- Geburtstage ---


@pytest.fixture
def birthdays(db: Database, redis: Redis) -> BirthdayService:
    return BirthdayService(db, SettingsStore(db, redis))


async def test_geburtstag_speichern_aendern_loeschen(birthdays: BirthdayService) -> None:
    await birthdays.set(G, 1, 14, 3)
    assert await birthdays.get(G, 1) == (14, 3)
    await birthdays.set(G, 1, 15, 4)  # ändern
    assert await birthdays.get(G, 1) == (15, 4)
    assert await birthdays.get(G + 1, 1) is None
    await birthdays.remove(G, 1)
    assert await birthdays.get(G, 1) is None
    with pytest.raises(UserError) as exc:
        await birthdays.remove(G, 1)
    assert exc.value.key == "error.bd_none"


@pytest.mark.parametrize(("day", "month"), [(30, 2), (31, 4), (0, 1), (1, 13), (32, 1)])
async def test_ungueltiges_datum(birthdays: BirthdayService, day: int, month: int) -> None:
    with pytest.raises(UserError) as exc:
        await birthdays.set(G, 1, day, month)
    assert exc.value.key == "error.bd_invalid"
    assert await birthdays.get(G, 1) is None


async def test_wer_heute_feiert(birthdays: BirthdayService) -> None:
    await birthdays.set(G, 1, 14, 3)
    await birthdays.set(G, 2, 14, 3)
    await birthdays.set(G, 3, 15, 3)
    await birthdays.set(G + 1, 4, 14, 3)
    assert await birthdays.today(G, date(2026, 3, 14)) == [1, 2]
    assert await birthdays.today(G, date(2026, 3, 16)) == []
    assert await birthdays.today(G + 1, date(2026, 3, 14)) == [4]


async def test_schalttag_wird_im_normaljahr_am_28_gefeiert(birthdays: BirthdayService) -> None:
    await birthdays.set(G, 1, 29, 2)
    await birthdays.set(G, 2, 28, 2)
    assert await birthdays.today(G, date(2025, 2, 28)) == [1, 2]
    assert await birthdays.today(G, date(2025, 3, 1)) == []
    assert await birthdays.today(G, date(2024, 2, 28)) == [2]  # Schaltjahr: erst am 29.
    assert await birthdays.today(G, date(2024, 2, 29)) == [1]


async def test_naechste_geburtstage(birthdays: BirthdayService) -> None:
    today = date(2026, 3, 14)
    await birthdays.set(G, 1, 14, 3)  # heute
    await birthdays.set(G, 2, 20, 3)  # in 6 Tagen
    await birthdays.set(G, 3, 1, 3)  # nächstes Jahr
    await birthdays.set(G, 4, 29, 2)  # 2027 am 28.2.
    await birthdays.set(G + 1, 5, 15, 3)
    rows = await birthdays.upcoming(G, today)
    assert [(u, days) for u, _, _, days in rows] == [(1, 0), (2, 6), (4, 351), (3, 352)]
    assert len(await birthdays.upcoming(G, today, limit=2)) == 2


async def test_nutzerdaten_loeschen(birthdays: BirthdayService) -> None:
    await birthdays.set(G, 1, 14, 3)
    await birthdays.set(G + 1, 1, 14, 3)
    assert await birthdays.delete_user(G, 1) is True
    assert await birthdays.delete_user(G, 1) is False
    assert await birthdays.get(G + 1, 1) == (14, 3)  # anderer Server bleibt


# --- Stat-Kanäle ---


@pytest.fixture
def statchannels(db: Database) -> StatChannelService:
    return StatChannelService(db)


async def test_stat_kanaele(statchannels: StatChannelService) -> None:
    view = await statchannels.add(G, 7, "members", "Mitglieder: {count}")
    assert (view.channel_id, view.kind, view.template) == (7, "members", "Mitglieder: {count}")
    assert [s.channel_id for s in await statchannels.list(G)] == [7]
    assert await statchannels.list(G + 1) == []
    with pytest.raises(UserError) as exc:
        await statchannels.remove(G + 1, 7)
    assert exc.value.key == "error.st_unknown"
    await statchannels.remove(G, 7)
    assert await statchannels.list(G) == []


@pytest.mark.parametrize(
    ("kind", "template", "key"),
    [
        ("nonsense", "{count}", "error.st_kind"),
        ("members", "ohne zahl", "error.st_template"),
        ("members", "{level}", "error.st_template"),
    ],
)
async def test_ungueltiger_stat_kanal(
    statchannels: StatChannelService, kind: str, template: str, key: str
) -> None:
    with pytest.raises(UserError) as exc:
        await statchannels.add(G, 7, kind, template)
    assert exc.value.key == key
    assert await statchannels.list(G) == []


async def test_stat_kanal_grenze(statchannels: StatChannelService) -> None:
    for i in range(MAX_STAT_CHANNELS):
        await statchannels.add(G, 100 + i, "members", "{count}")
    with pytest.raises(UserError) as exc:
        await statchannels.add(G, 999, "members", "{count}")
    assert exc.value.key == "error.st_limit"
    await statchannels.add(G + 1, 998, "members", "{count}")


# --- Geplante Nachrichten ---


@pytest.fixture
def schedules(db: Database) -> ScheduleService:
    return ScheduleService(db, Scheduler(db))


async def jobs(db: Database) -> list[tuple[str, dict[str, object], datetime]]:
    async with db.session() as session:
        rows = await session.execute(
            text("SELECT kind, payload, run_at FROM scheduled_jobs ORDER BY id")
        )
        return [(r.kind, r.payload, r.run_at) for r in rows]


async def add(schedules: ScheduleService, **kw: object) -> int:
    args = {"channel_id": 7, "text": "Hallo", "created_by": 1, "delay": timedelta(hours=1)} | kw
    return (await schedules.add(G, **args)).id  # type: ignore[arg-type]


async def test_einmalige_nachricht(schedules: ScheduleService, db: Database) -> None:
    message_id = await add(schedules, text="  Hallo Welt ")
    [view] = await schedules.list(G)
    assert (view.text, view.interval_seconds, view.channel_id) == ("Hallo Welt", None, 7)
    [(kind, payload, run_at)] = await jobs(db)
    assert kind == SCHEDULE_JOB
    assert payload["id"] == message_id
    assert datetime.fromisoformat(str(payload["at"])) == view.next_run == run_at
    claimed = await schedules.claim_run(G, message_id, view.next_run)
    assert claimed is not None
    assert await schedules.list(G) == []  # einmalig: danach erledigt
    assert len(await jobs(db)) == 1  # kein Folgetermin


async def test_wiederkehrende_nachricht_plant_den_naechsten_termin(
    schedules: ScheduleService, db: Database
) -> None:
    message_id = await add(schedules, interval=timedelta(hours=2))
    [view] = await schedules.list(G)
    now = view.next_run + timedelta(seconds=3)
    claimed = await schedules.claim_run(G, message_id, view.next_run, now=now)
    assert claimed is not None
    [after] = await schedules.list(G)
    assert after.next_run == view.next_run + timedelta(hours=2)
    kinds = await jobs(db)
    assert len(kinds) == 2
    assert datetime.fromisoformat(str(kinds[1][1]["at"])) == after.next_run


async def test_ausfall_ueberspringt_verpasste_runden(schedules: ScheduleService) -> None:
    message_id = await add(schedules, interval=timedelta(hours=1))
    [view] = await schedules.list(G)
    late = view.next_run + timedelta(hours=5, minutes=30)  # der Bot war lange aus
    assert await schedules.claim_run(G, message_id, view.next_run, now=late) is not None
    [after] = await schedules.list(G)
    assert after.next_run == view.next_run + timedelta(
        hours=6
    )  # nicht sechs Nachrichten auf einmal


async def test_doppelter_job_sendet_nur_einmal(schedules: ScheduleService, db: Database) -> None:
    message_id = await add(schedules, interval=timedelta(hours=1))
    [view] = await schedules.list(G)
    results = await asyncio.gather(
        *(schedules.claim_run(G, message_id, view.next_run) for _ in range(6))
    )
    assert sum(r is not None for r in results) == 1
    assert len(await jobs(db)) == 2  # genau ein Folgetermin, nicht sechs


async def test_veralteter_termin_wird_ignoriert(schedules: ScheduleService) -> None:
    message_id = await add(schedules, interval=timedelta(hours=1))
    [view] = await schedules.list(G)
    assert await schedules.claim_run(G, message_id, view.next_run - timedelta(minutes=1)) is None
    assert await schedules.claim_run(G + 1, message_id, view.next_run) is None  # fremder Server
    assert await schedules.claim_run(G, 99999, view.next_run) is None


async def test_abbrechen(schedules: ScheduleService) -> None:
    message_id = await add(schedules, interval=timedelta(hours=1))
    [view] = await schedules.list(G)
    with pytest.raises(UserError):
        await schedules.cancel(G + 1, message_id)
    await schedules.cancel(G, message_id)
    assert (
        await schedules.claim_run(G, message_id, view.next_run) is None
    )  # der geplante Job greift ins Leere
    with pytest.raises(UserError) as exc:
        await schedules.cancel(G, message_id)
    assert exc.value.key == "error.sm_unknown"


@pytest.mark.parametrize(
    ("kwargs", "key"),
    [
        ({"text": "  "}, "error.sm_text"),
        ({"text": "x" * 1501}, "error.sm_text"),
        ({"delay": timedelta(seconds=30)}, "error.sm_delay"),
        ({"delay": timedelta(days=366)}, "error.sm_delay"),
        ({"interval": timedelta(minutes=4)}, "error.sm_interval"),
        ({"interval": timedelta(days=31)}, "error.sm_interval"),
    ],
)
async def test_ungueltige_geplante_nachricht(
    schedules: ScheduleService, db: Database, kwargs: dict[str, object], key: str
) -> None:
    with pytest.raises(UserError) as exc:
        await add(schedules, **kwargs)
    assert exc.value.key == key
    assert await jobs(db) == []


async def test_grenze_geplanter_nachrichten(schedules: ScheduleService) -> None:
    for _ in range(MAX_SCHEDULED):
        await add(schedules)
    with pytest.raises(UserError) as exc:
        await add(schedules)
    assert exc.value.key == "error.sm_limit"
    await schedules.add(G + 1, channel_id=1, text="x", created_by=1, delay=timedelta(hours=1))


async def test_erledigte_zaehlen_nicht_zur_grenze(schedules: ScheduleService) -> None:
    for _ in range(MAX_SCHEDULED):
        await add(schedules)
    first = (await schedules.list(G))[0]
    await schedules.cancel(G, first.id)
    await add(schedules)  # wieder Platz
    assert datetime.now(UTC) < (await schedules.list(G))[0].next_run
