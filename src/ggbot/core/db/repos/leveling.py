from datetime import datetime
from typing import Any, Literal

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ggbot.core.db.models import RankCardStyle, XpMember
from ggbot.core.leveling.xp import MAX_XP

ImportMode = Literal["replace", "max", "add"]
CHUNK = 1000


def _clamp(expression: Any) -> Any:
    return func.greatest(0, func.least(expression, MAX_XP))


async def add_xp(
    session: AsyncSession,
    guild_id: int,
    user_id: int,
    amount: int,
    *,
    messages: int = 0,
    voice_seconds: int = 0,
    now: datetime,
) -> int:
    """Atomar (`xp = xp + amount` in der Datenbank): parallele Gewinne gehen nicht verloren.

    Liefert die neuen Gesamt-XP. Negative Beträge sind erlaubt (Moderation), unter 0 geht es nie.
    """
    initial = max(0, min(amount, MAX_XP))
    stmt = (
        insert(XpMember)
        .values(
            guild_id=guild_id,
            user_id=user_id,
            xp=initial,
            msg_count=messages,
            voice_seconds=voice_seconds,
            last_xp_at=now,
        )
        .on_conflict_do_update(
            index_elements=[XpMember.guild_id, XpMember.user_id],
            set_={
                "xp": _clamp(XpMember.xp + amount),
                "msg_count": XpMember.msg_count + messages,
                "voice_seconds": XpMember.voice_seconds + voice_seconds,
                "last_xp_at": now,
            },
        )
        .returning(XpMember.xp)
    )
    value: int = (await session.execute(stmt)).scalar_one()
    return value


async def get(session: AsyncSession, guild_id: int, user_id: int) -> XpMember | None:
    result = await session.execute(
        select(XpMember)
        .where(XpMember.guild_id == guild_id, XpMember.user_id == user_id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def set_xp(session: AsyncSession, guild_id: int, user_id: int, xp: int) -> None:
    clamped = max(0, min(xp, MAX_XP))
    stmt = insert(XpMember).values(guild_id=guild_id, user_id=user_id, xp=clamped)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[XpMember.guild_id, XpMember.user_id], set_={"xp": clamped}
        )
    )


async def position(session: AsyncSession, guild_id: int, user_id: int, xp: int) -> int:
    """Platz 1, 2, 3 … Bei gleichen XP entscheidet die Nutzer-ID, genau wie in `top`."""
    ahead = await session.execute(
        select(func.count())
        .select_from(XpMember)
        .where(
            XpMember.guild_id == guild_id,
            or_(XpMember.xp > xp, and_(XpMember.xp == xp, XpMember.user_id < user_id)),
        )
    )
    return int(ahead.scalar_one()) + 1


async def top(session: AsyncSession, guild_id: int, *, limit: int, offset: int) -> list[XpMember]:
    rows = await session.execute(
        select(XpMember)
        .where(XpMember.guild_id == guild_id, XpMember.xp > 0)
        .order_by(XpMember.xp.desc(), XpMember.user_id)
        .limit(limit)
        .offset(offset)
    )
    return list(rows.scalars())


async def count(session: AsyncSession, guild_id: int) -> int:
    value = await session.execute(
        select(func.count())
        .select_from(XpMember)
        .where(XpMember.guild_id == guild_id, XpMember.xp > 0)
    )
    return int(value.scalar_one())


async def delete_user(session: AsyncSession, guild_id: int, user_id: int) -> bool:
    result = await session.execute(
        delete(XpMember).where(XpMember.guild_id == guild_id, XpMember.user_id == user_id)
    )
    return int(result.rowcount) > 0  # type: ignore[attr-defined]


async def delete_guild(session: AsyncSession, guild_id: int) -> int:
    result = await session.execute(delete(XpMember).where(XpMember.guild_id == guild_id))
    return int(result.rowcount)  # type: ignore[attr-defined]


async def bulk_upsert(
    session: AsyncSession, guild_id: int, rows: list[tuple[int, int]], mode: ImportMode
) -> None:
    """replace: Import gewinnt. max: der höhere Wert bleibt (sicherer Standard). add: addieren."""
    for start in range(0, len(rows), CHUNK):
        chunk = rows[start : start + CHUNK]
        stmt = insert(XpMember).values(
            [{"guild_id": guild_id, "user_id": uid, "xp": xp} for uid, xp in chunk]
        )
        excluded = stmt.excluded.xp
        new_value = {
            "replace": excluded,
            "max": func.greatest(XpMember.xp, excluded),
            "add": _clamp(XpMember.xp + excluded),
        }[mode]
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=[XpMember.guild_id, XpMember.user_id], set_={"xp": new_value}
            )
        )


async def existing_ids(session: AsyncSession, guild_id: int, user_ids: list[int]) -> set[int]:
    found: set[int] = set()
    for start in range(0, len(user_ids), CHUNK):
        chunk = user_ids[start : start + CHUNK]
        rows = await session.execute(
            select(XpMember.user_id).where(
                XpMember.guild_id == guild_id, XpMember.user_id.in_(chunk)
            )
        )
        found.update(rows.scalars())
    return found


async def get_style(session: AsyncSession, guild_id: int, user_id: int) -> dict[str, Any] | None:
    row = await session.get(RankCardStyle, (guild_id, user_id))
    return dict(row.style) if row else None


async def set_style(
    session: AsyncSession, guild_id: int, user_id: int, style: dict[str, Any]
) -> None:
    stmt = insert(RankCardStyle).values(guild_id=guild_id, user_id=user_id, style=style)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[RankCardStyle.guild_id, RankCardStyle.user_id],
            set_={"style": style, "updated_at": func.now()},
        )
    )


async def delete_style(session: AsyncSession, guild_id: int, user_id: int) -> None:
    await session.execute(
        delete(RankCardStyle).where(
            RankCardStyle.guild_id == guild_id, RankCardStyle.user_id == user_id
        )
    )
