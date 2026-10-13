from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ggbot.core.db.base import Base

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_DENIED = "denied"


class Suggestion(Base):
    """Ein Vorschlag eines Mitglieds; das Team gibt ihn frei oder lehnt ihn ab."""

    __tablename__ = "suggestions"
    __table_args__ = (
        UniqueConstraint("guild_id", "number"),
        Index("ix_suggestions_guild_status", "guild_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger)
    # Laufende Nummer je Server: die ID der Datenbank sagt Nutzern nichts.
    number: Mapped[int] = mapped_column(Integer)
    author_id: Mapped[int] = mapped_column(BigInteger)
    channel_id: Mapped[int | None] = mapped_column(BigInteger)
    message_id: Mapped[int | None] = mapped_column(BigInteger)
    text: Mapped[str] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(String(10), default=STATUS_PENDING)
    decided_by: Mapped[int | None] = mapped_column(BigInteger)
    decision_reason: Mapped[str | None] = mapped_column(String(500))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SuggestionVote(Base):
    """Eine Stimme je Nutzer und Vorschlag: +1 dafür, -1 dagegen."""

    __tablename__ = "suggestion_votes"

    suggestion_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("suggestions.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    vote: Mapped[int] = mapped_column(SmallInteger)


class SuggestionCounter(Base):
    __tablename__ = "suggestion_counters"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    last_no: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
