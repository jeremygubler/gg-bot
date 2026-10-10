"""Tickets: Panels, Kategorien, Lebenszyklus und Transkripte. Ohne Discord.

Die Cogs legen Kanäle an und löschen sie; hier steht, was in der Datenbank gilt: wie viele Tickets
jemand offen haben darf, welche Nummer ein Ticket bekommt, wer es schliessen darf und dass ein
Ticket genau einmal geschlossen wird.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from ggbot.core import messages
from ggbot.core.db.engine import Database
from ggbot.core.db.models import Ticket, TicketCategory, TicketPanel
from ggbot.core.db.repos import tickets as repo
from ggbot.core.errors import UserError
from ggbot.core.settings.cache import SettingsCache
from ggbot.core.settings.store import SettingsStore
from ggbot.core.settings.tickets import TicketSettings

MAX_PANELS = 10
MAX_CATEGORIES = 10  # ein Button pro Kategorie, zwei Reihen
MAX_SUPPORT_ROLES = 10
MAX_TITLE = 256
MAX_DESCRIPTION = 1000
MAX_NAME = 80
MAX_CATEGORY_DESCRIPTION = 100
MAX_WELCOME = 1000
MAX_REASON = 300
WELCOME_FIELDS = ("user", "category", "number")


@dataclass(frozen=True)
class CategoryView:
    id: int
    panel_id: int
    name: str
    emoji: str | None
    description: str | None
    parent_id: int | None
    support_role_ids: tuple[int, ...]
    welcome_message: str | None
    questions: tuple[str, ...]
    position: int

    @classmethod
    def from_model(cls, c: TicketCategory) -> "CategoryView":
        return cls(
            c.id,
            c.panel_id,
            c.name,
            c.emoji,
            c.description,
            c.parent_id,
            tuple(c.support_role_ids),
            c.welcome_message,
            tuple(c.questions),
            c.position,
        )


@dataclass(frozen=True)
class TicketPanelView:
    id: int
    guild_id: int
    channel_id: int | None
    message_id: int | None
    title: str
    description: str | None
    categories: tuple[CategoryView, ...]

    @property
    def published(self) -> bool:
        return self.message_id is not None

    def category_by_name(self, name: str) -> CategoryView | None:
        wanted = name.casefold()
        return next((c for c in self.categories if c.name.casefold() == wanted), None)

    @classmethod
    def from_models(cls, p: TicketPanel, cats: list[TicketCategory]) -> "TicketPanelView":
        return cls(
            p.id,
            p.guild_id,
            p.channel_id,
            p.message_id,
            p.title,
            p.description,
            tuple(CategoryView.from_model(c) for c in cats),
        )


@dataclass(frozen=True)
class TicketView:
    id: int
    guild_id: int
    number: int
    channel_id: int
    creator_id: int
    category_id: int | None
    category_name: str
    support_role_ids: tuple[int, ...]
    status: str
    claimed_by: int | None
    answers: tuple[tuple[str, str], ...]
    created_at: datetime
    closed_at: datetime | None
    closed_by: int | None
    close_reason: str | None

    @property
    def is_open(self) -> bool:
        return self.status == "open"

    @classmethod
    def from_model(cls, t: Ticket) -> "TicketView":
        return cls(
            t.id,
            t.guild_id,
            t.number,
            t.channel_id,
            t.creator_id,
            t.category_id,
            t.category_name,
            tuple(t.support_role_ids),
            t.status,
            t.claimed_by,
            tuple((a["question"], a["answer"]) for a in t.answers),
            t.created_at,
            t.closed_at,
            t.closed_by,
            t.close_reason,
        )


@dataclass(frozen=True)
class Reservation:
    number: int
    category: CategoryView


def _utcnow() -> datetime:
    return datetime.now(UTC)


class TicketService:
    def __init__(self, db: Database, store: SettingsStore, redis: Redis) -> None:
        self._db = db
        self._settings = SettingsCache(store, TicketSettings)
        self._redis = redis

    async def settings(self, guild_id: int) -> TicketSettings:
        return await self._settings.get(guild_id)

    def invalidate(self, guild_id: int) -> None:
        self._settings.invalidate(guild_id)

    # --- Panels ---

    async def _panel_view(
        self, session: AsyncSession, guild_id: int, panel: TicketPanel
    ) -> TicketPanelView:
        return TicketPanelView.from_models(
            panel, await repo.categories(session, guild_id, panel.id)
        )

    async def create_panel(
        self, guild_id: int, *, title: str, description: str | None = None
    ) -> TicketPanelView:
        title = title.strip()
        if not 1 <= len(title) <= MAX_TITLE:
            raise UserError("error.tpanel_title")
        if description is not None and len(description) > MAX_DESCRIPTION:
            raise UserError("error.tpanel_description")
        async with self._db.session() as session:
            if await repo.count_panels(session, guild_id) >= MAX_PANELS:
                raise UserError("error.tpanel_limit", limit=MAX_PANELS)
            panel = await repo.create_panel(session, guild_id, title, description or None)
            return await self._panel_view(session, guild_id, panel)

    async def update_panel(
        self, guild_id: int, panel_id: int, *, title: str, description: str | None
    ) -> TicketPanelView:
        title = title.strip()
        if not 1 <= len(title) <= MAX_TITLE:
            raise UserError("error.tpanel_title")
        if description is not None and len(description) > MAX_DESCRIPTION:
            raise UserError("error.tpanel_description")
        async with self._db.session() as session:
            panel = await repo.get_panel(session, guild_id, panel_id)
            if panel is None:
                raise UserError("error.tpanel_not_found", panel_id=panel_id)
            panel.title = title
            panel.description = description or None
            await session.flush()
            return await self._panel_view(session, guild_id, panel)

    async def get_panel(self, guild_id: int, panel_id: int) -> TicketPanelView:
        async with self._db.session() as session:
            panel = await repo.get_panel(session, guild_id, panel_id)
            if panel is None:
                raise UserError("error.tpanel_not_found", panel_id=panel_id)
            return await self._panel_view(session, guild_id, panel)

    async def list_panels(self, guild_id: int) -> list[TicketPanelView]:
        async with self._db.session() as session:
            return [
                await self._panel_view(session, guild_id, p)
                for p in await repo.list_panels(session, guild_id)
            ]

    async def delete_panel(self, guild_id: int, panel_id: int) -> TicketPanelView:
        async with self._db.session() as session:
            panel = await repo.get_panel(session, guild_id, panel_id)
            if panel is None:
                raise UserError("error.tpanel_not_found", panel_id=panel_id)
            view = await self._panel_view(session, guild_id, panel)
            await repo.delete_panel(session, guild_id, panel_id)
            return view

    async def set_panel_message(
        self, guild_id: int, panel_id: int, channel_id: int | None, message_id: int | None
    ) -> None:
        async with self._db.session() as session:
            if not await repo.set_panel_message(
                session, guild_id, panel_id, channel_id, message_id
            ):
                raise UserError("error.tpanel_not_found", panel_id=panel_id)

    async def panel_message_deleted(self, guild_id: int, message_id: int) -> int:
        async with self._db.session() as session:
            return await repo.clear_panel_message(session, guild_id, message_id)

    # --- Kategorien ---

    async def add_category(
        self,
        guild_id: int,
        panel_id: int,
        *,
        name: str,
        support_role_id: int,
        emoji: str | None = None,
        description: str | None = None,
        parent_id: int | None = None,
        welcome_message: str | None = None,
        questions: list[str] | None = None,
    ) -> TicketPanelView:
        name = name.strip()
        if not 1 <= len(name) <= MAX_NAME:
            raise UserError("error.tcat_name")
        if description is not None and len(description) > MAX_CATEGORY_DESCRIPTION:
            raise UserError("error.tcat_description")
        if welcome_message is not None:
            if len(welcome_message) > MAX_WELCOME:
                raise UserError("error.tcat_welcome")
            try:
                messages.validate_template(welcome_message, WELCOME_FIELDS)
            except ValueError as exc:
                raise UserError("error.tcat_welcome_invalid", detail=str(exc)) from exc
        async with self._db.session() as session:
            panel = await repo.get_panel(session, guild_id, panel_id)
            if panel is None:
                raise UserError("error.tpanel_not_found", panel_id=panel_id)
            existing = await repo.categories(session, guild_id, panel_id)
            if len(existing) >= MAX_CATEGORIES:
                raise UserError("error.tcat_limit", limit=MAX_CATEGORIES)
            if any(c.name.casefold() == name.casefold() for c in existing):
                raise UserError("error.tcat_duplicate", name=name)
            await repo.add_category(
                session,
                panel_id=panel_id,
                guild_id=guild_id,
                name=name,
                emoji=emoji or None,
                description=description or None,
                parent_id=parent_id,
                support_role_ids=[support_role_id],
                welcome_message=welcome_message or None,
                questions=questions or [],
                position=await repo.next_position(session, guild_id, panel_id),
            )
            return await self._panel_view(session, guild_id, panel)

    async def _category(
        self, session: AsyncSession, guild_id: int, panel_id: int, name: str
    ) -> TicketCategory:
        for category in await repo.categories(session, guild_id, panel_id):
            if category.name.casefold() == name.strip().casefold():
                return category
        raise UserError("error.tcat_not_found", name=name)

    async def remove_category(self, guild_id: int, panel_id: int, name: str) -> TicketPanelView:
        async with self._db.session() as session:
            panel = await repo.get_panel(session, guild_id, panel_id)
            if panel is None:
                raise UserError("error.tpanel_not_found", panel_id=panel_id)
            category = await self._category(session, guild_id, panel_id, name)
            await repo.delete_category(session, guild_id, category.id)
            return await self._panel_view(session, guild_id, panel)

    async def change_support_role(
        self, guild_id: int, panel_id: int, name: str, role_id: int, *, add: bool
    ) -> TicketPanelView:
        async with self._db.session() as session:
            panel = await repo.get_panel(session, guild_id, panel_id)
            if panel is None:
                raise UserError("error.tpanel_not_found", panel_id=panel_id)
            category = await self._category(session, guild_id, panel_id, name)
            roles = list(category.support_role_ids)
            if add:
                if role_id in roles:
                    raise UserError("error.tcat_role_duplicate")
                if len(roles) >= MAX_SUPPORT_ROLES:
                    raise UserError("error.tcat_roles_limit", limit=MAX_SUPPORT_ROLES)
                roles.append(role_id)
            else:
                if role_id not in roles:
                    raise UserError("error.tcat_role_missing")
                if len(roles) == 1:
                    # Ohne Support-Rolle sähe nur der Ersteller sein Ticket.
                    raise UserError("error.tcat_last_role")
                roles.remove(role_id)
            category.support_role_ids = roles
            await session.flush()
            return await self._panel_view(session, guild_id, panel)

    async def get_category(self, guild_id: int, category_id: int) -> CategoryView:
        async with self._db.session() as session:
            category = await repo.get_category(session, guild_id, category_id)
            if category is None:
                raise UserError("error.tcat_gone")
            return CategoryView.from_model(category)

    # --- Tickets anlegen ---

    async def reserve(self, guild_id: int, creator_id: int, category_id: int) -> Reservation:
        """Prüft die Grenze, bevor der Kanal angelegt wird, und vergibt die Nummer.

        Die Nummer steht im Kanalnamen und wird darum vor dem Kanal gebraucht. Scheitert das Anlegen
        danach, bleibt eine Lücke; das ist der Preis dafür, dass Nummern nie doppelt vorkommen.
        """
        settings = await self.settings(guild_id)
        async with self._db.session() as session:
            category = await repo.get_category(session, guild_id, category_id)
            if category is None:
                raise UserError("error.tcat_gone")
            if await repo.count_open(session, guild_id, creator_id) >= settings.max_open_per_user:
                raise UserError("error.ticket_limit", limit=settings.max_open_per_user)
            number = await repo.next_number(session, guild_id)
            return Reservation(number, CategoryView.from_model(category))

    async def register(
        self,
        guild_id: int,
        *,
        number: int,
        creator_id: int,
        channel_id: int,
        category: CategoryView,
        answers: list[tuple[str, str]],
    ) -> TicketView:
        """Trägt das Ticket ein. Die Grenze gilt hier endgültig: zwei gleichzeitige Klicks
        desselben Nutzers kommen nacheinander durch das Sperr-Fenster und der zweite scheitert."""
        settings = await self.settings(guild_id)
        async with self._db.session() as session:
            await repo.lock_user(session, guild_id, creator_id)
            if await repo.count_open(session, guild_id, creator_id) >= settings.max_open_per_user:
                raise UserError("error.ticket_limit", limit=settings.max_open_per_user)
            ticket = await repo.create_ticket(
                session,
                guild_id=guild_id,
                number=number,
                channel_id=channel_id,
                creator_id=creator_id,
                category_id=category.id,
                category_name=category.name,
                support_role_ids=list(category.support_role_ids),
                answers=[{"question": q, "answer": a} for q, a in answers],
            )
            return TicketView.from_model(ticket)

    # --- Lesen ---

    async def by_channel(self, guild_id: int, channel_id: int) -> TicketView | None:
        async with self._db.session() as session:
            ticket = await repo.by_channel(session, guild_id, channel_id)
            return TicketView.from_model(ticket) if ticket else None

    async def require_channel(self, guild_id: int, channel_id: int) -> TicketView:
        ticket = await self.by_channel(guild_id, channel_id)
        if ticket is None:
            raise UserError("error.ticket_not_here")
        return ticket

    async def by_number(self, guild_id: int, number: int) -> TicketView:
        async with self._db.session() as session:
            ticket = await repo.by_number(session, guild_id, number)
            if ticket is None:
                raise UserError("error.ticket_unknown", number=number)
            return TicketView.from_model(ticket)

    async def open_tickets(self, guild_id: int) -> list[TicketView]:
        async with self._db.session() as session:
            return [TicketView.from_model(t) for t in await repo.open_tickets(session, guild_id)]

    # --- Zustand ---

    async def claim(self, guild_id: int, channel_id: int, user_id: int) -> TicketView:
        ticket = await self.require_channel(guild_id, channel_id)
        if not ticket.is_open:
            raise UserError("error.ticket_closed")
        if ticket.claimed_by == user_id:
            raise UserError("error.ticket_claimed_by_you")
        if ticket.claimed_by is not None:
            raise UserError("error.ticket_claimed", user=f"<@{ticket.claimed_by}>")
        async with self._db.session() as session:
            # Atomar: zwei Support-Mitglieder, die gleichzeitig klicken, gewinnt genau eines.
            if not await repo.set_claim(session, guild_id, channel_id, None, user_id):
                raise UserError("error.ticket_claim_lost")
        return await self.require_channel(guild_id, channel_id)

    async def unclaim(
        self, guild_id: int, channel_id: int, user_id: int, *, is_manager: bool
    ) -> TicketView:
        ticket = await self.require_channel(guild_id, channel_id)
        if not ticket.is_open:
            raise UserError("error.ticket_closed")
        if ticket.claimed_by is None:
            raise UserError("error.ticket_not_claimed")
        if ticket.claimed_by != user_id and not is_manager:
            raise UserError("error.ticket_claimed", user=f"<@{ticket.claimed_by}>")
        async with self._db.session() as session:
            if not await repo.set_claim(session, guild_id, channel_id, ticket.claimed_by, None):
                raise UserError("error.ticket_claim_lost")
        return await self.require_channel(guild_id, channel_id)

    async def close(
        self,
        guild_id: int,
        channel_id: int,
        *,
        closed_by: int,
        reason: str | None,
        html: str,
        message_count: int,
    ) -> TicketView:
        """Schliesst das Ticket und speichert das Transkript in einer Transaktion: es gibt kein
        geschlossenes Ticket ohne Transkript. Ein zweiter Versuch scheitert sauber."""
        if reason is not None and len(reason) > MAX_REASON:
            raise UserError("error.ticket_reason")
        async with self._db.session() as session:
            ticket = await repo.close_ticket(
                session,
                guild_id,
                channel_id,
                closed_by=closed_by,
                reason=reason or None,
                now=_utcnow(),
            )
            if ticket is None:
                if await repo.by_channel(session, guild_id, channel_id) is None:
                    raise UserError("error.ticket_not_here")
                raise UserError("error.ticket_closed")
            await repo.save_transcript(session, guild_id, ticket.id, html, message_count)
            return TicketView.from_model(ticket)

    # --- Transkript, Bewertung ---

    async def transcript(self, guild_id: int, number: int) -> tuple[TicketView, str]:
        async with self._db.session() as session:
            ticket = await repo.by_number(session, guild_id, number)
            if ticket is None:
                raise UserError("error.ticket_unknown", number=number)
            stored = await repo.get_transcript(session, guild_id, ticket.id)
            if stored is None:
                raise UserError("error.ticket_no_transcript", number=number)
            return TicketView.from_model(ticket), stored.html

    async def purge_transcripts(self, guild_id: int, *, now: datetime | None = None) -> int:
        """Löscht Transkripte nach Ablauf der Aufbewahrungsfrist des Servers (0 = nie)."""
        days = (await self.settings(guild_id)).retention_days
        if days <= 0:
            return 0
        cutoff = (now or _utcnow()) - timedelta(days=days)
        async with self._db.session() as session:
            return await repo.purge_transcripts(session, guild_id, cutoff)

    async def rate(self, guild_id: int, ticket_id: int, user_id: int, rating: int) -> TicketView:
        if not 1 <= rating <= 5:
            raise UserError("error.ticket_rating")
        async with self._db.session() as session:
            ticket = await repo.by_id(session, guild_id, ticket_id)
            if ticket is None or ticket.creator_id != user_id:
                # Fremde Tickets gibt es für andere Nutzer nicht.
                raise UserError("error.ticket_unknown", number=ticket_id)
            if ticket.status != "closed":
                raise UserError("error.ticket_not_closed")
            if not await repo.add_feedback(session, guild_id, ticket_id, user_id, rating):
                raise UserError("error.ticket_rated")
            return TicketView.from_model(ticket)

    async def feedback_summary(self, guild_id: int) -> tuple[int, float]:
        async with self._db.session() as session:
            return await repo.feedback_summary(session, guild_id)
