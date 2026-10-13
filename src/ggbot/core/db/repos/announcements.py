from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ggbot.core.db.models import Announcement
from ggbot.core.db.models.announcements import STATE_SCHEDULED, STATE_SENT


async def create(
    session: AsyncSession,
    guild_id: int,
    created_by: int,
    name: str,
    content: str | None,
    embed: dict[str, Any] | None,
) -> Announcement:
    row = Announcement(
        guild_id=guild_id, created_by=created_by, name=name, content=content, embed=embed
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


async def get(session: AsyncSession, guild_id: int, announcement_id: int) -> Announcement | None:
    result = await session.execute(
        select(Announcement)
        .where(Announcement.guild_id == guild_id, Announcement.id == announcement_id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def get_any(session: AsyncSession, announcement_id: int) -> Announcement | None:
    """Für den Job: er kennt nur die Nummer; der Server steht in der Zeile."""
    result = await session.execute(
        select(Announcement)
        .where(Announcement.id == announcement_id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def list_for_guild(session: AsyncSession, guild_id: int) -> list[Announcement]:
    rows = await session.execute(
        select(Announcement)
        .where(Announcement.guild_id == guild_id)
        .order_by(Announcement.id.desc())
    )
    return list(rows.scalars())


async def count(session: AsyncSession, guild_id: int) -> int:
    value = await session.execute(
        select(func.count()).select_from(Announcement).where(Announcement.guild_id == guild_id)
    )
    return int(value.scalar_one())


async def delete_one(session: AsyncSession, guild_id: int, announcement_id: int) -> bool:
    result = await session.execute(
        delete(Announcement).where(
            Announcement.guild_id == guild_id, Announcement.id == announcement_id
        )
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]


async def update_content(
    session: AsyncSession,
    guild_id: int,
    announcement_id: int,
    name: str,
    content: str | None,
    embed: dict[str, Any] | None,
) -> Announcement | None:
    result = await session.execute(
        update(Announcement)
        .where(Announcement.guild_id == guild_id, Announcement.id == announcement_id)
        .values(name=name, content=content, embed=embed)
        .returning(Announcement)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def schedule(
    session: AsyncSession,
    guild_id: int,
    announcement_id: int,
    channel_id: int,
    run_at: datetime,
    states: tuple[str, ...],
) -> Announcement | None:
    """Plant ein; nur aus einem der erlaubten Zustände heraus (bedingtes UPDATE)."""
    result = await session.execute(
        update(Announcement)
        .where(
            Announcement.guild_id == guild_id,
            Announcement.id == announcement_id,
            Announcement.state.in_(states),
        )
        .values(
            state=STATE_SCHEDULED,
            channel_id=channel_id,
            scheduled_at=run_at,
            rev=Announcement.rev + 1,
            error_key=None,
        )
        .returning(Announcement)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def cancel(session: AsyncSession, guild_id: int, announcement_id: int) -> Announcement | None:
    result = await session.execute(
        update(Announcement)
        .where(
            Announcement.guild_id == guild_id,
            Announcement.id == announcement_id,
            Announcement.state == STATE_SCHEDULED,
        )
        .values(state="draft", scheduled_at=None, rev=Announcement.rev + 1)
        .returning(Announcement)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def mark_sent(
    session: AsyncSession, announcement_id: int, rev: int, message_id: int, now: datetime
) -> bool:
    result = await session.execute(
        update(Announcement)
        .where(
            Announcement.id == announcement_id,
            Announcement.state == STATE_SCHEDULED,
            Announcement.rev == rev,
        )
        .values(state=STATE_SENT, message_id=message_id, sent_at=now, error_key=None)
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]


async def mark_failed(
    session: AsyncSession, announcement_id: int, rev: int, error_key: str
) -> bool:
    result = await session.execute(
        update(Announcement)
        .where(
            Announcement.id == announcement_id,
            Announcement.state == STATE_SCHEDULED,
            Announcement.rev == rev,
        )
        .values(state="failed", error_key=error_key)
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]


async def set_edit_result(
    session: AsyncSession, announcement_id: int, error_key: str | None
) -> None:
    await session.execute(
        update(Announcement)
        .where(Announcement.id == announcement_id, Announcement.state == STATE_SENT)
        .values(error_key=error_key)
    )
