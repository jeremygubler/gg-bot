from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ggbot.core.db.models import ChannelLock, ModCase, ModCaseCounter, ModNote


async def next_case_no(session: AsyncSession, guild_id: int) -> int:
    """Atomar: parallele Aktionen im selben Server bekommen verschiedene Nummern."""
    stmt = (
        insert(ModCaseCounter)
        .values(guild_id=guild_id, last_no=1)
        .on_conflict_do_update(
            index_elements=[ModCaseCounter.guild_id],
            set_={"last_no": ModCaseCounter.last_no + 1},
        )
        .returning(ModCaseCounter.last_no)
    )
    number: int = (await session.execute(stmt)).scalar_one()
    return number


async def create_case(
    session: AsyncSession,
    *,
    guild_id: int,
    type: str,
    target_id: int,
    moderator_id: int,
    reason: str | None,
    duration_seconds: int | None = None,
    expires_at: datetime | None = None,
    ref_case_no: int | None = None,
    active: bool = True,
) -> ModCase:
    case = ModCase(
        guild_id=guild_id,
        case_no=await next_case_no(session, guild_id),
        type=type,
        target_id=target_id,
        moderator_id=moderator_id,
        reason=reason,
        duration_seconds=duration_seconds,
        expires_at=expires_at,
        ref_case_no=ref_case_no,
        active=active,
    )
    session.add(case)
    await session.flush()
    await session.refresh(case)
    return case


async def get_case(session: AsyncSession, guild_id: int, case_no: int) -> ModCase | None:
    result = await session.execute(
        select(ModCase).where(ModCase.guild_id == guild_id, ModCase.case_no == case_no)
    )
    return result.scalar_one_or_none()


async def list_cases(
    session: AsyncSession, guild_id: int, target_id: int, *, limit: int, offset: int
) -> tuple[list[ModCase], int]:
    """Neueste zuerst, dazu die Gesamtzahl für die Seitenanzeige."""
    where = (ModCase.guild_id == guild_id, ModCase.target_id == target_id)
    total = (
        await session.execute(select(func.count()).select_from(ModCase).where(*where))
    ).scalar_one()
    rows = await session.execute(
        select(ModCase).where(*where).order_by(ModCase.case_no.desc()).limit(limit).offset(offset)
    )
    return list(rows.scalars()), total


async def count_active_warns(
    session: AsyncSession, guild_id: int, target_id: int, *, since: datetime | None
) -> int:
    where = [
        ModCase.guild_id == guild_id,
        ModCase.target_id == target_id,
        ModCase.type == "warn",
        ModCase.active.is_(True),
    ]
    if since is not None:
        where.append(ModCase.created_at >= since)
    return (
        await session.execute(select(func.count()).select_from(ModCase).where(*where))
    ).scalar_one()


async def deactivate_case(session: AsyncSession, guild_id: int, case_no: int) -> None:
    await session.execute(
        update(ModCase)
        .where(ModCase.guild_id == guild_id, ModCase.case_no == case_no)
        .values(active=False)
    )


async def deactivate_active(
    session: AsyncSession,
    guild_id: int,
    target_id: int,
    type: str,
) -> int:
    result = await session.execute(
        update(ModCase)
        .where(
            ModCase.guild_id == guild_id,
            ModCase.target_id == target_id,
            ModCase.type == type,
            ModCase.active.is_(True),
        )
        .values(active=False)
    )
    return int(result.rowcount)  # type: ignore[attr-defined]


async def add_note(
    session: AsyncSession, guild_id: int, target_id: int, author_id: int, content: str
) -> ModNote:
    note = ModNote(guild_id=guild_id, target_id=target_id, author_id=author_id, content=content)
    session.add(note)
    await session.flush()
    await session.refresh(note)
    return note


async def list_notes(session: AsyncSession, guild_id: int, target_id: int) -> list[ModNote]:
    rows = await session.execute(
        select(ModNote)
        .where(ModNote.guild_id == guild_id, ModNote.target_id == target_id)
        .order_by(ModNote.id)
    )
    return list(rows.scalars())


async def delete_note(session: AsyncSession, guild_id: int, note_id: int) -> bool:
    result = await session.execute(
        delete(ModNote).where(ModNote.guild_id == guild_id, ModNote.id == note_id)
    )
    return int(result.rowcount) > 0  # type: ignore[attr-defined]


async def get_lock(session: AsyncSession, guild_id: int, channel_id: int) -> ChannelLock | None:
    return await session.get(ChannelLock, (guild_id, channel_id))


async def save_lock(
    session: AsyncSession,
    guild_id: int,
    channel_id: int,
    previous_send_messages: bool | None,
    locked_by: int,
) -> None:
    session.add(
        ChannelLock(
            guild_id=guild_id,
            channel_id=channel_id,
            previous_send_messages=previous_send_messages,
            locked_by=locked_by,
        )
    )
    await session.flush()


async def delete_lock(session: AsyncSession, guild_id: int, channel_id: int) -> None:
    await session.execute(
        delete(ChannelLock).where(
            ChannelLock.guild_id == guild_id, ChannelLock.channel_id == channel_id
        )
    )


async def deactivate_active_before(
    session: AsyncSession, guild_id: int, target_id: int, newer_case_no: int
) -> None:
    """Ein neuer Tempban ersetzt frühere: deren Jobs laufen ins Leere."""
    await session.execute(
        update(ModCase)
        .where(
            ModCase.guild_id == guild_id,
            ModCase.target_id == target_id,
            ModCase.type == "tempban",
            ModCase.active.is_(True),
            ModCase.case_no < newer_case_no,
        )
        .values(active=False)
    )
