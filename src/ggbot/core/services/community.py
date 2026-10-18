"""Starboard, Geburtstage, Stat-Kanäle und geplante Nachrichten. Ohne Discord."""

import calendar
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from ggbot.core.community import birthday as bd_rules
from ggbot.core.community import schedule as schedule_rules
from ggbot.core.community import stats as stats_rules
from ggbot.core.db.engine import Database
from ggbot.core.db.models import ScheduledMessage, StatChannel
from ggbot.core.db.repos import community as repo
from ggbot.core.errors import UserError
from ggbot.core.scheduler import Scheduler
from ggbot.core.settings.birthday import BirthdaySettings
from ggbot.core.settings.cache import SettingsCache
from ggbot.core.settings.starboard import StarboardSettings
from ggbot.core.settings.store import SettingsStore

MAX_STAT_CHANNELS = 5
MAX_SCHEDULED = 25
MAX_SCHEDULED_TEXT = 1500
MIN_INTERVAL = timedelta(minutes=5)
MAX_INTERVAL = timedelta(days=30)
MIN_DELAY = timedelta(minutes=1)
MAX_DELAY = timedelta(days=365)
SCHEDULE_JOB = "schedule.send"


def _utcnow() -> datetime:
    return datetime.now(UTC)


# --- Starboard ---


@dataclass(frozen=True)
class StarEntry:
    guild_id: int
    message_id: int
    channel_id: int
    author_id: int
    star_message_id: int | None


class StarboardService:
    def __init__(self, db: Database, store: SettingsStore) -> None:
        self._db = db
        self._settings = SettingsCache(store, StarboardSettings)

    async def settings(self, guild_id: int) -> StarboardSettings:
        return await self._settings.get(guild_id)

    def invalidate(self, guild_id: int) -> None:
        self._settings.invalidate(guild_id)

    async def vote(
        self, guild_id: int, message_id: int, user_id: int, *, channel_id: int, author_id: int
    ) -> int:
        """Trägt den Stern ein. Der Rückgabewert ist der Stand *dieser* Transaktion; bei
        gleichzeitigen Sternen sieht jede nur ihren eigenen. Wer entscheiden will, zählt danach mit
        `count` neu: die zuletzt abgeschlossene sieht dann alle."""
        async with self._db.session() as session:
            return await repo.sb_vote(
                session, guild_id, message_id, user_id, channel_id=channel_id, author_id=author_id
            )

    async def unvote(self, guild_id: int, message_id: int, user_id: int) -> int | None:
        """Neuer Stand; None, wenn die Nachricht nie Sterne hatte."""
        async with self._db.session() as session:
            if await repo.sb_entry(session, guild_id, message_id) is None:
                return None
            return await repo.sb_unvote(session, guild_id, message_id, user_id)

    async def entry(self, guild_id: int, message_id: int) -> StarEntry | None:
        async with self._db.session() as session:
            e = await repo.sb_entry(session, guild_id, message_id)
            if e is None:
                return None
            return StarEntry(e.guild_id, e.message_id, e.channel_id, e.author_id, e.star_message_id)

    async def copy_message_ids(self, guild_id: int) -> frozenset[int]:
        """Die Kopien im Starboard-Kanal: sie sind der Beitrag selbst."""
        async with self._db.session() as session:
            return frozenset(await repo.sb_copy_ids(session, guild_id))

    async def count(self, guild_id: int, message_id: int) -> int:
        async with self._db.session() as session:
            return await repo.sb_count(session, guild_id, message_id)

    async def claim_post(self, guild_id: int, message_id: int) -> bool:
        async with self._db.session() as session:
            return await repo.sb_claim_post(session, guild_id, message_id)

    async def finish_post(
        self, guild_id: int, message_id: int, star_message_id: int | None
    ) -> None:
        async with self._db.session() as session:
            await repo.sb_finish_post(session, guild_id, message_id, star_message_id)

    async def forget(self, guild_id: int, message_id: int) -> StarEntry | None:
        """Die Nachricht samt Sternen vergessen (z. B. weil sie gelöscht wurde)."""
        async with self._db.session() as session:
            e = await repo.sb_delete_entry(session, guild_id, message_id)
            if e is None:
                return None
            return StarEntry(e.guild_id, e.message_id, e.channel_id, e.author_id, e.star_message_id)


