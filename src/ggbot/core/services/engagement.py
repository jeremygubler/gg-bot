"""Eigene Befehle, Giveaways und Erinnerungen: was in der Datenbank gilt. Ohne Discord."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ggbot.core.customcmd import template
from ggbot.core.db.engine import Database
from ggbot.core.db.models import CustomCommand, Giveaway, Reminder
from ggbot.core.db.repos import engagement as repo
from ggbot.core.errors import UserError
from ggbot.core.giveaway.rules import MAX_ACTIVE, MAX_WINNERS
from ggbot.core.scheduler import Scheduler

MAX_COMMANDS = 50
COMMAND_NAME = re.compile(r"\A[a-z0-9_-]{1,32}\Z")
MAX_COOLDOWN = 3600
MAX_PRIZE = 200
MAX_REMINDERS = 25
MAX_REMINDER_TEXT = 500
MIN_REMINDER = timedelta(minutes=1)
MAX_REMINDER = timedelta(days=365)
MIN_GIVEAWAY = timedelta(minutes=1)
MAX_GIVEAWAY = timedelta(days=60)
GIVEAWAY_JOB = "giveaway.end"
REMINDER_JOB = "reminder.fire"


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class CommandView:
    id: int
    name: str
    response: str
    ephemeral: bool
    required_role_id: int | None
    cooldown_seconds: int
    uses: int
    created_by: int

    @classmethod
    def from_model(cls, c: CustomCommand) -> "CommandView":
        return cls(
            c.id,
            c.name,
            c.response,
            c.ephemeral,
            c.required_role_id,
            c.cooldown_seconds,
            c.uses,
            c.created_by,
        )


@dataclass(frozen=True)
class GiveawayView:
    id: int
    guild_id: int
    channel_id: int
    message_id: int | None
    prize: str
    winner_count: int
    host_id: int
    required_role_id: int | None
    ends_at: datetime
    ended: bool
    winners: tuple[int, ...]

    @classmethod
    def from_model(cls, g: Giveaway) -> "GiveawayView":
        return cls(
            g.id,
            g.guild_id,
            g.channel_id,
            g.message_id,
            g.prize,
            g.winner_count,
            g.host_id,
            g.required_role_id,
            g.ends_at,
            g.ended,
            tuple(g.winners),
        )


@dataclass(frozen=True)
class ReminderView:
    id: int
    guild_id: int
    user_id: int
    channel_id: int | None
    text: str
    due_at: datetime

    @classmethod
    def from_model(cls, r: Reminder) -> "ReminderView":
        return cls(r.id, r.guild_id, r.user_id, r.channel_id, r.text, r.due_at)


class EngagementService:
    def __init__(self, db: Database, scheduler: Scheduler) -> None:
        self._db = db
        self._scheduler = scheduler

    # --- Eigene Befehle ---

    @staticmethod
    def _check_command(name: str, response: str, cooldown: int) -> str:
        name = name.strip().lower()
        if not COMMAND_NAME.match(name):
            raise UserError("error.cc_name")
        if not 0 <= cooldown <= MAX_COOLDOWN:
            raise UserError("error.cc_cooldown", limit=MAX_COOLDOWN)
        if not response.strip():
            raise UserError("error.cc_empty")
        try:
            template.validate(response)
        except ValueError as exc:
            raise UserError("error.cc_invalid", detail=str(exc)) from exc
        return name

    async def add_command(
        self,
        guild_id: int,
        name: str,
        response: str,
        created_by: int,
        *,
        ephemeral: bool = False,
        required_role_id: int | None = None,
        cooldown_seconds: int = 5,
    ) -> CommandView:
        name = self._check_command(name, response, cooldown_seconds)
        async with self._db.session() as session:
            if await repo.cc_count(session, guild_id) >= MAX_COMMANDS:
                raise UserError("error.cc_limit", limit=MAX_COMMANDS)
            if await repo.cc_get(session, guild_id, name):
                raise UserError("error.cc_exists", name=name)
            command = await repo.cc_add(
                session,
                guild_id=guild_id,
                name=name,
                response=response,
                ephemeral=ephemeral,
                required_role_id=required_role_id,
                cooldown_seconds=cooldown_seconds,
                created_by=created_by,
            )
            return CommandView.from_model(command)

    async def edit_command(
        self,
        guild_id: int,
        name: str,
        *,
        response: str | None = None,
        ephemeral: bool | None = None,
        required_role_id: int | None = None,
        clear_role: bool = False,
        cooldown_seconds: int | None = None,
    ) -> CommandView:
        async with self._db.session() as session:
            command = await repo.cc_get(session, guild_id, name.strip().lower())
            if command is None:
                raise UserError("error.cc_unknown", name=name)
            self._check_command(
                command.name,
                response if response is not None else command.response,
                cooldown_seconds if cooldown_seconds is not None else command.cooldown_seconds,
            )
            if response is not None:
                command.response = response
            if ephemeral is not None:
                command.ephemeral = ephemeral
            if clear_role:
                command.required_role_id = None
            elif required_role_id is not None:
                command.required_role_id = required_role_id
            if cooldown_seconds is not None:
                command.cooldown_seconds = cooldown_seconds
            await session.flush()
            return CommandView.from_model(command)

    async def remove_command(self, guild_id: int, name: str) -> None:
        async with self._db.session() as session:
            if not await repo.cc_delete(session, guild_id, name.strip().lower()):
                raise UserError("error.cc_unknown", name=name)

    async def get_command(self, guild_id: int, name: str) -> CommandView:
        async with self._db.session() as session:
            command = await repo.cc_get(session, guild_id, name.strip().lower())
            if command is None:
                raise UserError("error.cc_unknown", name=name)
            return CommandView.from_model(command)

    async def list_commands(self, guild_id: int) -> list[CommandView]:
        async with self._db.session() as session:
            return [CommandView.from_model(c) for c in await repo.cc_list(session, guild_id)]

    async def use_command(self, guild_id: int, name: str) -> CommandView:
        """Zählt den Aufruf mit und liefert den Befehl mit dem neuen Zähler."""
        async with self._db.session() as session:
            command = await repo.cc_use(session, guild_id, name.strip().lower())
            if command is None:
                raise UserError("error.cc_unknown", name=name)
            return CommandView.from_model(command)

    # --- Giveaways ---

    async def create_giveaway(
        self,
        guild_id: int,
        *,
        channel_id: int,
        prize: str,
        winner_count: int,
        host_id: int,
        duration: timedelta,
        required_role_id: int | None = None,
    ) -> GiveawayView:
        prize = prize.strip()
        if not 1 <= len(prize) <= MAX_PRIZE:
            raise UserError("error.gw_prize", limit=MAX_PRIZE)
        if not 1 <= winner_count <= MAX_WINNERS:
            raise UserError("error.gw_winners", limit=MAX_WINNERS)
        if not MIN_GIVEAWAY <= duration <= MAX_GIVEAWAY:
            raise UserError("error.gw_duration")
        ends_at = _utcnow() + duration
        async with self._db.session() as session:
            if len(await repo.gw_active(session, guild_id)) >= MAX_ACTIVE:
                raise UserError("error.gw_limit", limit=MAX_ACTIVE)
            giveaway = await repo.gw_create(
                session,
                guild_id=guild_id,
                channel_id=channel_id,
                prize=prize,
                winner_count=winner_count,
                host_id=host_id,
                required_role_id=required_role_id,
                ends_at=ends_at,
            )
            view = GiveawayView.from_model(giveaway)
        await self._scheduler.enqueue(guild_id, GIVEAWAY_JOB, ends_at, {"giveaway_id": view.id})
        return view

    async def set_giveaway_message(self, guild_id: int, giveaway_id: int, message_id: int) -> None:
        async with self._db.session() as session:
            await repo.gw_set_message(session, guild_id, giveaway_id, message_id)

    async def get_giveaway(self, guild_id: int, giveaway_id: int) -> GiveawayView:
        async with self._db.session() as session:
            giveaway = await repo.gw_get(session, guild_id, giveaway_id)
            if giveaway is None:
                raise UserError("error.gw_unknown", giveaway_id=giveaway_id)
            return GiveawayView.from_model(giveaway)

    async def active_giveaways(self, guild_id: int) -> list[GiveawayView]:
        async with self._db.session() as session:
            return [GiveawayView.from_model(g) for g in await repo.gw_active(session, guild_id)]

    async def toggle_entry(
        self, guild_id: int, giveaway_id: int, user_id: int, *, now: datetime | None = None
    ) -> tuple[bool, int]:
        """(jetzt dabei?, Teilnehmerzahl). Erster Klick trägt ein, der zweite zieht zurück."""
        async with self._db.session() as session:
            giveaway = await repo.gw_get(session, guild_id, giveaway_id)
            if giveaway is None:
                raise UserError("error.gw_unknown", giveaway_id=giveaway_id)
            if giveaway.ended or giveaway.ends_at <= (now or _utcnow()):
                raise UserError("error.gw_over")
            if await repo.gw_enter(session, guild_id, giveaway_id, user_id):
                joined = True
            else:
                await repo.gw_leave(session, guild_id, giveaway_id, user_id)
                joined = False
            return joined, await repo.gw_entry_count(session, guild_id, giveaway_id)

    async def end_giveaway(self, guild_id: int, giveaway_id: int) -> tuple[GiveawayView, list[int]]:
        """Beendet genau einmal und liefert die Teilnehmer. Ein zweiter Aufruf scheitert."""
        async with self._db.session() as session:
            giveaway = await repo.gw_end(session, guild_id, giveaway_id)
            if giveaway is None:
                if await repo.gw_get(session, guild_id, giveaway_id) is None:
                    raise UserError("error.gw_unknown", giveaway_id=giveaway_id)
                raise UserError("error.gw_over")
            return GiveawayView.from_model(giveaway), await repo.gw_entries(
                session, guild_id, giveaway_id
            )

    async def entrants_for_reroll(
        self, guild_id: int, giveaway_id: int
    ) -> tuple[GiveawayView, list[int]]:
        async with self._db.session() as session:
            giveaway = await repo.gw_get(session, guild_id, giveaway_id)
            if giveaway is None:
                raise UserError("error.gw_unknown", giveaway_id=giveaway_id)
            if not giveaway.ended:
                raise UserError("error.gw_running")
            return GiveawayView.from_model(giveaway), await repo.gw_entries(
                session, guild_id, giveaway_id
            )

    async def set_winners(self, guild_id: int, giveaway_id: int, winners: list[int]) -> None:
        async with self._db.session() as session:
            await repo.gw_set_winners(session, guild_id, giveaway_id, winners)

    async def delete_giveaway(self, guild_id: int, giveaway_id: int) -> GiveawayView:
        async with self._db.session() as session:
            giveaway = await repo.gw_get(session, guild_id, giveaway_id)
            if giveaway is None:
                raise UserError("error.gw_unknown", giveaway_id=giveaway_id)
            view = GiveawayView.from_model(giveaway)
            await repo.gw_delete(session, guild_id, giveaway_id)
            return view

    # --- Erinnerungen ---

    async def create_reminder(
        self,
        guild_id: int,
        user_id: int,
        channel_id: int | None,
        text: str,
        delay: timedelta,
    ) -> ReminderView:
        text = text.strip()
        if not 1 <= len(text) <= MAX_REMINDER_TEXT:
            raise UserError("error.rm_text", limit=MAX_REMINDER_TEXT)
        if not MIN_REMINDER <= delay <= MAX_REMINDER:
            raise UserError("error.rm_delay")
        due_at = _utcnow() + delay
        async with self._db.session() as session:
            if len(await repo.rm_pending(session, guild_id, user_id)) >= MAX_REMINDERS:
                raise UserError("error.rm_limit", limit=MAX_REMINDERS)
            reminder = await repo.rm_create(
                session,
                guild_id=guild_id,
                user_id=user_id,
                channel_id=channel_id,
                text=text,
                due_at=due_at,
            )
            view = ReminderView.from_model(reminder)
        await self._scheduler.enqueue(guild_id, REMINDER_JOB, due_at, {"reminder_id": view.id})
        return view

    async def pending_reminders(self, guild_id: int, user_id: int) -> list[ReminderView]:
        async with self._db.session() as session:
            return [
                ReminderView.from_model(r)
                for r in await repo.rm_pending(session, guild_id, user_id)
            ]

    async def cancel_reminder(self, guild_id: int, user_id: int, reminder_id: int) -> None:
        async with self._db.session() as session:
            if not await repo.rm_cancel(session, guild_id, user_id, reminder_id):
                raise UserError("error.rm_unknown", reminder_id=reminder_id)

    async def fire_reminder(self, guild_id: int, reminder_id: int) -> ReminderView | None:
        """Die fällige Erinnerung, genau einmal; None, wenn abgebrochen oder schon ausgeliefert."""
        async with self._db.session() as session:
            reminder = await repo.rm_fire(session, guild_id, reminder_id)
            return ReminderView.from_model(reminder) if reminder else None
