from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ggbot.core.db.models import (
    Ticket,
    TicketCategory,
    TicketCounter,
    TicketFeedback,
    TicketPanel,
    TicketTranscript,
)

# --- Panels ---


async def create_panel(
    session: AsyncSession, guild_id: int, title: str, description: str | None
) -> TicketPanel:
    panel = TicketPanel(guild_id=guild_id, title=title, description=description)
    session.add(panel)
    await session.flush()
    await session.refresh(panel)
    return panel


async def get_panel(session: AsyncSession, guild_id: int, panel_id: int) -> TicketPanel | None:
    result = await session.execute(
        select(TicketPanel)
        .where(TicketPanel.guild_id == guild_id, TicketPanel.id == panel_id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def list_panels(session: AsyncSession, guild_id: int) -> list[TicketPanel]:
    rows = await session.execute(
        select(TicketPanel).where(TicketPanel.guild_id == guild_id).order_by(TicketPanel.id)
    )
    return list(rows.scalars())


async def count_panels(session: AsyncSession, guild_id: int) -> int:
    value = await session.execute(
        select(func.count()).select_from(TicketPanel).where(TicketPanel.guild_id == guild_id)
    )
    return int(value.scalar_one())


async def delete_panel(session: AsyncSession, guild_id: int, panel_id: int) -> bool:
    result = await session.execute(
        delete(TicketPanel).where(TicketPanel.guild_id == guild_id, TicketPanel.id == panel_id)
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]


async def set_panel_message(
    session: AsyncSession,
    guild_id: int,
    panel_id: int,
    channel_id: int | None,
    message_id: int | None,
) -> bool:
    result = await session.execute(
        update(TicketPanel)
        .where(TicketPanel.guild_id == guild_id, TicketPanel.id == panel_id)
        .values(channel_id=channel_id, message_id=message_id)
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]


async def clear_panel_message(session: AsyncSession, guild_id: int, message_id: int) -> int:
    result = await session.execute(
        update(TicketPanel)
        .where(TicketPanel.guild_id == guild_id, TicketPanel.message_id == message_id)
        .values(channel_id=None, message_id=None)
    )
    return int(result.rowcount)  # type: ignore[attr-defined]


# --- Kategorien ---


async def categories(session: AsyncSession, guild_id: int, panel_id: int) -> list[TicketCategory]:
    rows = await session.execute(
        select(TicketCategory)
        .where(TicketCategory.guild_id == guild_id, TicketCategory.panel_id == panel_id)
        .order_by(TicketCategory.position, TicketCategory.id)
        .execution_options(populate_existing=True)
    )
    return list(rows.scalars())


async def get_category(
    session: AsyncSession, guild_id: int, category_id: int
) -> TicketCategory | None:
    result = await session.execute(
        select(TicketCategory)
        .where(TicketCategory.guild_id == guild_id, TicketCategory.id == category_id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def add_category(session: AsyncSession, **values: Any) -> TicketCategory:
    category = TicketCategory(**values)
    session.add(category)
    await session.flush()
    await session.refresh(category)
    return category


async def delete_category(session: AsyncSession, guild_id: int, category_id: int) -> bool:
    result = await session.execute(
        delete(TicketCategory).where(
            TicketCategory.guild_id == guild_id, TicketCategory.id == category_id
        )
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]


async def next_position(session: AsyncSession, guild_id: int, panel_id: int) -> int:
    value = await session.execute(
        select(func.coalesce(func.max(TicketCategory.position) + 1, 0)).where(
            TicketCategory.guild_id == guild_id, TicketCategory.panel_id == panel_id
        )
    )
    return int(value.scalar_one())


# --- Tickets ---


async def lock_user(session: AsyncSession, guild_id: int, user_id: int) -> None:
    """Hält gleichzeitige Anlegeversuche desselben Nutzers bis zum Ende der Transaktion an."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"ticket:{guild_id}:{user_id}"},
    )


async def next_number(session: AsyncSession, guild_id: int) -> int:
    stmt = (
        insert(TicketCounter)
        .values(guild_id=guild_id, last_no=1)
        .on_conflict_do_update(
            index_elements=[TicketCounter.guild_id],
            set_={"last_no": TicketCounter.last_no + 1},
        )
        .returning(TicketCounter.last_no)
    )
    number: int = (await session.execute(stmt)).scalar_one()
    return number


async def count_open(session: AsyncSession, guild_id: int, creator_id: int) -> int:
    value = await session.execute(
        select(func.count())
        .select_from(Ticket)
        .where(
            Ticket.guild_id == guild_id, Ticket.creator_id == creator_id, Ticket.status == "open"
        )
    )
    return int(value.scalar_one())


async def create_ticket(session: AsyncSession, **values: Any) -> Ticket:
    ticket = Ticket(**values)
    session.add(ticket)
    await session.flush()
    await session.refresh(ticket)
    return ticket


async def by_channel(session: AsyncSession, guild_id: int, channel_id: int) -> Ticket | None:
    result = await session.execute(
        select(Ticket)
        .where(Ticket.guild_id == guild_id, Ticket.channel_id == channel_id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def by_number(session: AsyncSession, guild_id: int, number: int) -> Ticket | None:
    result = await session.execute(
        select(Ticket)
        .where(Ticket.guild_id == guild_id, Ticket.number == number)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def by_id(session: AsyncSession, guild_id: int, ticket_id: int) -> Ticket | None:
    result = await session.execute(
        select(Ticket)
        .where(Ticket.guild_id == guild_id, Ticket.id == ticket_id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def open_tickets(session: AsyncSession, guild_id: int) -> list[Ticket]:
    rows = await session.execute(
        select(Ticket)
        .where(Ticket.guild_id == guild_id, Ticket.status == "open")
        .order_by(Ticket.number)
    )
    return list(rows.scalars())


async def set_claim(
    session: AsyncSession, guild_id: int, channel_id: int, expected: int | None, new: int | None
) -> bool:
    """Atomar: nur wenn der Stand noch `expected` ist und das Ticket offen ist."""
    condition = Ticket.claimed_by.is_(None) if expected is None else Ticket.claimed_by == expected
    result = await session.execute(
        update(Ticket)
        .where(
            Ticket.guild_id == guild_id,
            Ticket.channel_id == channel_id,
            Ticket.status == "open",
            condition,
        )
        .values(claimed_by=new)
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]


async def close_ticket(
    session: AsyncSession,
    guild_id: int,
    channel_id: int,
    *,
    closed_by: int,
    reason: str | None,
    now: datetime,
) -> Ticket | None:
    """Nur ein offenes Ticket lässt sich schliessen; wer zu spät kommt, bekommt None."""
    result = await session.execute(
        update(Ticket)
        .where(
            Ticket.guild_id == guild_id, Ticket.channel_id == channel_id, Ticket.status == "open"
        )
        .values(status="closed", closed_at=now, closed_by=closed_by, close_reason=reason)
        .returning(Ticket)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def save_transcript(
    session: AsyncSession, guild_id: int, ticket_id: int, html: str, message_count: int
) -> None:
    session.add(
        TicketTranscript(
            ticket_id=ticket_id, guild_id=guild_id, html=html, message_count=message_count
        )
    )
    await session.flush()


async def get_transcript(
    session: AsyncSession, guild_id: int, ticket_id: int
) -> TicketTranscript | None:
    result = await session.execute(
        select(TicketTranscript).where(
            TicketTranscript.guild_id == guild_id, TicketTranscript.ticket_id == ticket_id
        )
    )
    return result.scalar_one_or_none()


async def purge_transcripts(session: AsyncSession, guild_id: int, before: datetime) -> int:
    result = await session.execute(
        delete(TicketTranscript).where(
            TicketTranscript.guild_id == guild_id, TicketTranscript.created_at < before
        )
    )
    return int(result.rowcount)  # type: ignore[attr-defined]


async def add_feedback(
    session: AsyncSession, guild_id: int, ticket_id: int, user_id: int, rating: int
) -> bool:
    """False, wenn schon bewertet wurde."""
    stmt = (
        insert(TicketFeedback)
        .values(ticket_id=ticket_id, guild_id=guild_id, user_id=user_id, rating=rating)
        .on_conflict_do_nothing(index_elements=[TicketFeedback.ticket_id])
        .returning(TicketFeedback.ticket_id)
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def feedback_summary(session: AsyncSession, guild_id: int) -> tuple[int, float]:
    row = (
        await session.execute(
            select(func.count(), func.coalesce(func.avg(TicketFeedback.rating), 0)).where(
                TicketFeedback.guild_id == guild_id
            )
        )
    ).one()
    return int(row[0]), float(row[1])
