"""Ankündigungen gegen die echte Datenbank und den echten Scheduler."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from ggbot.core.announce import message as msg
from ggbot.core.db.engine import Database
from ggbot.core.db.models import ScheduledJob
from ggbot.core.errors import UserError
from ggbot.core.scheduler import Scheduler
from ggbot.core.services.announcements import MAX_PER_GUILD, SYNC_JOB, AnnouncementService

G, OTHER_G, USER, CHANNEL = 1, 2, 10, 500
NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)


@pytest.fixture
def service(db: Database) -> AnnouncementService:
    return AnnouncementService(db, Scheduler(db))


def text(content: str = "Hallo") -> msg.Message:
    return msg.Message(content=content)


async def jobs(db: Database) -> list[ScheduledJob]:
    async with db.session() as session:
        rows = await session.execute(
            select(ScheduledJob).where(ScheduledJob.kind == SYNC_JOB).order_by(ScheduledJob.id)
        )
        return list(rows.scalars())


async def test_entwurf_anlegen_lesen_aendern_loeschen(service: AnnouncementService) -> None:
    created = await service.create(
        G, USER, "  Willkommen   Woche ", msg.Message(embed=msg.Embed(title="Hi", color=5))
    )

    assert (created.name, created.state, created.message.embed) == (
        "Willkommen Woche",
        "draft",
        msg.Embed(title="Hi", color=5),
    )
    changed = await service.update(G, created.id, "Neu", text("Text"))
    assert (changed.name, changed.message.content, changed.message.embed) == ("Neu", "Text", None)
    assert [v.id for v in await service.list(G)] == [created.id]
    await service.delete(G, created.id)
    assert await service.list(G) == []


async def test_leere_nachricht_und_leerer_name_werden_abgelehnt(
    service: AnnouncementService,
) -> None:
    with pytest.raises(UserError) as empty:
        await service.create(G, USER, "x", msg.Message())
    assert empty.value.key == "error.announce_empty"
    with pytest.raises(UserError) as name:
        await service.create(G, USER, "   ", text())
    assert name.value.key == "error.announce_name"


async def test_server_sehen_nur_ihre_eigenen(service: AnnouncementService) -> None:
    mine = await service.create(G, USER, "a", text())

    for call in (
        service.get(OTHER_G, mine.id),
        service.update(OTHER_G, mine.id, "x", text()),
        service.delete(OTHER_G, mine.id),
        service.schedule(OTHER_G, mine.id, CHANNEL, None),
        service.cancel(OTHER_G, mine.id),
    ):
        with pytest.raises(UserError) as unknown:
            await call
        assert unknown.value.key == "error.announce_unknown"
    assert await service.list(OTHER_G) == []
    assert (await service.get(G, mine.id)).state == "draft"


async def test_obergrenze_je_server(
    service: AnnouncementService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("ggbot.core.services.announcements.MAX_PER_GUILD", 2)
    await service.create(G, USER, "a", text())
    await service.create(G, USER, "b", text())

    with pytest.raises(UserError) as full:
        await service.create(G, USER, "c", text())
    assert full.value.key == "error.announce_limit"
    await service.create(OTHER_G, USER, "a", text())
    assert MAX_PER_GUILD == 100


async def test_sofort_senden_plant_einen_job_mit_nummer(
    service: AnnouncementService, db: Database
) -> None:
    created = await service.create(G, USER, "a", text())

    before = datetime.now(UTC)
    view = await service.schedule(G, created.id, CHANNEL, None)

    assert (view.state, view.channel_id, view.rev) == ("scheduled", CHANNEL, 1)
    [job] = await jobs(db)
    assert job.payload == {"id": created.id, "rev": 1}
    assert before <= job.run_at <= datetime.now(UTC)


async def test_geplant_zur_gewuenschten_zeit(service: AnnouncementService, db: Database) -> None:
    created = await service.create(G, USER, "a", text())
    when = NOW + timedelta(hours=3)

    view = await service.schedule(G, created.id, CHANNEL, when, now=NOW)

    assert view.scheduled_at == when
    [job] = await jobs(db)
    assert job.run_at == when


async def test_zeitgrenzen(service: AnnouncementService, db: Database) -> None:
    created = await service.create(G, USER, "a", text())

    with pytest.raises(UserError) as past:
        await service.schedule(G, created.id, CHANNEL, NOW - timedelta(minutes=3), now=NOW)
    assert past.value.key == "error.announce_past"
    with pytest.raises(UserError) as far:
        await service.schedule(G, created.id, CHANNEL, NOW + timedelta(days=366), now=NOW)
    assert far.value.key == "error.announce_far"
    assert await jobs(db) == []
    # Knapp in der Vergangenheit gilt als „jetzt“, genau ein Jahr noch als erlaubt.
    near = await service.schedule(G, created.id, CHANNEL, NOW - timedelta(minutes=1), now=NOW)
    assert near.scheduled_at == NOW
    await service.schedule(G, created.id, CHANNEL, NOW + timedelta(days=365), now=NOW)


async def test_zeit_ohne_zeitzone_ist_ein_programmierfehler(service: AnnouncementService) -> None:
    created = await service.create(G, USER, "a", text())
    with pytest.raises(ValueError, match="zeitzonenbewusst"):
        await service.schedule(G, created.id, CHANNEL, datetime(2027, 1, 1))


async def test_neu_planen_erhoeht_die_nummer_und_absagen_ebenso(
    service: AnnouncementService,
) -> None:
    created = await service.create(G, USER, "a", text())

    first = await service.schedule(G, created.id, CHANNEL, NOW + timedelta(hours=1), now=NOW)
    second = await service.schedule(G, created.id, CHANNEL, NOW + timedelta(hours=2), now=NOW)
    cancelled = await service.cancel(G, created.id)

    assert (first.rev, second.rev, cancelled.rev) == (1, 2, 3)
    assert (cancelled.state, cancelled.scheduled_at) == ("draft", None)
    with pytest.raises(UserError) as again:
        await service.cancel(G, created.id)
    assert again.value.key == "error.announce_not_scheduled"


async def test_gesendetes_laesst_sich_nicht_erneut_senden(service: AnnouncementService) -> None:
    created = await service.create(G, USER, "a", text())
    view = await service.schedule(G, created.id, CHANNEL, None)
    assert await service.mark_sent(created.id, view.rev, 900)

    with pytest.raises(UserError) as sent:
        await service.schedule(G, created.id, CHANNEL, None)
    assert sent.value.key == "error.announce_sent"
    with pytest.raises(UserError):
        await service.cancel(G, created.id)


async def test_senden_ist_nur_mit_aktueller_nummer_moeglich(service: AnnouncementService) -> None:
    created = await service.create(G, USER, "a", text())
    old = await service.schedule(G, created.id, CHANNEL, NOW + timedelta(hours=1), now=NOW)
    await service.cancel(G, created.id)

    assert await service.mark_sent(created.id, old.rev, 900) is False  # abgesagt: Job veraltet
    assert await service.mark_failed(created.id, old.rev, "error.announce_perms") is False
    assert (await service.get(G, created.id)).state == "draft"


async def test_gleichzeitige_antworten_des_jobs_gelten_einmal(service: AnnouncementService) -> None:
    created = await service.create(G, USER, "a", text())
    view = await service.schedule(G, created.id, CHANNEL, None)

    results = await asyncio.gather(
        *(service.mark_sent(created.id, view.rev, 900 + i) for i in range(5))
    )

    assert results.count(True) == 1


async def test_fehlgeschlagen_kann_erneut_gesendet_werden(service: AnnouncementService) -> None:
    created = await service.create(G, USER, "a", text())
    view = await service.schedule(G, created.id, CHANNEL, None)
    assert await service.mark_failed(created.id, view.rev, "error.announce_perms")

    failed = await service.get(G, created.id)
    assert (failed.state, failed.error_key) == ("failed", "error.announce_perms")
    retried = await service.schedule(G, created.id, CHANNEL, None)
    assert (retried.state, retried.error_key, retried.rev) == ("scheduled", None, 2)


async def test_bearbeiten_einer_gesendeten_plant_einen_abgleich(
    service: AnnouncementService, db: Database
) -> None:
    created = await service.create(G, USER, "a", text("alt"))
    view = await service.schedule(G, created.id, CHANNEL, None)
    await service.mark_sent(created.id, view.rev, 900)
    assert len(await jobs(db)) == 1

    changed = await service.update(G, created.id, "a", text("neu"))

    assert (changed.state, changed.message_id) == ("sent", 900)
    after = await jobs(db)
    assert len(after) == 2
    assert after[-1].payload == {"id": created.id, "rev": view.rev}


async def test_bearbeiten_eines_entwurfs_plant_nichts(
    service: AnnouncementService, db: Database
) -> None:
    created = await service.create(G, USER, "a", text())
    await service.update(G, created.id, "a", text("neu"))
    assert await jobs(db) == []


async def test_ergebnis_der_bearbeitung_wird_gemerkt_und_geloescht(
    service: AnnouncementService,
) -> None:
    created = await service.create(G, USER, "a", text())
    view = await service.schedule(G, created.id, CHANNEL, None)
    await service.mark_sent(created.id, view.rev, 900)

    await service.set_edit_result(created.id, "error.announce_message_gone")
    assert (await service.get(G, created.id)).error_key == "error.announce_message_gone"
    await service.set_edit_result(created.id, None)
    assert (await service.get(G, created.id)).error_key is None


async def test_loeschen_laesst_den_job_ins_leere_laufen(
    service: AnnouncementService, db: Database
) -> None:
    created = await service.create(G, USER, "a", text())
    await service.schedule(G, created.id, CHANNEL, None)
    await service.delete(G, created.id)

    assert await service.get_for_job(created.id) is None


async def test_alte_nummer_kann_eine_neu_geplante_nicht_abschliessen(
    service: AnnouncementService,
) -> None:
    created = await service.create(G, USER, "a", text())
    old = await service.schedule(G, created.id, CHANNEL, NOW + timedelta(hours=1), now=NOW)
    new = await service.schedule(G, created.id, CHANNEL, NOW + timedelta(hours=2), now=NOW)

    assert await service.mark_sent(created.id, old.rev, 900) is False
    assert await service.mark_failed(created.id, old.rev, "error.announce_perms") is False
    assert (await service.get(G, created.id)).state == "scheduled"
    assert await service.mark_sent(created.id, new.rev, 901) is True
