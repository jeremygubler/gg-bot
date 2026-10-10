"""Rollen- und Ticket-Panels im Dashboard anlegen, bearbeiten, löschen und veröffentlichen.

Alle Änderungen laufen durch dieselben Dienste wie die Slash-Befehle. Discord selbst fasst nur
der Bot an: nach jeder Änderung an einem veröffentlichten Panel schickt die API ein Event, der Bot
aktualisiert die Nachricht und meldet das Ergebnis über `panelstatus` zurück.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ggbot.api import policy
from ggbot.api.access import Csrf, GuildAccess, api_error, guild_access, is_administrator
from ggbot.api.errors import request_locale
from ggbot.api.ids import Id
from ggbot.api.ratelimit import hit
from ggbot.core import events
from ggbot.core.errors import UserError
from ggbot.core.i18n import translator
from ggbot.core.panelstatus import PanelStatus, get_status, set_status
from ggbot.core.services.rolepanels import (
    MAX_DESCRIPTION,
    MAX_ITEM_DESCRIPTION,
    MAX_LABEL,
    MAX_TITLE,
    PanelView,
    RolePanelService,
)
from ggbot.core.services.tickets import (
    MAX_CATEGORY_DESCRIPTION,
    MAX_NAME,
    MAX_WELCOME,
    CategoryView,
    TicketPanelView,
    TicketService,
)
from ggbot.core.settings.types import Snowflake

router = APIRouter()

WRITES_PER_MINUTE = 60
Access = Annotated[GuildAccess, Depends(guild_access)]


# --- Ausgabe (IDs als Strings, JavaScript verfälscht 64-Bit-Zahlen) ---


class RoleItemOut(BaseModel):
    id: str
    role_id: str
    label: str | None
    emoji: str | None
    description: str | None
    position: int


class RolePanelOut(BaseModel):
    id: str
    kind: str
    mode: str
    title: str
    description: str | None
    channel_id: str | None
    message_id: str | None
    external: bool
    published: bool
    items: list[RoleItemOut]


class CategoryOut(BaseModel):
    id: str
    name: str
    emoji: str | None
    description: str | None
    parent_id: str | None
    support_role_ids: list[str]
    welcome_message: str | None
    questions: list[str]


class TicketPanelOut(BaseModel):
    id: str
    title: str
    description: str | None
    channel_id: str | None
    message_id: str | None
    published: bool
    categories: list[CategoryOut]


def _opt(value: int | None) -> str | None:
    return None if value is None else str(value)


def role_panel_out(view: PanelView) -> RolePanelOut:
    return RolePanelOut(
        id=str(view.id),
        kind=view.kind,
        mode=view.mode,
        title=view.title,
        description=view.description,
        channel_id=_opt(view.channel_id),
        message_id=_opt(view.message_id),
        external=view.external,
        published=view.published,
        items=[
            RoleItemOut(
                id=str(i.id),
                role_id=str(i.role_id),
                label=i.label,
                emoji=i.emoji,
                description=i.description,
                position=i.position,
            )
            for i in view.items
        ],
    )


def _category_out(c: CategoryView) -> CategoryOut:
    return CategoryOut(
        id=str(c.id),
        name=c.name,
        emoji=c.emoji,
        description=c.description,
        parent_id=_opt(c.parent_id),
        support_role_ids=[str(r) for r in c.support_role_ids],
        welcome_message=c.welcome_message,
        questions=list(c.questions),
    )


def ticket_panel_out(view: TicketPanelView) -> TicketPanelOut:
    return TicketPanelOut(
        id=str(view.id),
        title=view.title,
        description=view.description,
        channel_id=_opt(view.channel_id),
        message_id=_opt(view.message_id),
        published=view.published,
        categories=[_category_out(c) for c in view.categories],
    )


# --- Eingabe ---


class RolePanelCreate(BaseModel):
    title: str = Field(max_length=MAX_TITLE)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION)
    kind: Literal["button", "select", "reaction"]
    mode: Literal["normal", "unique", "verify"] = "normal"


class RolePanelUpdate(BaseModel):
    title: str = Field(max_length=MAX_TITLE)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION)
    mode: Literal["normal", "unique", "verify"]


class RoleItemIn(BaseModel):
    role_id: Snowflake
    label: str | None = Field(default=None, max_length=MAX_LABEL)
    emoji: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=MAX_ITEM_DESCRIPTION)


class TicketPanelIn(BaseModel):
    title: str = Field(max_length=MAX_TITLE)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION)


class CategoryIn(BaseModel):
    name: str = Field(max_length=MAX_NAME)
    support_role_id: Snowflake
    emoji: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=MAX_CATEGORY_DESCRIPTION)
    parent_id: Snowflake | None = None
    welcome_message: str | None = Field(default=None, max_length=MAX_WELCOME)
    questions: list[str] = Field(default_factory=list, max_length=20)


class SupportRoleIn(BaseModel):
    role_id: Snowflake


class PublishIn(BaseModel):
    channel_id: Snowflake


class StatusOut(BaseModel):
    state: Literal["none", "pending", "done", "error"]
    message: str | None = None
    notice: str | None = None


# --- Hilfen ---


async def _write_gate(request: Request, access: GuildAccess) -> None:
    await hit(
        request.app.state.redis,
        "panel_write",
        access.session.user_id,
        limit=WRITES_PER_MINUTE,
        window=60,
    )


def _require_administrator(access: GuildAccess) -> None:
    """Rollen-Panels vergeben Rollen. Wer nur *Server verwalten* darf, könnte sich darüber selbst
    höher stufen; die Rollenhierarchie des Nutzers kennt die API nicht (bei den Slash-Befehlen
    muss seine höchste Rolle über der vergebenen stehen). Deshalb Eigentümer oder Administrator."""
    if not is_administrator(access.guild):
        raise api_error(403, "needs_administrator")


async def _notify(
    request: Request,
    access: GuildAccess,
    kind: Literal["role", "ticket"],
    panel_id: Id,
    *,
    action: Literal["publish", "refresh", "delete"],
    channel_id: int | None = None,
    message_id: int | None = None,
) -> None:
    redis = request.app.state.redis
    await set_status(redis, kind, access.guild.id, panel_id, PanelStatus(state="pending"))
    await events.publish(
        redis,
        events.PanelPublish(
            guild_id=access.guild.id,
            panel_id=panel_id,
            kind=kind,
            action=action,
            channel_id=channel_id,
            message_id=message_id,
        ),
    )


def _status_out(request: Request, status: PanelStatus | None) -> StatusOut:
    if status is None:
        return StatusOut(state="none")
    locale = request_locale(request, request.app.state.config.default_locale)
    message = (
        translator.t(locale, status.error_key, **status.error_params) if status.error_key else None
    )
    notice = (
        translator.t(locale, status.notice_key, **status.notice_params)
        if status.notice_key
        else None
    )
    return StatusOut(state=status.state, message=message, notice=notice)


# --- Rollen-Panels ---


def _roles(request: Request) -> RolePanelService:
    return request.app.state.rolepanels  # type: ignore[no-any-return]


@router.get("/guilds/{guild_id}/panels/roles")
async def list_role_panels(request: Request, access: Access) -> list[RolePanelOut]:
    return [role_panel_out(p) for p in await _roles(request).list(access.guild.id)]


@router.post("/guilds/{guild_id}/panels/roles", dependencies=[Csrf], status_code=201)
async def create_role_panel(
    body: RolePanelCreate, request: Request, access: Access
) -> RolePanelOut:
    _require_administrator(access)
    await _write_gate(request, access)
    view = await _roles(request).create(
        access.guild.id,
        kind=body.kind,
        mode=body.mode,
        title=body.title,
        description=body.description,
    )
    return role_panel_out(view)


async def _role_panel(request: Request, access: GuildAccess, panel_id: Id) -> PanelView:
    return await _roles(request).get(access.guild.id, panel_id)


async def _refresh_role_panel(request: Request, access: GuildAccess, view: PanelView) -> None:
    if view.published and not view.external:
        await _notify(request, access, "role", view.id, action="refresh")


@router.put("/guilds/{guild_id}/panels/roles/{panel_id}", dependencies=[Csrf])
async def update_role_panel(
    panel_id: Id, body: RolePanelUpdate, request: Request, access: Access
) -> RolePanelOut:
    _require_administrator(access)
    await _write_gate(request, access)
    view = await _roles(request).update(
        access.guild.id, panel_id, title=body.title, description=body.description, mode=body.mode
    )
    await _refresh_role_panel(request, access, view)
    return role_panel_out(view)


@router.delete("/guilds/{guild_id}/panels/roles/{panel_id}", dependencies=[Csrf], status_code=204)
async def delete_role_panel(panel_id: Id, request: Request, access: Access) -> None:
    _require_administrator(access)
    await _write_gate(request, access)
    view = await _roles(request).delete(access.guild.id, panel_id)
    if view.published and not view.external:
        await _notify(
            request,
            access,
            "role",
            view.id,
            action="delete",
            channel_id=view.channel_id,
            message_id=view.message_id,
        )


@router.post(
    "/guilds/{guild_id}/panels/roles/{panel_id}/items", dependencies=[Csrf], status_code=201
)
async def add_role_item(
    panel_id: Id, body: RoleItemIn, request: Request, access: Access
) -> RolePanelOut:
    _require_administrator(access)
    await _write_gate(request, access)
    policy.check_grantable(access.snapshot, body.role_id)
    emoji = policy.check_emoji(body.emoji)
    view = await _roles(request).add_item(
        access.guild.id,
        panel_id,
        role_id=body.role_id,
        label=body.label,
        emoji=emoji,
        description=body.description,
    )
    await _refresh_role_panel(request, access, view)
    return role_panel_out(view)


@router.delete("/guilds/{guild_id}/panels/roles/{panel_id}/items/{role_id}", dependencies=[Csrf])
async def remove_role_item(
    panel_id: Id, role_id: Id, request: Request, access: Access
) -> RolePanelOut:
    _require_administrator(access)
    await _write_gate(request, access)
    view, _removed = await _roles(request).remove_item(access.guild.id, panel_id, role_id)
    await _refresh_role_panel(request, access, view)
    return role_panel_out(view)


@router.post(
    "/guilds/{guild_id}/panels/roles/{panel_id}/publish", dependencies=[Csrf], status_code=202
)
async def publish_role_panel(
    panel_id: Id, body: PublishIn, request: Request, access: Access
) -> StatusOut:
    _require_administrator(access)
    await _write_gate(request, access)
    view = await _role_panel(request, access, panel_id)
    if view.external:
        raise UserError("error.panel_external_publish")
    if not view.items:
        raise UserError("error.panel_empty")
    policy.check_text_channel(access.snapshot, body.channel_id)
    await _notify(request, access, "role", panel_id, action="publish", channel_id=body.channel_id)
    return StatusOut(state="pending")


@router.get("/guilds/{guild_id}/panels/roles/{panel_id}/status")
async def role_panel_status(panel_id: Id, request: Request, access: Access) -> StatusOut:
    await _role_panel(request, access, panel_id)
    return _status_out(
        request, await get_status(request.app.state.redis, "role", access.guild.id, panel_id)
    )


# --- Ticket-Panels ---


def _tickets(request: Request) -> TicketService:
    return request.app.state.tickets  # type: ignore[no-any-return]


async def _refresh_ticket_panel(
    request: Request, access: GuildAccess, view: TicketPanelView
) -> None:
    if view.published:
        await _notify(request, access, "ticket", view.id, action="refresh")


async def _category_name(
    request: Request, access: GuildAccess, panel_id: Id, category_id: Id
) -> str:
    """Die Dienste adressieren Kategorien über den Namen; die API über die ID (Namen dürfen
    Schrägstriche und Sonderzeichen enthalten)."""
    panel = await _tickets(request).get_panel(access.guild.id, panel_id)
    category = next((c for c in panel.categories if c.id == category_id), None)
    if category is None:
        raise UserError("error.tcat_gone")
    return category.name


@router.get("/guilds/{guild_id}/panels/tickets")
async def list_ticket_panels(request: Request, access: Access) -> list[TicketPanelOut]:
    return [ticket_panel_out(p) for p in await _tickets(request).list_panels(access.guild.id)]


@router.post("/guilds/{guild_id}/panels/tickets", dependencies=[Csrf], status_code=201)
async def create_ticket_panel(
    body: TicketPanelIn, request: Request, access: Access
) -> TicketPanelOut:
    await _write_gate(request, access)
    view = await _tickets(request).create_panel(
        access.guild.id, title=body.title, description=body.description
    )
    return ticket_panel_out(view)


@router.put("/guilds/{guild_id}/panels/tickets/{panel_id}", dependencies=[Csrf])
async def update_ticket_panel(
    panel_id: Id, body: TicketPanelIn, request: Request, access: Access
) -> TicketPanelOut:
    await _write_gate(request, access)
    view = await _tickets(request).update_panel(
        access.guild.id, panel_id, title=body.title, description=body.description
    )
    await _refresh_ticket_panel(request, access, view)
    return ticket_panel_out(view)


@router.delete("/guilds/{guild_id}/panels/tickets/{panel_id}", dependencies=[Csrf], status_code=204)
async def delete_ticket_panel(panel_id: Id, request: Request, access: Access) -> None:
    await _write_gate(request, access)
    view = await _tickets(request).delete_panel(access.guild.id, panel_id)
    if view.published:
        await _notify(
            request,
            access,
            "ticket",
            view.id,
            action="delete",
            channel_id=view.channel_id,
            message_id=view.message_id,
        )


@router.post(
    "/guilds/{guild_id}/panels/tickets/{panel_id}/categories",
    dependencies=[Csrf],
    status_code=201,
)
async def add_category(
    panel_id: Id, body: CategoryIn, request: Request, access: Access
) -> TicketPanelOut:
    await _write_gate(request, access)
    policy.check_support_role(access.snapshot, body.support_role_id)
    if body.parent_id is not None:
        policy.check_category_channel(access.snapshot, body.parent_id)
    view = await _tickets(request).add_category(
        access.guild.id,
        panel_id,
        name=body.name,
        support_role_id=body.support_role_id,
        emoji=policy.check_emoji(body.emoji),
        description=body.description,
        parent_id=body.parent_id,
        welcome_message=body.welcome_message,
        questions=policy.clean_question_list(body.questions),
    )
    await _refresh_ticket_panel(request, access, view)
    return ticket_panel_out(view)


@router.delete(
    "/guilds/{guild_id}/panels/tickets/{panel_id}/categories/{category_id}", dependencies=[Csrf]
)
async def remove_category(
    panel_id: Id, category_id: Id, request: Request, access: Access
) -> TicketPanelOut:
    await _write_gate(request, access)
    name = await _category_name(request, access, panel_id, category_id)
    view = await _tickets(request).remove_category(access.guild.id, panel_id, name)
    await _refresh_ticket_panel(request, access, view)
    return ticket_panel_out(view)


@router.post(
    "/guilds/{guild_id}/panels/tickets/{panel_id}/categories/{category_id}/roles",
    dependencies=[Csrf],
    status_code=201,
)
async def add_support_role(
    panel_id: Id, category_id: Id, body: SupportRoleIn, request: Request, access: Access
) -> TicketPanelOut:
    await _write_gate(request, access)
    policy.check_support_role(access.snapshot, body.role_id)
    name = await _category_name(request, access, panel_id, category_id)
    view = await _tickets(request).change_support_role(
        access.guild.id, panel_id, name, body.role_id, add=True
    )
    return ticket_panel_out(view)


@router.delete(
    "/guilds/{guild_id}/panels/tickets/{panel_id}/categories/{category_id}/roles/{role_id}",
    dependencies=[Csrf],
)
async def remove_support_role(
    panel_id: Id, category_id: Id, role_id: Id, request: Request, access: Access
) -> TicketPanelOut:
    await _write_gate(request, access)
    name = await _category_name(request, access, panel_id, category_id)
    view = await _tickets(request).change_support_role(
        access.guild.id, panel_id, name, role_id, add=False
    )
    return ticket_panel_out(view)


@router.post(
    "/guilds/{guild_id}/panels/tickets/{panel_id}/publish", dependencies=[Csrf], status_code=202
)
async def publish_ticket_panel(
    panel_id: Id, body: PublishIn, request: Request, access: Access
) -> StatusOut:
    await _write_gate(request, access)
    view = await _tickets(request).get_panel(access.guild.id, panel_id)
    if not view.categories:
        raise UserError("error.tpanel_empty")
    policy.check_text_channel(access.snapshot, body.channel_id)
    await _notify(request, access, "ticket", panel_id, action="publish", channel_id=body.channel_id)
    return StatusOut(state="pending")


@router.get("/guilds/{guild_id}/panels/tickets/{panel_id}/status")
async def ticket_panel_status(panel_id: Id, request: Request, access: Access) -> StatusOut:
    await _tickets(request).get_panel(access.guild.id, panel_id)
    return _status_out(
        request, await get_status(request.app.state.redis, "ticket", access.guild.id, panel_id)
    )
