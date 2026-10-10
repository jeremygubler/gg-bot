"""Öffentliche Rangliste: vom Bot gebaut, von der API ohne Anmeldung ausgeliefert.

Die Datenbank kennt nur Nutzer-IDs. Anzeigenamen und Avatare weiss allein der Bot; er legt sie für
die Top-Plätze kurz in Redis ab. Nichts davon landet in der Datenbank, und nach Ablauf der
Gültigkeit ist es weg. Die API liest nur diesen Zwischenspeicher.
"""

from pydantic import BaseModel
from redis.asyncio import Redis

from ggbot.core.redis import get_str, key

TTL_SECONDS = 2 * 3600
TOP = 100


class BoardRow(BaseModel):
    position: int
    # None: Name (noch) nicht aufgelöst; die Oberfläche zeigt dann einen Platzhalter.
    name: str | None
    avatar_url: str | None
    level: int
    xp: int


class PublicBoard(BaseModel):
    guild_name: str
    rows: list[BoardRow]


def _key(guild_id: int) -> str:
    return key("publicboard", guild_id)


async def save_board(redis: Redis, guild_id: int, board: PublicBoard) -> None:
    await redis.set(_key(guild_id), board.model_dump_json(), ex=TTL_SECONDS)


async def load_board(redis: Redis, guild_id: int) -> PublicBoard | None:
    raw = await get_str(redis, _key(guild_id))
    return PublicBoard.model_validate_json(raw) if raw is not None else None


async def delete_board(redis: Redis, guild_id: int) -> None:
    await redis.delete(_key(guild_id))