# --- Geburtstage ---


class BirthdayService:
    def __init__(self, db: Database, store: SettingsStore) -> None:
        self._db = db
        self._settings = SettingsCache(store, BirthdaySettings)

    async def settings(self, guild_id: int) -> BirthdaySettings:
        return await self._settings.get(guild_id)

    def invalidate(self, guild_id: int) -> None:
        self._settings.invalidate(guild_id)

    async def set(self, guild_id: int, user_id: int, day: int, month: int) -> None:
        if not bd_rules.valid_date(day, month):
            raise UserError("error.bd_invalid", day=day, month=month)
        async with self._db.session() as session:
            await repo.bd_set(session, guild_id, user_id, day, month)

    async def remove(self, guild_id: int, user_id: int) -> None:
        async with self._db.session() as session:
            if not await repo.bd_delete(session, guild_id, user_id):
                raise UserError("error.bd_none")

    async def get(self, guild_id: int, user_id: int) -> tuple[int, int] | None:
        async with self._db.session() as session:
            b = await repo.bd_get(session, guild_id, user_id)
            return (b.day, b.month) if b else None

    async def today(self, guild_id: int, today: date) -> list[int]:
        async with self._db.session() as session:
            return await repo.bd_on(session, guild_id, today, calendar.isleap(today.year))

    async def upcoming(
        self, guild_id: int, today: date, limit: int = 10
    ) -> list[tuple[int, int, int, int]]:
        """(Nutzer, Tag, Monat, Tage bis dahin), nächste zuerst; heute zählt als 0."""
        async with self._db.session() as session:
            rows = await repo.bd_all(session, guild_id)
        found: list[tuple[int, int, int, int]] = []
        for b in rows:
            found.append((b.user_id, b.day, b.month, _days_until(b.day, b.month, today)))
        found.sort(key=lambda r: (r[3], r[0]))
        return found[:limit]

    async def delete_user(self, guild_id: int, user_id: int) -> bool:
        async with self._db.session() as session:
            return await repo.bd_delete(session, guild_id, user_id)


def _days_until(day: int, month: int, today: date) -> int:
    for year in (today.year, today.year + 1, today.year + 2, today.year + 3, today.year + 4):
        target = date(year, month, min(day, calendar.monthrange(year, month)[1]))
        if target >= today:
            return (target - today).days
    return 0  # unerreichbar: jedes gültige Datum kommt innerhalb von vier Jahren vor


# --- Stat-Kanäle ---


@dataclass(frozen=True)
class StatView:
    id: int
    guild_id: int
    channel_id: int
    kind: str
    template: str

    @classmethod
    def from_model(cls, s: StatChannel) -> "StatView":
        return cls(s.id, s.guild_id, s.channel_id, s.kind, s.template)


class StatChannelService:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def add(self, guild_id: int, channel_id: int, kind: str, template: str) -> StatView:
        if kind not in stats_rules.KINDS:
            raise UserError("error.st_kind")
        try:
            stats_rules.validate_template(template)
        except ValueError as exc:
            raise UserError("error.st_template", detail=str(exc)) from exc
        async with self._db.session() as session:
            if len(await repo.st_list(session, guild_id)) >= MAX_STAT_CHANNELS:
                raise UserError("error.st_limit", limit=MAX_STAT_CHANNELS)
            return StatView.from_model(
                await repo.st_add(
                    session, guild_id=guild_id, channel_id=channel_id, kind=kind, template=template
                )
            )

    async def remove(self, guild_id: int, channel_id: int) -> None:
        async with self._db.session() as session:
            if not await repo.st_delete(session, guild_id, channel_id):
                raise UserError("error.st_unknown")

    async def list(self, guild_id: int) -> list[StatView]:
        async with self._db.session() as session:
            return [StatView.from_model(s) for s in await repo.st_list(session, guild_id)]


