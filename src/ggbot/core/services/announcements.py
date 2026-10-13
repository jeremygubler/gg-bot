"""Ankündigungen aus dem Dashboard: Entwürfe, Planung, Senden und Bearbeiten. Ohne Discord.

Gesendet wird vom Bot über den persistenten Scheduler (`announce.sync`); die API plant nur ein.
So gibt es einen Weg für „jetzt senden“ und „später senden“, und ein Neustart verliert nichts.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ggbot.core.announce import message as msg
from ggbot.core.db.engine import Database
from ggbot.core.db.models import Announcement
from ggbot.core.db.models.announcements import (
    STATE_DRAFT,
    STATE_FAILED,
    STATE_SCHEDULED,
    STATE_SENT,
)
from ggbot.core.db.repos import announcements as repo
from ggbot.core.errors import UserError
from ggbot.core.scheduler import Scheduler

SYNC_JOB = "announce.sync"
MAX_PER_GUILD = 100
MAX_AHEAD = timedelta(days=365)
# Ein Zeitpunkt knapp in der Vergangenheit ist „jetzt“ (Uhren, Übertragungszeit); weiter zurück
# ist fast sicher ein Irrtum und würde sofort etwas senden.
PAST_TOLERANCE = timedelta(minutes=2)
SENDABLE = (STATE_DRAFT, STATE_FAILED, STATE_SCHEDULED)


@dataclass(frozen=True)
class AnnouncementView:
    id: int
    guild_id: int
    name: str
    message: msg.Message
    state: str
    channel_id: int | None
    message_id: int | None
    scheduled_at: datetime | None
    sent_at: datetime | None
    rev: int
    error_key: str | None

    @classmethod
    def of(cls, row: Announcement) -> "AnnouncementView":
        embed = msg.Embed.model_validate(row.embed) if row.embed else None
        return cls(
            id=row.id,
            guild_id=row.guild_id,
            name=row.name,
            message=msg.Message(content=row.content, embed=embed),
            state=row.state,
            channel_id=row.channel_id,
            message_id=row.message_id,
            scheduled_at=row.scheduled_at,
            sent_at=row.sent_at,
            rev=row.rev,
            error_key=row.error_key,
        )


def _clean_name(name: str) -> str:
    cleaned = " ".join(name.split())[: msg.MAX_NAME]
    if not cleaned:
        raise UserError("error.announce_name")
    return cleaned


def _parts(message: msg.Message) -> tuple[str | None, dict[str, object] | None]:
    clean = msg.normalize(message)
    embed = clean.embed.model_dump(mode="json") if clean.embed else None
    return clean.content, embed


class AnnouncementService:
    def __init__(self, db: Database, scheduler: Scheduler) -> None:
        self._db = db
        self._scheduler = scheduler

    async def create(
        self, guild_id: int, user_id: int, name: str, message: msg.Message
    ) -> AnnouncementView:
        content, embed = _parts(message)
        cleaned = _clean_name(name)
        async with self._db.session() as session:
            if await repo.count(session, guild_id) >= MAX_PER_GUILD:
                raise UserError("error.announce_limit", max=MAX_PER_GUILD)
            row = await repo.create(session, guild_id, user_id, cleaned, content, embed)
            return AnnouncementView.of(row)

    async def get(self, guild_id: int, announcement_id: int) -> AnnouncementView:
        async with self._db.session() as session:
            row = await repo.get(session, guild_id, announcement_id)
        if row is None:
            raise UserError("error.announce_unknown")
        return AnnouncementView.of(row)

    async def list(self, guild_id: int) -> list[AnnouncementView]:
        async with self._db.session() as session:
            return [AnnouncementView.of(r) for r in await repo.list_for_guild(session, guild_id)]

    async def update(
        self, guild_id: int, announcement_id: int, name: str, message: msg.Message
    ) -> AnnouncementView:
        """Ändert Name und Inhalt; eine schon gesendete Nachricht wird in Discord angepasst."""
        content, embed = _parts(message)
        cleaned = _clean_name(name)
        async with self._db.session() as session:
            row = await repo.update_content(
                session, guild_id, announcement_id, cleaned, content, embed
            )
            if row is None:
                raise UserError("error.announce_unknown")
            view = AnnouncementView.of(row)
        if view.state == STATE_SENT:
            await self._enqueue(view, datetime.now(UTC))
        return view

    async def delete(self, guild_id: int, announcement_id: int) -> None:
        """Entfernt nur den Eintrag. Eine schon gesendete Nachricht bleibt in Discord stehen."""
        async with self._db.session() as session:
            if not await repo.delete_one(session, guild_id, announcement_id):
                raise UserError("error.announce_unknown")

    async def schedule(
        self,
        guild_id: int,
        announcement_id: int,
        channel_id: int,
        run_at: datetime | None,
        *,
        now: datetime | None = None,
    ) -> AnnouncementView:
        """Plant das Senden; ohne Zeit sofort. Gesendete lassen sich nicht erneut senden."""
        current = now or datetime.now(UTC)
        if run_at is not None and run_at.tzinfo is None:
            raise ValueError("run_at muss zeitzonenbewusst sein")
        when = current if run_at is None or run_at < current else run_at
        if run_at is not None and run_at < current - PAST_TOLERANCE:
            raise UserError("error.announce_past")
        if when > current + MAX_AHEAD:
            raise UserError("error.announce_far")
        async with self._db.session() as session:
            row = await repo.schedule(
                session, guild_id, announcement_id, channel_id, when, SENDABLE
            )
            if row is None:
                existing = await repo.get(session, guild_id, announcement_id)
                raise UserError(
                    "error.announce_sent" if existing is not None else "error.announce_unknown"
                )
            view = AnnouncementView.of(row)
        await self._enqueue(view, when)
        return view

    async def cancel(self, guild_id: int, announcement_id: int) -> AnnouncementView:
        async with self._db.session() as session:
            row = await repo.cancel(session, guild_id, announcement_id)
            if row is None:
                existing = await repo.get(session, guild_id, announcement_id)
                raise UserError(
                    "error.announce_not_scheduled" if existing else "error.announce_unknown"
                )
            return AnnouncementView.of(row)

    async def _enqueue(self, view: AnnouncementView, run_at: datetime) -> None:
        await self._scheduler.enqueue(
            view.guild_id, SYNC_JOB, run_at, {"id": view.id, "rev": view.rev}
        )

    # --- Für den Bot (Job) ---

    async def get_for_job(self, announcement_id: int) -> AnnouncementView | None:
        async with self._db.session() as session:
            row = await repo.get_any(session, announcement_id)
        return AnnouncementView.of(row) if row else None

    async def mark_sent(self, announcement_id: int, rev: int, message_id: int) -> bool:
        async with self._db.session() as session:
            return await repo.mark_sent(
                session, announcement_id, rev, message_id, datetime.now(UTC)
            )

    async def mark_failed(self, announcement_id: int, rev: int, error_key: str) -> bool:
        async with self._db.session() as session:
            return await repo.mark_failed(session, announcement_id, rev, error_key)

    async def set_edit_result(self, announcement_id: int, error_key: str | None) -> None:
        async with self._db.session() as session:
            await repo.set_edit_result(session, announcement_id, error_key)
