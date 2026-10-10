"""Last und Ausfälle: der Scheduler liefert jeden Job genau einmal, auch wenn viele Worker
gleichzeitig ziehen, einer mittendrin stirbt oder Handler scheitern. Dazu der Ereignisweg unter
Beschuss."""

import asyncio
import random
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy import text

from ggbot.core import events
from ggbot.core.db.engine import Database
from ggbot.core.db.models import ScheduledJob
from ggbot.core.db.repos import jobs as repo
from ggbot.core.scheduler import Scheduler

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
JOBS = 300
WORKERS = 5


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


async def seed(db: Database, count: int = JOBS) -> None:
    async with db.session() as session:
        for _ in range(count):
            await repo.enqueue(session, 1, "work", T0)


async def states(db: Database) -> dict[str, int]:
    async with db.session() as session:
        rows = await session.execute(text("SELECT state, count(*) FROM scheduled_jobs GROUP BY 1"))
        return {row[0]: row[1] for row in rows}


async def drain(workers: list[Scheduler]) -> None:
    """Alle Worker ziehen gleichzeitig weiter, bis keiner mehr etwas findet."""

    async def loop(worker: Scheduler) -> None:
        while await worker.run_once():
            pass

    await asyncio.gather(*(loop(w) for w in workers))


async def test_viele_worker_liefern_jeden_job_genau_einmal(db: Database) -> None:
    await seed(db)
    executed: list[int] = []
    clock = Clock()

    async def handler(job: ScheduledJob) -> None:
        executed.append(job.id)
        await asyncio.sleep(0)  # andere Worker dürfen dazwischenfunken

    workers = [
        Scheduler(db, clock=clock, batch=7, worker=f"w{i}")  # kleine Pakete: viele Zugriffe
        for i in range(WORKERS)
    ]
    for worker in workers:
        worker.register("work", handler)

    await drain(workers)

    assert len(executed) == JOBS
    assert len(set(executed)) == JOBS  # keiner doppelt
    assert await states(db) == {"done": JOBS}


async def test_scheiternde_handler_gehen_nicht_verloren_und_laufen_nicht_zu_oft(
    db: Database,
) -> None:
    await seed(db, 120)
    clock = Clock()
    attempts: dict[int, int] = {}
    rng = random.Random(7)  # noqa: S311  # fester Startwert, kein Sicherheitszweck
    flaky = {i for i in range(1, 121) if rng.random() < 0.3}

    async def handler(job: ScheduledJob) -> None:
        attempts[job.id] = attempts.get(job.id, 0) + 1
        if job.id in flaky and attempts[job.id] == 1:
            raise RuntimeError("erster Versuch scheitert")

    workers = [Scheduler(db, clock=clock, batch=5, worker=f"w{i}") for i in range(WORKERS)]
    for worker in workers:
        worker.register("work", handler)

    await drain(workers)
    assert await states(db) == {
        "done": 120 - len(flaky),
        "pending": len(flaky),
    }  # Wiederholung wartet

    clock.now = T0 + timedelta(minutes=5)  # Backoff vorbei
    await drain(workers)

    assert await states(db) == {"done": 120}
    assert {i for i, n in attempts.items() if n == 2} == flaky  # genau die, nicht mehr
    assert {n for i, n in attempts.items() if i not in flaky} == {1}


async def test_ein_toter_worker_verliert_keine_jobs(db: Database) -> None:
    """Ein Prozess stirbt nach dem Ziehen, ohne zu melden: danach übernimmt ein anderer."""
    await seed(db, 40)
    clock = Clock()
    async with db.session() as session:  # „Worker x“ zieht alles und stirbt
        claimed = await repo.claim_due(
            session, now=T0, worker="x", lease=timedelta(minutes=5), limit=40
        )
    assert len(claimed) == 40
    executed: list[int] = []

    async def handler(job: ScheduledJob) -> None:
        executed.append(job.id)

    survivors = [Scheduler(db, clock=clock, batch=6, worker=f"s{i}") for i in range(3)]
    for worker in survivors:
        worker.register("work", handler)

    await drain(survivors)
    assert executed == []  # Pacht läuft noch: niemand greift in fremde Arbeit

    clock.now = T0 + timedelta(minutes=6)
    await drain(survivors)

    assert sorted(executed) == sorted(j.id for j in claimed)
    assert len(set(executed)) == 40
    assert await states(db) == {"done": 40}


async def test_ereignisse_unter_beschuss_kommen_vollstaendig_und_in_reihenfolge(
    redis: Redis,
) -> None:
    received: list[int] = []
    ready = asyncio.Event()

    async def listen() -> None:
        stream = events.subscribe(redis)
        ready.set()
        async for event in stream:
            if isinstance(event, events.GuildSync):
                received.append(event.guild_id)
                if len(received) == 500:
                    return

    task = asyncio.create_task(listen())
    await ready.wait()
    await asyncio.sleep(0.2)  # Abo steht
    for guild_id in range(500):
        await events.publish(redis, events.GuildSync(guild_id=guild_id))
        if guild_id % 100 == 50:
            await redis.publish(events.CHANNEL, "kein json {")  # Müll dazwischen

    await asyncio.wait_for(task, timeout=15)

    assert received == list(range(500))
