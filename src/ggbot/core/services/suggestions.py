"""Vorschläge: anlegen, abstimmen, entscheiden. Ohne Discord."""

from dataclasses import dataclass
from datetime import UTC, datetime

from ggbot.core.db.engine import Database
from ggbot.core.db.models import Suggestion
from ggbot.core.db.models.suggestions import STATUS_APPROVED, STATUS_DENIED, STATUS_PENDING
from ggbot.core.db.repos import suggestions as repo
from ggbot.core.errors import UserError
from ggbot.core.settings.cache import SettingsCache
from ggbot.core.settings.store import SettingsStore
from ggbot.core.settings.suggestions import SuggestionSettings
from ggbot.core.suggestions import rules


@dataclass(frozen=True)
class SuggestionView:
    id: int
    guild_id: int
    number: int
    author_id: int
    channel_id: int | None
    message_id: int | None
    text: str
    status: str
    decided_by: int | None
    decision_reason: str | None

    @classmethod
    def of(cls, row: Suggestion) -> "SuggestionView":
        return cls(
            id=row.id,
            guild_id=row.guild_id,
            number=row.number,
            author_id=row.author_id,
            channel_id=row.channel_id,
            message_id=row.message_id,
            text=row.text,
            status=row.status,
            decided_by=row.decided_by,
            decision_reason=row.decision_reason,
        )


class SuggestionService:
    def __init__(self, db: Database, store: SettingsStore) -> None:
        self._db = db
        self._settings = SettingsCache(store, SuggestionSettings)

    async def settings(self, guild_id: int) -> SuggestionSettings:
        return await self._settings.get(guild_id)

    def invalidate(self, guild_id: int) -> None:
        self._settings.invalidate(guild_id)

    async def create(self, guild_id: int, author_id: int, text: str) -> SuggestionView:
        cleaned = rules.clean_text(text)
        async with self._db.session() as session:
            return SuggestionView.of(await repo.create(session, guild_id, author_id, cleaned))

    async def set_message(self, suggestion_id: int, channel_id: int, message_id: int) -> None:
        async with self._db.session() as session:
            await repo.set_message(session, suggestion_id, channel_id, message_id)

    async def get(self, guild_id: int, suggestion_id: int) -> SuggestionView | None:
        async with self._db.session() as session:
            row = await repo.get(session, guild_id, suggestion_id)
            return SuggestionView.of(row) if row else None

    async def get_by_number(self, guild_id: int, number: int) -> SuggestionView | None:
        async with self._db.session() as session:
            row = await repo.get_by_number(session, guild_id, number)
            return SuggestionView.of(row) if row else None

    async def tally(self, suggestion_id: int) -> rules.Tally:
        async with self._db.session() as session:
            up, down = await repo.tally(session, suggestion_id)
        return rules.Tally(up, down)

    async def vote(
        self, guild_id: int, suggestion_id: int, user_id: int, clicked: int
    ) -> rules.Tally:
        """Stimme setzen, wechseln oder zurücknehmen; liefert den neuen Stand."""
        settings = await self.settings(guild_id)
        async with self._db.session() as session:
            row = await repo.get(session, guild_id, suggestion_id)
            if row is None:
                raise UserError("error.suggest_unknown")
            if row.status != STATUS_PENDING:
                raise UserError("error.suggest_closed")
            if row.author_id == user_id and not settings.self_vote:
                raise UserError("error.suggest_self_vote")
            new = rules.toggle(await repo.current_vote(session, suggestion_id, user_id), clicked)
            if new is None:
                await repo.delete_vote(session, suggestion_id, user_id)
            else:
                await repo.set_vote(session, suggestion_id, user_id, new)
            up, down = await repo.tally(session, suggestion_id)
        return rules.Tally(up, down)

    async def decide(
        self,
        guild_id: int,
        number: int,
        *,
        approve: bool,
        decided_by: int,
        reason: str | None,
        now: datetime | None = None,
    ) -> SuggestionView:
        cleaned = (reason or "").strip()[:500] or None
        async with self._db.session() as session:
            row = await repo.decide(
                session,
                guild_id,
                number,
                status=STATUS_APPROVED if approve else STATUS_DENIED,
                decided_by=decided_by,
                reason=cleaned,
                now=now or datetime.now(UTC),
            )
            if row is None:
                # Unbekannt oder schon entschieden: für den Nutzer macht das einen Unterschied.
                exists = await repo.get_by_number(session, guild_id, number)
                raise UserError("error.suggest_decided" if exists else "error.suggest_unknown")
            return SuggestionView.of(row)
