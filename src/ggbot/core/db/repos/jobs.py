from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ggbot.core.db.models import ScheduledJob
from ggbot.core.db.models.jobs import STATE_DONE, STATE_FAILED, STATE_PENDING, STATE_RUNNING


async def enqueue(
    session: AsyncSession,
    guild_id: int,
    kind: str,
    run_at: datetime,
    payload: dict[str, Any] | None = None,
) -> ScheduledJob:
    job = ScheduledJob(guild_id=guild_id, kind=kind, run_at=run_at, payload=payload or {})
    session.add(job)
    await session.flush()
    return job


async def claim_due(
    session: AsyncSession, *, now: datetime, worker: str, lease: timedelta, limit: int
) -> list[ScheduledJob]:
    """Holt fällige Jobs exklusiv. Parallele Worker überspringen gesperrte Zeilen."""
    due = or_(
        and_(ScheduledJob.state == STATE_PENDING, ScheduledJob.run_at <= now),
        and_(ScheduledJob.state == STATE_RUNNING, ScheduledJob.claimed_until < now),
    )
    result = await session.execute(
        select(ScheduledJob)
        .where(due)
        .order_by(ScheduledJob.run_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    jobs = list(result.scalars())
    for job in jobs:
        job.state = STATE_RUNNING
        job.claimed_by = worker
        job.claimed_until = now + lease
        job.attempts += 1
    await session.flush()
    return jobs


async def complete(
    session: AsyncSession, job_id: int, *, now: datetime, result: dict[str, Any] | None = None
) -> None:
    await session.execute(
        update(ScheduledJob)
        .where(ScheduledJob.id == job_id)
        .values(
            state=STATE_DONE, finished_at=now, claimed_by=None, claimed_until=None, result=result
        )
    )


async def fail(
    session: AsyncSession,
    job_id: int,
    *,
    now: datetime,
    error: str,
    retry_at: datetime | None,
) -> None:
    """`retry_at=None` beendet den Job endgültig als `failed`."""
    values: dict[str, Any] = {"last_error": error, "claimed_by": None, "claimed_until": None}
    if retry_at is None:
        values.update(state=STATE_FAILED, finished_at=now)
    else:
        values.update(state=STATE_PENDING, run_at=retry_at)
    await session.execute(update(ScheduledJob).where(ScheduledJob.id == job_id).values(**values))


async def get(session: AsyncSession, guild_id: int, job_id: int) -> ScheduledJob | None:
    result = await session.execute(
        select(ScheduledJob).where(ScheduledJob.id == job_id, ScheduledJob.guild_id == guild_id)
    )
    return result.scalar_one_or_none()
