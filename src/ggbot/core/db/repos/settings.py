from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ggbot.core.db.models import DashboardAudit, GuildSetting


async def get(session: AsyncSession, guild_id: int, module: str) -> dict[str, Any] | None:
    result = await session.execute(
        select(GuildSetting.data).where(
            GuildSetting.guild_id == guild_id, GuildSetting.module == module
        )
    )
    return result.scalar_one_or_none()


async def upsert(
    session: AsyncSession, guild_id: int, module: str, data: dict[str, Any], version: int
) -> None:
    stmt = insert(GuildSetting).values(guild_id=guild_id, module=module, data=data, version=version)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[GuildSetting.guild_id, GuildSetting.module],
            set_={
                "data": stmt.excluded.data,
                "version": stmt.excluded.version,
                "updated_at": func.now(),
            },
        )
    )


async def add_audit(
    session: AsyncSession,
    guild_id: int,
    user_id: int,
    module: str,
    before: dict[str, Any] | None,
    after: dict[str, Any],
) -> None:
    session.add(
        DashboardAudit(
            guild_id=guild_id, user_id=user_id, module=module, before=before, after=after
        )
    )
