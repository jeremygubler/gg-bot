"""Server-Daten fürs Dashboard: Snapshot, Einstellungen, Änderungsprotokoll."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ValidationError

from ggbot.api.access import Csrf, GuildAccess, api_error, current_user, guild_access
from ggbot.api.ratelimit import hit
from ggbot.core.db.repos import settings as repo
from ggbot.core.settings.registry import MODELS
from ggbot.core.settings.schema import SettingsModel
from ggbot.core.settings.store import SettingsStore
from ggbot.core.settings.types import MAX_SNOWFLAKE
from ggbot.core.snapshot import GuildSnapshot

router = APIRouter()

WRITES_PER_MINUTE = 30
AUDIT_PAGE_MAX = 100


class ModuleInfo(BaseModel):
    module: str
    title: str


class AuditEntry(BaseModel):
    id: int
    user_id: str
    module: str
    before: dict[str, Any] | None
    after: dict[str, Any]
    at: str


def _model(module: str) -> type[SettingsModel]:
    model = MODELS.get(module)
    if model is None:
        raise api_error(404, "module_not_found")
    return model


@router.get("/modules", dependencies=[Depends(current_user)])
async def modules() -> list[ModuleInfo]:
    return [
        ModuleInfo(module=name, title=model.model_json_schema().get("title", name))
        for name, model in MODELS.items()
    ]


@router.get("/modules/{module}/schema", dependencies=[Depends(current_user)])
async def schema(module: str) -> dict[str, Any]:
    return _model(module).model_json_schema()


@router.get("/guilds/{guild_id}/snapshot")
async def snapshot(access: Annotated[GuildAccess, Depends(guild_access)]) -> GuildSnapshot:
    return access.snapshot


@router.get("/guilds/{guild_id}/settings/{module}")
async def get_settings(
    module: str,
    request: Request,
    access: Annotated[GuildAccess, Depends(guild_access)],
) -> dict[str, Any]:
    model = _model(module)
    store: SettingsStore = request.app.state.settings
    return (await store.get(access.guild.id, model)).model_dump(mode="json")


@router.put("/guilds/{guild_id}/settings/{module}", dependencies=[Csrf])
async def put_settings(
    module: str,
    body: dict[str, Any],
    request: Request,
    access: Annotated[GuildAccess, Depends(guild_access)],
) -> dict[str, Any]:
    model = _model(module)
    await hit(
        request.app.state.redis,
        "settings_write",
        access.session.user_id,
        limit=WRITES_PER_MINUTE,
        window=60,
    )
    try:
        new = model.model_validate(body)
    except ValidationError as exc:
        # Ohne Eingabewerte: die Oberfläche braucht nur Ort und Grund.
        raise HTTPException(
            status_code=422,
            detail={
                "error": "invalid_settings",
                "fields": [
                    {"loc": list(e["loc"]), "msg": e["msg"]}
                    for e in exc.errors(
                        include_url=False, include_input=False, include_context=False
                    )
                ],
            },
        ) from exc

    store: SettingsStore = request.app.state.settings
    current = await store.get(access.guild.id, model)
    if current == new:
        # Kein Audit-Eintrag, kein Event, wenn nichts geändert wurde.
        return new.model_dump(mode="json")
    await store.save(access.guild.id, new, audit_user_id=access.session.user_id)
    return new.model_dump(mode="json")


@router.get("/guilds/{guild_id}/audit")
async def audit(
    request: Request,
    access: Annotated[GuildAccess, Depends(guild_access)],
    limit: Annotated[int, Query(ge=1, le=AUDIT_PAGE_MAX)] = 50,
    before: Annotated[int | None, Query(ge=1, le=MAX_SNOWFLAKE)] = None,
) -> list[AuditEntry]:
    async with request.app.state.db.session() as session:
        rows = await repo.list_audit(session, access.guild.id, limit=limit, before_id=before)
    return [
        AuditEntry(
            id=row.id,
            user_id=str(row.user_id),
            module=row.module,
            before=row.before,
            after=row.after,
            at=row.at.isoformat(),
        )
        for row in rows
    ]
