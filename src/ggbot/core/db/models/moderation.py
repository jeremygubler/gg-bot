from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ggbot.core.db.base import Base


class ModCase(Base):
    """Eine Moderationsaktion. `case_no` zählt pro Server, `id` ist nur der technische Schlüssel."""

    __tablename__ = "mod_cases"
    __table_args__ = (
        UniqueConstraint("guild_id", "case_no"),
        Index("ix_mod_cases_guild_id_target_id_created_at", "guild_id", "target_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger)
    case_no: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(16))
    target_id: Mapped[int] = mapped_column(BigInteger)
    moderator_id: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[str | None] = mapped_column(Text)
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Verwarnung: zählt für die Eskalation. Tempban: wartet auf den Unban.
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    ref_case_no: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ModCaseCounter(Base):
    __tablename__ = "mod_case_counters"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    last_no: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class ModNote(Base):
    __tablename__ = "mod_notes"
    __table_args__ = (Index("ix_mod_notes_guild_id_target_id", "guild_id", "target_id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger)
    target_id: Mapped[int] = mapped_column(BigInteger)
    author_id: Mapped[int] = mapped_column(BigInteger)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChannelLock(Base):
    """Merkt sich, wie `send_messages` für @everyone vor `/lock` stand (None = geerbt)."""

    __tablename__ = "channel_locks"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    channel_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    previous_send_messages: Mapped[bool | None] = mapped_column(Boolean)
    locked_by: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
