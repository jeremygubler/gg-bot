from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ggbot.core.db.models import Suggestion, SuggestionCounter, SuggestionVote
from ggbot.core.db.models.suggestions import STATUS_PENDING


async def next_number(session: AsyncSession, guild_id: int) -> int:
    stmt = (
        insert(SuggestionCounter)
        .values(guild_id=guild_id, last_no=1)
        .on_conflict_do_update(
            index_elements=[SuggestionCounter.guild_id],
            set_={"last_no": SuggestionCounter.last_no + 1},
        )
        .returning(SuggestionCounter.last_no)
    )
    number: int = (await session.execute(stmt)).scalar_one()
    return number


async def create(session: AsyncSession, guild_id: int, author_id: int, text: str) -> Suggestion:
    suggestion = Suggestion(
        guild_id=guild_id,
        number=await next_number(session, guild_id),
        author_id=author_id,
        text=text,
    )
    session.add(suggestion)
    await session.flush()
    await session.refresh(suggestion)
    return suggestion


async def get(session: AsyncSession, guild_id: int, suggestion_id: int) -> Suggestion | None:
    result = await session.execute(
        select(Suggestion)
        .where(Suggestion.guild_id == guild_id, Suggestion.id == suggestion_id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def get_by_number(session: AsyncSession, guild_id: int, number: int) -> Suggestion | None:
    result = await session.execute(
        select(Suggestion)
        .where(Suggestion.guild_id == guild_id, Suggestion.number == number)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def set_message(
    session: AsyncSession, suggestion_id: int, channel_id: int, message_id: int
) -> None:
    await session.execute(
        update(Suggestion)
        .where(Suggestion.id == suggestion_id)
        .values(channel_id=channel_id, message_id=message_id)
    )


async def tally(session: AsyncSession, suggestion_id: int) -> tuple[int, int]:
    row = (
        await session.execute(
            select(
                func.count().filter(SuggestionVote.vote == 1),
                func.count().filter(SuggestionVote.vote == -1),
            ).where(SuggestionVote.suggestion_id == suggestion_id)
        )
    ).one()
    return int(row[0]), int(row[1])


async def current_vote(session: AsyncSession, suggestion_id: int, user_id: int) -> int | None:
    result = await session.execute(
        select(SuggestionVote.vote)
        .where(SuggestionVote.suggestion_id == suggestion_id, SuggestionVote.user_id == user_id)
        .with_for_update()
    )
    return result.scalar_one_or_none()


async def set_vote(session: AsyncSession, suggestion_id: int, user_id: int, vote: int) -> None:
    stmt = insert(SuggestionVote).values(suggestion_id=suggestion_id, user_id=user_id, vote=vote)
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[SuggestionVote.suggestion_id, SuggestionVote.user_id],
            set_={"vote": stmt.excluded.vote},
        )
    )


async def delete_vote(session: AsyncSession, suggestion_id: int, user_id: int) -> None:
    await session.execute(
        delete(SuggestionVote).where(
            SuggestionVote.suggestion_id == suggestion_id, SuggestionVote.user_id == user_id
        )
    )


async def decide(
    session: AsyncSession,
    guild_id: int,
    number: int,
    *,
    status: str,
    decided_by: int,
    reason: str | None,
    now: datetime,
) -> Suggestion | None:
    """Entscheidet atomar; nur ein offener Vorschlag lässt sich entscheiden (None sonst)."""
    result = await session.execute(
        update(Suggestion)
        .where(
            Suggestion.guild_id == guild_id,
            Suggestion.number == number,
            Suggestion.status == STATUS_PENDING,
        )
        .values(status=status, decided_by=decided_by, decision_reason=reason, decided_at=now)
        .returning(Suggestion)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()
