from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Identity, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ggbot.core.db.base import Base


class AutomodHit(Base):
    """Ein Treffer einer Automod-Regel. Nur Statistik, mit Aufbewahrungsfrist."""

    __tablename__ = "automod_hits"
    __table_args__ = (Index("ix_automod_hits_guild_id_created_at", "guild_id", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger)
    user_id: Mapped[int] = mapped_column(BigInteger)
    channel_id: Mapped[int | None] = mapped_column(BigInteger)
    rule: Mapped[str] = mapped_column(String(16))
    # Was getan wurde, kommagetrennt, z. B. "delete,warn".
    actions: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
