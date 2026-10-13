"""Ankündigungen im Dashboard bauen, planen, senden und nachträglich bearbeiten.

Der Entwurf lebt in der Datenbank. Gesendet wird vom Bot über den Scheduler: die API plant nur
ein und liest danach den Zustand aus der Zeile (`scheduled` → `sent` oder `failed`).
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import AwareDatetime, BaseModel, Field

from ggbot.api import policy
from ggbot.api.access import Csrf, GuildAccess, guild_access
from ggbot.api.errors import request_locale
from ggbot.api.ids import Id
from ggbot.api.ratelimit import hit
from ggbot.core.announce import message as msg
from ggbot.core.db.repos import settings as audit_repo
from ggbot.core.i18n import translator
from ggbot.core.services.announcements import AnnouncementService, AnnouncementView
from ggbot.core.settings.types import Snowflake

router = APIRouter()

WRITES_PER_MINUTE = 60
AUDIT_MODULE = "announcements"
Access = Annotated[GuildAccess, Depends(guild_access)]


class AnnouncementIn(BaseModel):
    name: str = Field(min_length=1, max_length=msg.MAX_NAME)
    content: str | None = Field(default=None, max_length=msg.MAX_CONTENT)
    embed: msg.Embed | None = None


class SendIn(BaseModel):
    channel_id: Snowflake
    # Leer = sofort. Mit Zeitzone, sonst wüsste der Server nicht, wann „18:00“ ist.
    scheduled_at: AwareDatetime | None = None


class AnnouncementOut(BaseModel):
    id: str
    name: str
    content: str | None
    embed: msg.Embed | None
    state: str
    channel_id: str | None
    message_id: str | None
    scheduled_at: datetime | None
    sent_at: datetime | None
    error: str | None


def _opt(value: int | None) -> str | None:
    return None if value is None else str(value)


def out(request: Request, view: AnnouncementView) -> AnnouncementOut:
    locale = request_locale(request, request.app.state.config.default_locale)
    return AnnouncementOut(
        id=str(view.id),
        name=view.name,
        content=view.message.content,
        embed=view.message.embed,
        state=view.state,
        channel_id=_opt(view.channel_id),
        message_id=_opt(view.message_id),
        scheduled_at=view.scheduled_at,
        sent_at=view.sent_at,
        error=translator.t(locale, view.error_key) if view.error_key else None,
    )


def _service(request: Request) -> AnnouncementService:
    return request.app.state.announcements  # type: ignore[no-any-return]


async def _write_gate(request: Request, access: GuildAccess) -> None:
    await hit(
        request.app.state.redis,
        "announce_write",
        access.session.user_id,
        limit=WRITES_PER_MINUTE,
        window=60,
    )


async def _audit(request: Request, access: GuildAccess, action: str, **details: object) -> None:
    async with request.app.state.db.session() as session:
        await audit_repo.add_audit(
            session,
            access.guild.id,
            access.session.user_id,
            AUDIT_MODULE,
            None,
            {"action": action, **{k: str(v) for k, v in details.items()}},
        )


@router.get("/guilds/{guild_id}/announcements")
async def list_announcements(request: Request, access: Access) -> list[AnnouncementOut]:
    return [out(request, v) for v in await _service(request).list(access.guild.id)]


@router.get("/guilds/{guild_id}/announcements/{announcement_id}")
async def get_announcement(
    announcement_id: Id, request: Request, access: Access
) -> AnnouncementOut:
    return out(request, await _service(request).get(access.guild.id, announcement_id))


@router.post("/guilds/{guild_id}/announcements", dependencies=[Csrf], status_code=201)
async def create_announcement(
    body: AnnouncementIn, request: Request, access: Access
) -> AnnouncementOut:
    await _write_gate(request, access)
    view = await _service(request).create(
        access.guild.id,
        access.session.user_id,
        body.name,
        msg.Message(content=body.content, embed=body.embed),
    )
    return out(request, view)


@router.put("/guilds/{guild_id}/announcements/{announcement_id}", dependencies=[Csrf])
async def update_announcement(
    announcement_id: Id, body: AnnouncementIn, request: Request, access: Access
) -> AnnouncementOut:
    await _write_gate(request, access)
    view = await _service(request).update(
        access.guild.id,
        announcement_id,
        body.name,
        msg.Message(content=body.content, embed=body.embed),
    )
    if view.state == "sent":
        await _audit(request, access, "edit", announcement_id=view.id, channel_id=view.channel_id)
    return out(request, view)


@router.delete("/guilds/{guild_id}/announcements/{announcement_id}", dependencies=[Csrf])
async def delete_announcement(announcement_id: Id, request: Request, access: Access) -> None:
    await _write_gate(request, access)
    await _service(request).delete(access.guild.id, announcement_id)


@router.post(
    "/guilds/{guild_id}/announcements/{announcement_id}/send",
    dependencies=[Csrf],
    status_code=202,
)
async def send_announcement(
    announcement_id: Id, body: SendIn, request: Request, access: Access
) -> AnnouncementOut:
    await _write_gate(request, access)
    policy.check_text_channel(access.snapshot, body.channel_id)
    view = await _service(request).schedule(
        access.guild.id, announcement_id, body.channel_id, body.scheduled_at
    )
    await _audit(
        request,
        access,
        "schedule" if body.scheduled_at else "send",
        announcement_id=view.id,
        channel_id=view.channel_id,
        scheduled_at=view.scheduled_at,
    )
    return out(request, view)


@router.post("/guilds/{guild_id}/announcements/{announcement_id}/cancel", dependencies=[Csrf])
async def cancel_announcement(
    announcement_id: Id, request: Request, access: Access
) -> AnnouncementOut:
    await _write_gate(request, access)
    view = await _service(request).cancel(access.guild.id, announcement_id)
    await _audit(request, access, "cancel", announcement_id=view.id)
    return out(request, view)
