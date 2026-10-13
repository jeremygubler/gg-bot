from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Identity, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ggbot.core.db.base import Base

STATE_DRAFT = "draft"
STATE_SCHEDULED = "scheduled"
STATE_SENT = "sent"
STATE_FAILED = "failed"


class Announcement(Base):
    """Eine im Dashboard gebaute Nachricht: Entwurf, geplant, gesendet oder fehlgeschlagen."""

    __tablename__ = "announcements"
    __table_args__ = (Index("ix_announcements_guild_state", "guild_id", "state"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger)
    # Nur für die Liste im Dashboard; wird nie nach Discord geschickt.
    name: Mapped[str] = mapped_column(String(100))
    content: Mapped[str | None] = mapped_column(String(2000))
    embed: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    state: Mapped[str] = mapped_column(String(12), default=STATE_DRAFT, server_default=STATE_DRAFT)
    channel_id: Mapped[int | None] = mapped_column(BigInteger)
    message_id: Mapped[int | None] = mapped_column(BigInteger)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Zählt jede Einplanung hoch: ein alter Job mit anderer Nummer wird übersprungen.
    rev: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error_key: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
