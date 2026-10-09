from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ggbot.core.db.models import Guild


async def get(session: AsyncSession, guild_id: int) -> Guild | None:
    return await session.get(Guild, guild_id)


async def ensure(session: AsyncSession, guild_id: int, *, default_locale: str) -> Guild:
    """Legt den Server an, falls er fehlt; ein Wiederbeitritt hebt `left_at` auf."""
    await session.execute(
        insert(Guild)
        .values(id=guild_id, locale=default_locale, timezone="UTC")
        .on_conflict_do_update(index_elements=[Guild.id], set_={"left_at": None})
    )
    result = await session.execute(
        select(Guild).where(Guild.id == guild_id).execution_options(populate_existing=True)
    )
    return result.scalar_one()


async def set_locale(session: AsyncSession, guild_id: int, locale: str) -> None:
    await session.execute(update(Guild).where(Guild.id == guild_id).values(locale=locale))


async def set_timezone(session: AsyncSession, guild_id: int, timezone: str) -> None:
    await session.execute(update(Guild).where(Guild.id == guild_id).values(timezone=timezone))


async def mark_left(session: AsyncSession, guild_id: int, when: datetime) -> None:
    await session.execute(update(Guild).where(Guild.id == guild_id).values(left_at=when))
