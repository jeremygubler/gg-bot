from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Identity, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ggbot.core.db.base import Base

STATE_PENDING = "pending"
STATE_RUNNING = "running"
STATE_DONE = "done"
STATE_FAILED = "failed"


class ScheduledJob(Base):
    """Persistenter Job (Tempban-Ende, Erinnerung, Auto-Close …).

    Wird mit `FOR UPDATE SKIP LOCKED` geclaimt; ein Job in `running` mit
    abgelaufenem `claimed_until` gilt als verwaist und wird erneut ausgeliefert.
    """

    __tablename__ = "scheduled_jobs"
    __table_args__ = (Index("ix_scheduled_jobs_state_run_at", "state", "run_at"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    kind: Mapped[str] = mapped_column(String(64))
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    state: Mapped[str] = mapped_column(String(16), default=STATE_PENDING)
    claimed_by: Mapped[str | None] = mapped_column(String(64))
    claimed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
