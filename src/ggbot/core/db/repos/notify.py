from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import cast

from ggbot.core.db.models import NotifySubscription as Sub


async def add(session: AsyncSession, **values: Any) -> Sub:
    sub = Sub(**values)
    session.add(sub)
    await session.flush()
    await session.refresh(sub)
    return sub


async def get(session: AsyncSession, guild_id: int, sub_id: int) -> Sub | None:
    result = await session.execute(
        select(Sub)
        .where(Sub.guild_id == guild_id, Sub.id == sub_id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def by_ref(session: AsyncSession, guild_id: int, provider: str, ref: str) -> Sub | None:
    result = await session.execute(
        select(Sub)
        .where(Sub.guild_id == guild_id, Sub.provider == provider, Sub.ref == ref)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def list_for_guild(session: AsyncSession, guild_id: int) -> list[Sub]:
    rows = await session.execute(
        select(Sub).where(Sub.guild_id == guild_id).order_by(Sub.provider, Sub.display_name, Sub.id)
    )
    return list(rows.scalars())


async def count(session: AsyncSession, guild_id: int) -> int:
    value = await session.execute(
        select(func.count()).select_from(Sub).where(Sub.guild_id == guild_id)
    )
    return int(value.scalar_one())


async def delete_sub(session: AsyncSession, guild_id: int, sub_id: int) -> bool:
    result = await session.execute(delete(Sub).where(Sub.guild_id == guild_id, Sub.id == sub_id))
    return bool(result.rowcount)  # type: ignore[attr-defined]


async def all_for_provider(session: AsyncSession, provider: str) -> list[Sub]:
    """Über alle Server: die Abfrage bei Twitch/YouTube ist pro Streamer, nicht pro Server.

    Das ist bewusst die einzige Abfrage ohne `guild_id`; wer ihr Ergebnis auswertet, handelt je
    Eintrag mit dessen eigener `guild_id`.
    """
    rows = await session.execute(select(Sub).where(Sub.provider == provider).order_by(Sub.id))
    return list(rows.scalars())


async def claim_stream(
    session: AsyncSession, sub_id: int, *, old_key: str | None, new_key: str
) -> bool:
    """Atomar: nur wer den bisherigen Stand sieht, darf diesen Stream ankündigen."""
    result = await session.execute(
        update(Sub)
        .where(Sub.id == sub_id, Sub.last_key.is_not_distinct_from(old_key))
        .values(last_key=new_key, live=True, misses=0)
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]


async def save_stream_state(session: AsyncSession, sub_id: int, *, live: bool, misses: int) -> None:
    await session.execute(update(Sub).where(Sub.id == sub_id).values(live=live, misses=misses))


async def revert_stream(
    session: AsyncSession,
    sub_id: int,
    *,
    new_key: str,
    old_key: str | None,
    live: bool,
    misses: int,
) -> None:
    await session.execute(
        update(Sub)
        .where(Sub.id == sub_id, Sub.last_key == new_key)
        .values(last_key=old_key, live=live, misses=misses)
    )


async def set_message(session: AsyncSession, sub_id: int, message_id: int | None) -> None:
    await session.execute(update(Sub).where(Sub.id == sub_id).values(message_id=message_id))


async def claim_videos(
    session: AsyncSession, sub_id: int, *, old: list[str], new: list[str]
) -> bool:
    """Atomar: nur wer den bisherigen Stand sieht, darf die neuen Videos ankündigen."""
    result = await session.execute(
        update(Sub).where(Sub.id == sub_id, Sub.seen == cast(old, JSONB)).values(seen=new)
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]
