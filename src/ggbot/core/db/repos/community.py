from datetime import date, datetime
from typing import Any

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ggbot.core.db.models import (
    Birthday,
    ScheduledMessage,
    StarboardEntry,
    StarboardVote,
    StatChannel,
)

# --- Starboard ---


async def sb_vote(
    session: AsyncSession,
    guild_id: int,
    message_id: int,
    user_id: int,
    *,
    channel_id: int,
    author_id: int,
) -> int:
    """Trägt den Stern ein (jeder Nutzer einmal) und liefert den neuen Stand."""
    await session.execute(
        insert(StarboardEntry)
        .values(
            guild_id=guild_id, message_id=message_id, channel_id=channel_id, author_id=author_id
        )
        .on_conflict_do_nothing()
    )
    await session.execute(
        insert(StarboardVote)
        .values(guild_id=guild_id, message_id=message_id, user_id=user_id)
        .on_conflict_do_nothing()
    )
    return await sb_count(session, guild_id, message_id)


async def sb_unvote(session: AsyncSession, guild_id: int, message_id: int, user_id: int) -> int:
    await session.execute(
        delete(StarboardVote).where(
            StarboardVote.guild_id == guild_id,
            StarboardVote.message_id == message_id,
            StarboardVote.user_id == user_id,
        )
    )
    return await sb_count(session, guild_id, message_id)


async def sb_count(session: AsyncSession, guild_id: int, message_id: int) -> int:
    value = await session.execute(
        select(func.count())
        .select_from(StarboardVote)
        .where(StarboardVote.guild_id == guild_id, StarboardVote.message_id == message_id)
    )
    return int(value.scalar_one())


async def sb_entry(session: AsyncSession, guild_id: int, message_id: int) -> StarboardEntry | None:
    result = await session.execute(
        select(StarboardEntry)
        .where(StarboardEntry.guild_id == guild_id, StarboardEntry.message_id == message_id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def sb_copy_ids(session: AsyncSession, guild_id: int) -> list[int]:
    """Nachrichten-IDs der Starboard-Kopien eines Servers."""
    rows = await session.execute(
        select(StarboardEntry.star_message_id).where(StarboardEntry.guild_id == guild_id)
    )
    return [i for i in rows.scalars() if i is not None]


async def sb_claim_post(session: AsyncSession, guild_id: int, message_id: int) -> bool:
    """Genau einer darf eine noch nicht geposte Nachricht posten."""
    result = await session.execute(
        update(StarboardEntry)
        .where(
            StarboardEntry.guild_id == guild_id,
            StarboardEntry.message_id == message_id,
            StarboardEntry.star_message_id.is_(None),
            StarboardEntry.posting.is_(False),
        )
        .values(posting=True)
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]


async def sb_finish_post(
    session: AsyncSession, guild_id: int, message_id: int, star_message_id: int | None
) -> None:
    await session.execute(
        update(StarboardEntry)
        .where(StarboardEntry.guild_id == guild_id, StarboardEntry.message_id == message_id)
        .values(star_message_id=star_message_id, posting=False)
    )


async def sb_delete_entry(
    session: AsyncSession, guild_id: int, message_id: int
) -> StarboardEntry | None:
    entry = await sb_entry(session, guild_id, message_id)
    if entry is None:
        return None
    await session.execute(
        delete(StarboardVote).where(
            StarboardVote.guild_id == guild_id, StarboardVote.message_id == message_id
        )
    )
    await session.execute(
        delete(StarboardEntry).where(
            StarboardEntry.guild_id == guild_id, StarboardEntry.message_id == message_id
        )
    )
    return entry


# --- Geburtstage ---


async def bd_set(session: AsyncSession, guild_id: int, user_id: int, day: int, month: int) -> None:
    stmt = insert(Birthday).values(guild_id=guild_id, user_id=user_id, day=day, month=month)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[Birthday.guild_id, Birthday.user_id],
            set_={"day": day, "month": month},
        )
    )


