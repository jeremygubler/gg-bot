from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Index, Integer, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ggbot.core.db.base import Base


class XpMember(Base):
    """XP eines Mitglieds. Das Level wird aus den XP berechnet, nie gespeichert (eine Wahrheit)."""

    __tablename__ = "xp_members"
    __table_args__ = (Index("ix_xp_members_guild_id_xp", "guild_id", "xp"),)

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    xp: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    msg_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    voice_seconds: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    last_xp_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RankCardStyle(Base):
    """Persönliche Rank-Card-Einstellungen (JSON, validiert durch `CardStyle`)."""

    __tablename__ = "rank_card_styles"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    style: Mapped[dict[str, Any]] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
