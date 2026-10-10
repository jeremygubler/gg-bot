from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ggbot.core.db.models import CustomCommand, Giveaway, GiveawayEntry, Reminder

# --- Eigene Befehle ---


async def cc_get(session: AsyncSession, guild_id: int, name: str) -> CustomCommand | None:
    result = await session.execute(
        select(CustomCommand)
        .where(CustomCommand.guild_id == guild_id, CustomCommand.name == name)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def cc_list(session: AsyncSession, guild_id: int) -> list[CustomCommand]:
    rows = await session.execute(
        select(CustomCommand).where(CustomCommand.guild_id == guild_id).order_by(CustomCommand.name)
    )
    return list(rows.scalars())


async def cc_count(session: AsyncSession, guild_id: int) -> int:
    value = await session.execute(
        select(func.count()).select_from(CustomCommand).where(CustomCommand.guild_id == guild_id)
    )
    return int(value.scalar_one())


async def cc_add(session: AsyncSession, **values: Any) -> CustomCommand:
    command = CustomCommand(**values)
    session.add(command)
    await session.flush()
    await session.refresh(command)
    return command


async def cc_delete(session: AsyncSession, guild_id: int, name: str) -> bool:
    result = await session.execute(
        delete(CustomCommand).where(CustomCommand.guild_id == guild_id, CustomCommand.name == name)
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]


async def cc_use(session: AsyncSession, guild_id: int, name: str) -> CustomCommand | None:
    """Zählt einen Aufruf atomar mit und liefert den Befehl mit dem neuen Stand."""
    result = await session.execute(
        update(CustomCommand)
        .where(CustomCommand.guild_id == guild_id, CustomCommand.name == name)
        .values(uses=CustomCommand.uses + 1)
        .returning(CustomCommand)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


# --- Giveaways ---


async def gw_create(session: AsyncSession, **values: Any) -> Giveaway:
    giveaway = Giveaway(**values)
    session.add(giveaway)
    await session.flush()
    await session.refresh(giveaway)
    return giveaway


async def gw_get(session: AsyncSession, guild_id: int, giveaway_id: int) -> Giveaway | None:
    result = await session.execute(
        select(Giveaway)
        .where(Giveaway.guild_id == guild_id, Giveaway.id == giveaway_id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def gw_active(session: AsyncSession, guild_id: int) -> list[Giveaway]:
    rows = await session.execute(
        select(Giveaway)
        .where(Giveaway.guild_id == guild_id, Giveaway.ended.is_(False))
        .order_by(Giveaway.ends_at)
    )
    return list(rows.scalars())


async def gw_set_message(
    session: AsyncSession, guild_id: int, giveaway_id: int, message_id: int
) -> None:
    await session.execute(
        update(Giveaway)
        .where(Giveaway.guild_id == guild_id, Giveaway.id == giveaway_id)
        .values(message_id=message_id)
    )


async def gw_end(session: AsyncSession, guild_id: int, giveaway_id: int) -> Giveaway | None:
    """Beendet genau einmal: wer zu spät kommt, bekommt None."""
    result = await session.execute(
        update(Giveaway)
        .where(Giveaway.guild_id == guild_id, Giveaway.id == giveaway_id, Giveaway.ended.is_(False))
        .values(ended=True)
        .returning(Giveaway)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def gw_set_winners(
    session: AsyncSession, guild_id: int, giveaway_id: int, winners: list[int]
) -> None:
    await session.execute(
        update(Giveaway)
        .where(Giveaway.guild_id == guild_id, Giveaway.id == giveaway_id)
        .values(winners=winners)
    )


async def gw_delete(session: AsyncSession, guild_id: int, giveaway_id: int) -> bool:
    result = await session.execute(
        delete(Giveaway).where(Giveaway.guild_id == guild_id, Giveaway.id == giveaway_id)
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]


async def gw_entries(session: AsyncSession, guild_id: int, giveaway_id: int) -> list[int]:
    rows = await session.execute(
        select(GiveawayEntry.user_id)
        .where(GiveawayEntry.guild_id == guild_id, GiveawayEntry.giveaway_id == giveaway_id)
        .order_by(GiveawayEntry.user_id)
    )
    return list(rows.scalars())


async def gw_enter(session: AsyncSession, guild_id: int, giveaway_id: int, user_id: int) -> bool:
    """True, wenn neu eingetragen; False, wenn schon dabei."""
    stmt = (
        insert(GiveawayEntry)
        .values(giveaway_id=giveaway_id, user_id=user_id, guild_id=guild_id)
        .on_conflict_do_nothing()
        .returning(GiveawayEntry.user_id)
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def gw_leave(session: AsyncSession, guild_id: int, giveaway_id: int, user_id: int) -> bool:
    result = await session.execute(
        delete(GiveawayEntry).where(
            GiveawayEntry.guild_id == guild_id,
            GiveawayEntry.giveaway_id == giveaway_id,
            GiveawayEntry.user_id == user_id,
        )
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]


async def gw_entry_count(session: AsyncSession, guild_id: int, giveaway_id: int) -> int:
    value = await session.execute(
        select(func.count())
        .select_from(GiveawayEntry)
        .where(GiveawayEntry.guild_id == guild_id, GiveawayEntry.giveaway_id == giveaway_id)
    )
    return int(value.scalar_one())


# --- Erinnerungen ---


async def rm_create(session: AsyncSession, **values: Any) -> Reminder:
    reminder = Reminder(**values)
    session.add(reminder)
    await session.flush()
    await session.refresh(reminder)
    return reminder


async def rm_pending(session: AsyncSession, guild_id: int, user_id: int) -> list[Reminder]:
    rows = await session.execute(
        select(Reminder)
        .where(
            Reminder.guild_id == guild_id,
            Reminder.user_id == user_id,
            Reminder.state == "pending",
        )
        .order_by(Reminder.due_at)
    )
    return list(rows.scalars())


async def rm_fire(session: AsyncSession, guild_id: int, reminder_id: int) -> Reminder | None:
    """Genau einmal: nur eine noch offene Erinnerung wird ausgeliefert."""
    result = await session.execute(
        update(Reminder)
        .where(
            Reminder.guild_id == guild_id, Reminder.id == reminder_id, Reminder.state == "pending"
        )
        .values(state="done")
        .returning(Reminder)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def rm_cancel(session: AsyncSession, guild_id: int, user_id: int, reminder_id: int) -> bool:
    result = await session.execute(
        update(Reminder)
        .where(
            Reminder.guild_id == guild_id,
            Reminder.user_id == user_id,
            Reminder.id == reminder_id,
            Reminder.state == "pending",
        )
        .values(state="cancelled")
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]


async def rm_purge(session: AsyncSession, before: datetime) -> int:
    """Erledigte und abgebrochene Erinnerungen aufräumen (nicht serverbezogen: reine Pflege)."""
    result = await session.execute(
        delete(Reminder).where(Reminder.state != "pending", Reminder.due_at < before)
    )
    return int(result.rowcount)  # type: ignore[attr-defined]


async def rm_cancel_all(session: AsyncSession, guild_id: int, user_id: int) -> int:
    result = await session.execute(
        update(Reminder)
        .where(
            Reminder.guild_id == guild_id, Reminder.user_id == user_id, Reminder.state == "pending"
        )
        .values(state="cancelled")
    )
    return int(result.rowcount)  # type: ignore[attr-defined]
