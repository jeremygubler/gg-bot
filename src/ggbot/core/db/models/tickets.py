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
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ggbot.core.db.base import Base


class TicketPanel(Base):
    """Eine Nachricht mit einem Button pro Kategorie."""

    __tablename__ = "ticket_panels"
    __table_args__ = (Index("ix_ticket_panels_guild_id", "guild_id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger)
    channel_id: Mapped[int | None] = mapped_column(BigInteger)
    message_id: Mapped[int | None] = mapped_column(BigInteger)
    title: Mapped[str] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TicketCategory(Base):
    __tablename__ = "ticket_categories"
    __table_args__ = (UniqueConstraint("panel_id", "name"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    panel_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("ticket_panels.id", ondelete="CASCADE"), index=True
    )
    guild_id: Mapped[int] = mapped_column(BigInteger)
    name: Mapped[str] = mapped_column(String(80))
    emoji: Mapped[str | None] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(String(100))
    # Discord-Kategorie, in der die Ticket-Kanäle entstehen (None = oberste Ebene).
    parent_id: Mapped[int | None] = mapped_column(BigInteger)
    support_role_ids: Mapped[list[int]] = mapped_column(JSONB, default=list)
    welcome_message: Mapped[str | None] = mapped_column(String(1000))
    # Fragen, die der Ersteller vorab in einem Formular beantwortet (höchstens 5).
    questions: Mapped[list[str]] = mapped_column(JSONB, default=list)
    position: Mapped[int] = mapped_column(Integer)


class TicketCounter(Base):
    __tablename__ = "ticket_counters"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    last_no: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class Ticket(Base):
    __tablename__ = "tickets"
    __table_args__ = (
        UniqueConstraint("guild_id", "number"),
        UniqueConstraint("channel_id"),
        Index("ix_tickets_guild_creator_status", "guild_id", "creator_id", "status"),
        Index("ix_tickets_guild_status", "guild_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger)
    number: Mapped[int] = mapped_column(Integer)
    channel_id: Mapped[int] = mapped_column(BigInteger)
    creator_id: Mapped[int] = mapped_column(BigInteger)
    # Die Kategorie kann später gelöscht werden; der Name und die Support-Rollen bleiben im Ticket.
    category_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("ticket_categories.id", ondelete="SET NULL")
    )
    category_name: Mapped[str] = mapped_column(String(80))
    support_role_ids: Mapped[list[int]] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(8), default="open")  # open | closed
    claimed_by: Mapped[int | None] = mapped_column(BigInteger)
    answers: Mapped[list[dict[str, str]]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by: Mapped[int | None] = mapped_column(BigInteger)
    close_reason: Mapped[str | None] = mapped_column(String(300))


class TicketTranscript(Base):
    __tablename__ = "ticket_transcripts"
    __table_args__ = (Index("ix_ticket_transcripts_guild_id", "guild_id"),)

    ticket_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tickets.id", ondelete="CASCADE"), primary_key=True
    )
    guild_id: Mapped[int] = mapped_column(BigInteger)
    html: Mapped[str] = mapped_column(Text)
    message_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TicketFeedback(Base):
    __tablename__ = "ticket_feedback"

    ticket_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tickets.id", ondelete="CASCADE"), primary_key=True
    )
    guild_id: Mapped[int] = mapped_column(BigInteger)
    user_id: Mapped[int] = mapped_column(BigInteger)
    rating: Mapped[int] = mapped_column(SmallInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
