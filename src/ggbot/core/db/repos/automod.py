from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ggbot.core.db.models import AutomodHit


async def add_hit(
    session: AsyncSession,
    *,
    guild_id: int,
    user_id: int,
    channel_id: int | None,
    rule: str,
    actions: str,
) -> None:
    session.add(
        AutomodHit(
            guild_id=guild_id, user_id=user_id, channel_id=channel_id, rule=rule, actions=actions
        )
    )


async def count_by_rule(session: AsyncSession, guild_id: int, since: datetime) -> dict[str, int]:
    rows = await session.execute(
        select(AutomodHit.rule, func.count())
        .where(AutomodHit.guild_id == guild_id, AutomodHit.created_at >= since)
        .group_by(AutomodHit.rule)
    )
    return {rule: count for rule, count in rows.all()}


async def purge_older_than(session: AsyncSession, cutoff: datetime) -> int:
    result = await session.execute(delete(AutomodHit).where(AutomodHit.created_at < cutoff))
    return int(result.rowcount)  # type: ignore[attr-defined]