# --- Geplante Nachrichten ---


@dataclass(frozen=True)
class ScheduledView:
    id: int
    guild_id: int
    channel_id: int
    text: str
    next_run: datetime
    interval_seconds: int | None
    created_by: int

    @classmethod
    def from_model(cls, m: ScheduledMessage) -> "ScheduledView":
        return cls(
            m.id, m.guild_id, m.channel_id, m.text, m.next_run, m.interval_seconds, m.created_by
        )


class ScheduleService:
    def __init__(self, db: Database, scheduler: Scheduler) -> None:
        self._db = db
        self._scheduler = scheduler

    async def add(
        self,
        guild_id: int,
        *,
        channel_id: int,
        text: str,
        created_by: int,
        delay: timedelta,
        interval: timedelta | None = None,
    ) -> ScheduledView:
        text = text.strip()
        if not 1 <= len(text) <= MAX_SCHEDULED_TEXT:
            raise UserError("error.sm_text", limit=MAX_SCHEDULED_TEXT)
        if not MIN_DELAY <= delay <= MAX_DELAY:
            raise UserError("error.sm_delay")
        if interval is not None and not MIN_INTERVAL <= interval <= MAX_INTERVAL:
            raise UserError("error.sm_interval")
        run_at = _utcnow() + delay
        async with self._db.session() as session:
            if await repo.sm_count_active(session, guild_id) >= MAX_SCHEDULED:
                raise UserError("error.sm_limit", limit=MAX_SCHEDULED)
            message = await repo.sm_add(
                session,
                guild_id=guild_id,
                channel_id=channel_id,
                text=text,
                next_run=run_at,
                interval_seconds=int(interval.total_seconds()) if interval else None,
                created_by=created_by,
            )
            view = ScheduledView.from_model(message)
        await self._scheduler.enqueue(
            guild_id, SCHEDULE_JOB, run_at, {"id": view.id, "at": run_at.isoformat()}
        )
        return view

    async def list(self, guild_id: int) -> list[ScheduledView]:
        async with self._db.session() as session:
            return [ScheduledView.from_model(m) for m in await repo.sm_list(session, guild_id)]

    async def cancel(self, guild_id: int, message_id: int) -> None:
        async with self._db.session() as session:
            if not await repo.sm_cancel(session, guild_id, message_id):
                raise UserError("error.sm_unknown", message_id=message_id)

    async def claim_run(
        self, guild_id: int, message_id: int, due: datetime, *, now: datetime | None = None
    ) -> ScheduledView | None:
        """Beansprucht den Termin `due` und plant den nächsten. None: schon verarbeitet, abgebrochen
        oder veraltet (eine Doppelauslieferung des Jobs sendet nichts)."""
        now = now or _utcnow()
        async with self._db.session() as session:
            current = await repo.sm_get(session, guild_id, message_id)
            if current is None or current.state != "active" or current.next_run != due:
                return None
            interval = (
                timedelta(seconds=current.interval_seconds) if current.interval_seconds else None
            )
            new_next = schedule_rules.next_run(due, interval, now) if interval else due
            claimed = await repo.sm_claim_run(
                session,
                guild_id,
                message_id,
                expected=due,
                new_next=new_next,
                done=interval is None,
            )
            if claimed is None:
                return None
            view = ScheduledView.from_model(claimed)
        if interval is not None:
            await self._scheduler.enqueue(
                guild_id, SCHEDULE_JOB, new_next, {"id": message_id, "at": new_next.isoformat()}
            )
        return view
