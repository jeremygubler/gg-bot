from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ggbot.core.db.models import StickyMessage, TempVoiceChannel

# --- Sticky ---


async def sticky_upsert(
    session: AsyncSession, guild_id: int, channel_id: int, text: str, created_by: int
) -> None:
    stmt = insert(StickyMessage).values(
        guild_id=guild_id, channel_id=channel_id, text=text, created_by=created_by
    )
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[StickyMessage.channel_id],
            set_={"text": stmt.excluded.text, "created_by": stmt.excluded.created_by},
        )
    )


async def sticky_get(session: AsyncSession, channel_id: int) -> StickyMessage | None:
    result = await session.execute(
        select(StickyMessage)
        .where(StickyMessage.channel_id == channel_id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def sticky_list(session: AsyncSession, guild_id: int) -> list[StickyMessage]:
    rows = await session.execute(
        select(StickyMessage)
        .where(StickyMessage.guild_id == guild_id)
        .order_by(StickyMessage.channel_id)
    )
    return list(rows.scalars())


async def sticky_count(session: AsyncSession, guild_id: int) -> int:
    value = await session.execute(
        select(func.count()).select_from(StickyMessage).where(StickyMessage.guild_id == guild_id)
    )
    return int(value.scalar_one())


async def sticky_delete(
    session: AsyncSession, guild_id: int, channel_id: int
) -> StickyMessage | None:
    result = await session.execute(
        delete(StickyMessage)
        .where(StickyMessage.guild_id == guild_id, StickyMessage.channel_id == channel_id)
        .returning(StickyMessage)
    )
    return result.scalar_one_or_none()


async def sticky_set_last(session: AsyncSession, channel_id: int, message_id: int | None) -> None:
    await session.execute(
        update(StickyMessage)
        .where(StickyMessage.channel_id == channel_id)
        .values(last_message_id=message_id)
    )


# --- Temporäre Sprachkanäle ---


async def voice_add(session: AsyncSession, guild_id: int, channel_id: int, owner_id: int) -> None:
    await session.execute(
        insert(TempVoiceChannel)
        .values(guild_id=guild_id, channel_id=channel_id, owner_id=owner_id)
        .on_conflict_do_nothing()
    )


async def voice_owner(session: AsyncSession, channel_id: int) -> int | None:
    result = await session.execute(
        select(TempVoiceChannel.owner_id).where(TempVoiceChannel.channel_id == channel_id)
    )
    return result.scalar_one_or_none()


async def voice_remove(session: AsyncSession, channel_id: int) -> bool:
    result = await session.execute(
        delete(TempVoiceChannel).where(TempVoiceChannel.channel_id == channel_id)
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]


async def voice_list(session: AsyncSession, guild_id: int) -> list[TempVoiceChannel]:
    rows = await session.execute(
        select(TempVoiceChannel).where(TempVoiceChannel.guild_id == guild_id)
    )
    return list(rows.scalars())


async def voice_count(session: AsyncSession, guild_id: int) -> int:
    value = await session.execute(
        select(func.count())
        .select_from(TempVoiceChannel)
        .where(TempVoiceChannel.guild_id == guild_id)
    )
    return int(value.scalar_one())
