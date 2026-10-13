from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from ggbot.core.db.base import Base


class StickyMessage(Base):
    """Eine Nachricht, die im Kanal immer ganz unten bleibt."""

    __tablename__ = "sticky_messages"

    channel_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    text: Mapped[str] = mapped_column(String(1500))
    # Die zuletzt gepostete Kopie: sie wird vor dem neuen Post gelöscht.
    last_message_id: Mapped[int | None] = mapped_column(BigInteger)
    created_by: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TempVoiceChannel(Base):
    """Ein vom Bot angelegter Sprachkanal; danach räumt der Bot nach einem Neustart auf."""

    __tablename__ = "temp_voice_channels"

    channel_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    owner_id: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
