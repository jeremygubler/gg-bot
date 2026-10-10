"""Ergebnis einer Panel-Veröffentlichung, vom Bot geschrieben, vom Dashboard gelesen.

Das Veröffentlichen läuft über ein Event; der Bot ist der Einzige, der Discord anspricht. Damit
die Oberfläche trotzdem Erfolg oder Fehler zeigen kann, legt der Bot das Ergebnis kurz in Redis ab.
"""

from typing import Any, Literal

from pydantic import BaseModel
from redis.asyncio import Redis

from ggbot.core.redis import get_str, key

TTL_SECONDS = 600
Kind = Literal["role", "ticket"]


class PanelStatus(BaseModel):
    state: Literal["pending", "done", "error"]
    # Bei Fehlern der i18n-Schlüssel samt Parametern; die API übersetzt in der Sprache des Nutzers.
    error_key: str | None = None
    error_params: dict[str, Any] = {}
    # Veröffentlicht, aber mit Einschränkung (z. B. abgelehnte Reaktions-Emojis).
    notice_key: str | None = None
    notice_params: dict[str, Any] = {}


def _key(kind: str, guild_id: int, panel_id: int) -> str:
    return key("panelstatus", kind, guild_id, panel_id)


async def set_status(
    redis: Redis, kind: str, guild_id: int, panel_id: int, status: PanelStatus
) -> None:
    await redis.set(_key(kind, guild_id, panel_id), status.model_dump_json(), ex=TTL_SECONDS)


async def get_status(redis: Redis, kind: str, guild_id: int, panel_id: int) -> PanelStatus | None:
    raw = await get_str(redis, _key(kind, guild_id, panel_id))
    return PanelStatus.model_validate_json(raw) if raw is not None else None
