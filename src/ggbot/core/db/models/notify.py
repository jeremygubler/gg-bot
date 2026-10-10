from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Identity,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ggbot.core.db.base import Base


class NotifySubscription(Base):
    """Ein Streamer oder Kanal, der in einem Discord-Kanal angekündigt wird, samt Zustand."""

    __tablename__ = "notify_subscriptions"
    __table_args__ = (
        UniqueConstraint("guild_id", "provider", "ref"),
        Index("ix_notify_subscriptions_provider_ref", "provider", "ref"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    provider: Mapped[str] = mapped_column(String(8))  # twitch | youtube
    ref: Mapped[str] = mapped_column(String(64))  # Twitch-Login oder YouTube-Kanal-ID
    display_name: Mapped[str] = mapped_column(String(100))
    channel_id: Mapped[int] = mapped_column(BigInteger)
    message: Mapped[str | None] = mapped_column(String(300))
    role_id: Mapped[int | None] = mapped_column(BigInteger)
    # Zustand (notify_state): was zuletzt angekündigt wurde und ob der Stream noch läuft.
    last_key: Mapped[str | None] = mapped_column(String(64))
    live: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    misses: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    seen: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    message_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
