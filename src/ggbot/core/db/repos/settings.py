from typing import Any

from sqlalchemy import func, select, text
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


async def list_audit(
    session: AsyncSession, guild_id: int, *, limit: int, before_id: int | None = None
) -> list[DashboardAudit]:
    """Neueste zuerst; `before_id` blättert (Einträge mit kleinerer id)."""
    stmt = select(DashboardAudit).where(DashboardAudit.guild_id == guild_id)
    if before_id is not None:
        stmt = stmt.where(DashboardAudit.id < before_id)
    result = await session.execute(stmt.order_by(DashboardAudit.id.desc()).limit(limit))
    return list(result.scalars())


async def lock(session: AsyncSession, guild_id: int, module: str) -> None:
    """Serialisiert Schreiber desselben (Server, Modul) bis zum Ende der Transaktion.

    Ohne das könnten zwei gleichzeitige Dashboard-Speicherungen beide denselben
    „Vorher"-Stand lesen; das Änderungsprotokoll zeigte dann eine Kette, die es nie gab.
    """
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:name, 0))"),
        {"name": f"settings:{guild_id}:{module}"},
    )