async def bd_get(session: AsyncSession, guild_id: int, user_id: int) -> Birthday | None:
    result = await session.execute(
        select(Birthday)
        .where(Birthday.guild_id == guild_id, Birthday.user_id == user_id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def bd_delete(session: AsyncSession, guild_id: int, user_id: int) -> bool:
    result = await session.execute(
        delete(Birthday).where(Birthday.guild_id == guild_id, Birthday.user_id == user_id)
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]


async def bd_on(session: AsyncSession, guild_id: int, today: date, leap_year: bool) -> list[int]:
    """Wer heute feiert. Der 29. Februar rückt in Nicht-Schaltjahren auf den 28."""
    condition = and_(Birthday.day == today.day, Birthday.month == today.month)
    if (today.day, today.month) == (28, 2) and not leap_year:
        condition = or_(condition, and_(Birthday.day == 29, Birthday.month == 2))
    rows = await session.execute(
        select(Birthday.user_id)
        .where(Birthday.guild_id == guild_id, condition)
        .order_by(Birthday.user_id)
    )
    return list(rows.scalars())


async def bd_all(session: AsyncSession, guild_id: int) -> list[Birthday]:
    rows = await session.execute(select(Birthday).where(Birthday.guild_id == guild_id))
    return list(rows.scalars())


# --- Stat-Kanäle ---


async def st_add(session: AsyncSession, **values: Any) -> StatChannel:
    stat = StatChannel(**values)
    session.add(stat)
    await session.flush()
    await session.refresh(stat)
    return stat


async def st_list(session: AsyncSession, guild_id: int) -> list[StatChannel]:
    rows = await session.execute(
        select(StatChannel).where(StatChannel.guild_id == guild_id).order_by(StatChannel.id)
    )
    return list(rows.scalars())


async def st_delete(session: AsyncSession, guild_id: int, channel_id: int) -> bool:
    result = await session.execute(
        delete(StatChannel).where(
            StatChannel.guild_id == guild_id, StatChannel.channel_id == channel_id
        )
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]


# --- Geplante Nachrichten ---


async def sm_add(session: AsyncSession, **values: Any) -> ScheduledMessage:
    message = ScheduledMessage(**values)
    session.add(message)
    await session.flush()
    await session.refresh(message)
    return message


async def sm_list(session: AsyncSession, guild_id: int) -> list[ScheduledMessage]:
    rows = await session.execute(
        select(ScheduledMessage)
        .where(ScheduledMessage.guild_id == guild_id, ScheduledMessage.state == "active")
        .order_by(ScheduledMessage.next_run)
    )
    return list(rows.scalars())


async def sm_get(session: AsyncSession, guild_id: int, message_id: int) -> ScheduledMessage | None:
    result = await session.execute(
        select(ScheduledMessage)
        .where(ScheduledMessage.guild_id == guild_id, ScheduledMessage.id == message_id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def sm_cancel(session: AsyncSession, guild_id: int, message_id: int) -> bool:
    result = await session.execute(
        update(ScheduledMessage)
        .where(
            ScheduledMessage.guild_id == guild_id,
            ScheduledMessage.id == message_id,
            ScheduledMessage.state == "active",
        )
        .values(state="done")
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]


async def sm_claim_run(
    session: AsyncSession,
    guild_id: int,
    message_id: int,
    *,
    expected: datetime,
    new_next: datetime,
    done: bool,
) -> ScheduledMessage | None:
    """Beansprucht genau diesen Termin: wer ihn zuerst fortschreibt, darf senden."""
    result = await session.execute(
        update(ScheduledMessage)
        .where(
            ScheduledMessage.guild_id == guild_id,
            ScheduledMessage.id == message_id,
            ScheduledMessage.state == "active",
            ScheduledMessage.next_run == expected,
        )
        .values(next_run=new_next, state="done" if done else "active")
        .returning(ScheduledMessage)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def sm_count_active(session: AsyncSession, guild_id: int) -> int:
    value = await session.execute(
        select(func.count())
        .select_from(ScheduledMessage)
        .where(ScheduledMessage.guild_id == guild_id, ScheduledMessage.state == "active")
    )
    return int(value.scalar_one())
