"""Persistenter Job-Scheduler auf `scheduled_jobs`.

Mehrere Prozesse/Shards können parallel laufen: jeder Job wird dank
`FOR UPDATE SKIP LOCKED` nur einmal ausgeliefert.
"""

import asyncio
import contextlib
import socket
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from ggbot.core.db.engine import Database
from ggbot.core.db.models import ScheduledJob
from ggbot.core.db.repos import jobs as repo
from ggbot.core.logging import get_logger

log = get_logger(__name__)

Handler = Callable[[ScheduledJob], Awaitable[None]]
Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(UTC)


def backoff(attempts: int) -> timedelta:
    """30 s, 60 s, 120 s … höchstens 1 h."""
    return timedelta(seconds=min(30 * 2 ** max(attempts - 1, 0), 3600))


class Scheduler:
    def __init__(
        self,
        db: Database,
        *,
        clock: Clock = _utcnow,
        interval: float = 5.0,
        lease: timedelta = timedelta(minutes=5),
        max_attempts: int = 5,
        batch: int = 20,
        worker: str | None = None,
    ) -> None:
        self._db = db
        self._clock = clock
        self._interval = interval
        self._lease = lease
        self._max_attempts = max_attempts
        self._batch = batch
        self._worker = worker or f"{socket.gethostname()}:{id(self):x}"
        self._handlers: dict[str, Handler] = {}
        self._task: asyncio.Task[None] | None = None

    def register(self, kind: str, handler: Handler) -> None:
        if kind in self._handlers:
            raise ValueError(f"Job-Art bereits registriert: {kind}")
        self._handlers[kind] = handler

    async def enqueue(
        self, guild_id: int, kind: str, run_at: datetime, payload: dict[str, object] | None = None
    ) -> int:
        if run_at.tzinfo is None:
            raise ValueError("run_at muss zeitzonenbewusst sein")
        async with self._db.session() as session:
            job = await repo.enqueue(session, guild_id, kind, run_at, payload)
            return job.id

    async def run_once(self) -> int:
        """Führt alle fälligen Jobs aus und liefert deren Anzahl."""
        now = self._clock()
        async with self._db.session() as session:
            jobs = await repo.claim_due(
                session, now=now, worker=self._worker, lease=self._lease, limit=self._batch
            )
        for job in jobs:
            await self._execute(job)
        return len(jobs)

    async def _execute(self, job: ScheduledJob) -> None:
        handler = self._handlers.get(job.kind)
        try:
            if handler is None:
                raise LookupError(f"kein Handler für Job-Art {job.kind!r}")
            await handler(job)
        except Exception as exc:
            log.exception("job_failed", job_id=job.id, kind=job.kind, attempts=job.attempts)
            now = self._clock()
            retry_at = (
                None
                if job.attempts >= self._max_attempts or handler is None
                else now + backoff(job.attempts)
            )
            async with self._db.session() as session:
                await repo.fail(session, job.id, now=now, error=repr(exc)[:2000], retry_at=retry_at)
            return
        async with self._db.session() as session:
            await repo.complete(session, job.id, now=self._clock())

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="scheduler")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.run_once()
            except Exception:
                # Eine kurze DB-Störung darf den Scheduler nicht beenden.
                log.exception("scheduler_tick_failed")
            await asyncio.sleep(self._interval)
