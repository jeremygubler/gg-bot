from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
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


class CustomCommand(Base):
    __tablename__ = "custom_commands"
    __table_args__ = (UniqueConstraint("guild_id", "name"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    name: Mapped[str] = mapped_column(String(32))
    response: Mapped[str] = mapped_column(Text)
    ephemeral: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    required_role_id: Mapped[int | None] = mapped_column(BigInteger)
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=5, server_default="5")
    uses: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_by: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Giveaway(Base):
    __tablename__ = "giveaways"
    __table_args__ = (Index("ix_giveaways_guild_ended", "guild_id", "ended"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger)
    channel_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int | None] = mapped_column(BigInteger)
    prize: Mapped[str] = mapped_column(String(200))
    winner_count: Mapped[int] = mapped_column(Integer)
    host_id: Mapped[int] = mapped_column(BigInteger)
    required_role_id: Mapped[int | None] = mapped_column(BigInteger)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    winners: Mapped[list[int]] = mapped_column(JSONB, default=list, server_default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GiveawayEntry(Base):
    __tablename__ = "giveaway_entries"

    giveaway_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("giveaways.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Reminder(Base):
    __tablename__ = "reminders"
    __table_args__ = (Index("ix_reminders_guild_user", "guild_id", "user_id", "state"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger)
    user_id: Mapped[int] = mapped_column(BigInteger)
    channel_id: Mapped[int | None] = mapped_column(BigInteger)
    text: Mapped[str] = mapped_column(String(500))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(10), default="pending")  # pending|done|cancelled
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StarboardEntry(Base):
    """Eine Nachricht, die Sterne bekommen hat, und ihre Kopie im Starboard-Kanal."""

    __tablename__ = "starboard_entries"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    message_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    channel_id: Mapped[int] = mapped_column(BigInteger)
    author_id: Mapped[int] = mapped_column(BigInteger)
    star_message_id: Mapped[int | None] = mapped_column(BigInteger)
    # Verhindert, dass zwei gleichzeitige Sterne dieselbe Nachricht zweimal posten.
    posting: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StarboardVote(Base):
    """Wer eine Nachricht mit dem Stern markiert hat. Der Primärschlüssel zählt jeden einmal."""

    __tablename__ = "starboard_votes"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    message_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)


class Birthday(Base):
    """Nur Tag und Monat, bewusst ohne Jahr: so lässt sich daraus kein Alter ableiten."""

    __tablename__ = "birthdays"
    __table_args__ = (Index("ix_birthdays_guild_month_day", "guild_id", "month", "day"),)

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    day: Mapped[int] = mapped_column(SmallInteger)
    month: Mapped[int] = mapped_column(SmallInteger)


class StatChannel(Base):
    __tablename__ = "stat_channels"
    __table_args__ = (UniqueConstraint("channel_id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    channel_id: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(16))
    template: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ScheduledMessage(Base):
    __tablename__ = "scheduled_messages"
    __table_args__ = (Index("ix_scheduled_messages_guild_state", "guild_id", "state"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger)
    channel_id: Mapped[int] = mapped_column(BigInteger)
    text: Mapped[str] = mapped_column(String(1500))
    next_run: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    interval_seconds: Mapped[int | None] = mapped_column(Integer)
    created_by: Mapped[int] = mapped_column(BigInteger)
    state: Mapped[str] = mapped_column(String(10), default="active")  # active | done
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
