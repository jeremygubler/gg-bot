from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
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


class RolePanel(Base):
    """Ein Rollen-Panel: eine Nachricht mit Buttons, Auswahlmenü oder Reaktionen."""

    __tablename__ = "role_panels"
    __table_args__ = (
        Index("ix_role_panels_guild_id", "guild_id"),
        Index("ix_role_panels_message_id", "message_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    guild_id: Mapped[int] = mapped_column(BigInteger)
    channel_id: Mapped[int | None] = mapped_column(BigInteger)
    message_id: Mapped[int | None] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(8))  # button | select | reaction
    mode: Mapped[str] = mapped_column(String(8))  # normal | unique | verify
    title: Mapped[str] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(Text)
    # Reaktions-Panel an einer fremden Nachricht: der Bot besitzt sie nicht und ändert sie nicht.
    external: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RolePanelItem(Base):
    __tablename__ = "role_panel_items"
    __table_args__ = (
        UniqueConstraint("panel_id", "role_id"),
        UniqueConstraint(
            "panel_id", "emoji_key"
        ),  # NULL zählt als verschieden: mehrere ohne Emoji ok
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    panel_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("role_panels.id", ondelete="CASCADE"), index=True
    )
    guild_id: Mapped[int] = mapped_column(BigInteger)
    role_id: Mapped[int] = mapped_column(BigInteger)
    label: Mapped[str | None] = mapped_column(String(80))
    emoji: Mapped[str | None] = mapped_column(String(64))
    emoji_key: Mapped[str | None] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(String(100))
    position: Mapped[int] = mapped_column(Integer)
