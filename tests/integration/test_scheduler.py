import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from ggbot.core.db.engine import Database
from ggbot.core.db.models import ScheduledJob
from ggbot.core.db.repos import jobs as repo
from ggbot.core.scheduler import Scheduler

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


class FakeClock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


async def fetch(db: Database, job_id: int) -> ScheduledJob:
    async with db.session() as session:
        job = await repo.get(session, 1, job_id)
    assert job is not None
    return job


async def test_faelliger_job_laeuft_genau_einmal(db: Database) -> None:
    clock = FakeClock()
    scheduler = Scheduler(db, clock=clock)
    ran: list[int] = []

    async def handler(job: ScheduledJob) -> None:
        ran.append(job.id)

    scheduler.register("test", handler)
    job_id = await scheduler.enqueue(1, "test", T0, {"x": 1})
    assert await scheduler.run_once() == 1
    assert await scheduler.run_once() == 0
    assert ran == [job_id]
    assert (await fetch(db, job_id)).state == "done"


async def test_zukuenftiger_job_wartet(db: Database) -> None:
    clock = FakeClock()
    scheduler = Scheduler(db, clock=clock)
    scheduler.register("test", lambda job: asyncio.sleep(0))
    await scheduler.enqueue(1, "test", T0 + timedelta(minutes=1))
    assert await scheduler.run_once() == 0
    clock.now = T0 + timedelta(minutes=1)
    assert await scheduler.run_once() == 1


async def test_fehler_wird_wiederholt_mit_backoff_dann_failed(db: Database) -> None:
    clock = FakeClock()
    scheduler = Scheduler(db, clock=clock, max_attempts=2)
    calls = 0

    async def boom(job: ScheduledJob) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("kaputt")

    scheduler.register("test", boom)
    job_id = await scheduler.enqueue(1, "test", T0)

    await scheduler.run_once()
    job = await fetch(db, job_id)
    assert (job.state, job.attempts) == ("pending", 1)
    assert job.run_at == T0 + timedelta(seconds=30)
    assert job.last_error is not None
    assert "kaputt" in job.last_error

    assert await scheduler.run_once() == 0  # Backoff noch nicht abgelaufen
    clock.now = T0 + timedelta(seconds=30)
    await scheduler.run_once()
    job = await fetch(db, job_id)
    assert (job.state, job.attempts, calls) == ("failed", 2, 2)
    assert job.finished_at is not None


async def test_unbekannte_art_schlaegt_endgueltig_fehl(db: Database) -> None:
    scheduler = Scheduler(db, clock=FakeClock())
    job_id = await scheduler.enqueue(1, "gibt-es-nicht", T0)
    await scheduler.run_once()
    job = await fetch(db, job_id)
    assert job.state == "failed"
    assert job.last_error is not None
    assert "kein Handler" in job.last_error


async def test_verwaister_job_wird_nach_ablauf_der_lease_neu_geliefert(db: Database) -> None:
    clock = FakeClock()
    async with db.session() as session:
        job = await repo.enqueue(session, 1, "test", T0)
        job_id = job.id
    async with db.session() as session:  # Worker A claimt und stirbt
        await repo.claim_due(session, now=T0, worker="a", lease=timedelta(minutes=5), limit=10)

    scheduler = Scheduler(db, clock=clock)
    ran: list[int] = []

    async def handler(job: ScheduledJob) -> None:
        ran.append(job.id)

    scheduler.register("test", handler)
    assert await scheduler.run_once() == 0  # Lease läuft noch
    clock.now = T0 + timedelta(minutes=6)
    assert await scheduler.run_once() == 1
    assert ran == [job_id]


async def test_parallele_worker_teilen_sich_die_jobs(db: Database) -> None:
    async with db.session() as session:
        for _ in range(10):
            await repo.enqueue(session, 1, "test", T0)

    async def claim(worker: str) -> list[int]:
        async with db.session() as session:
            jobs = await repo.claim_due(
                session, now=T0, worker=worker, lease=timedelta(minutes=5), limit=10
            )
            await asyncio.sleep(0.2)  # Sperren halten, bis der andere gelesen hat
            return [j.id for j in jobs]

    a, b = await asyncio.gather(claim("a"), claim("b"))
    assert set(a).isdisjoint(b)
    assert len(a) + len(b) == 10


async def test_run_at_ohne_zeitzone_wird_abgelehnt(db: Database) -> None:
    scheduler = Scheduler(db)
    with pytest.raises(ValueError, match="zeitzonenbewusst"):
        await scheduler.enqueue(1, "test", datetime(2026, 1, 1))


async def test_doppelte_registrierung_wird_abgelehnt(db: Database) -> None:
    scheduler = Scheduler(db)
    scheduler.register("test", lambda job: asyncio.sleep(0))
    with pytest.raises(ValueError, match="bereits registriert"):
        scheduler.register("test", lambda job: asyncio.sleep(0))


async def test_job_ist_nach_guild_gescoped(db: Database) -> None:
    scheduler = Scheduler(db)
    job_id = await scheduler.enqueue(1, "test", T0)
    async with db.session() as session:
        assert await repo.get(session, 2, job_id) is None
