"""Guild-Snapshot (Kanäle, Rollen, Emojis) in Redis.

Der Bot schreibt, die API liest. So braucht das Dashboard weder Bot-Token noch
Gateway-Verbindung für die Auswahlfelder.
"""

from pydantic import BaseModel
from redis.asyncio import Redis

from ggbot.core.redis import get_str, key

TTL_SECONDS = 7 * 24 * 3600


class ChannelInfo(BaseModel):
    id: int
    name: str
    type: str
    position: int
    parent_id: int | None = None


class RoleInfo(BaseModel):
    id: int
    name: str
    color: int
    position: int
    managed: bool


class EmojiInfo(BaseModel):
    id: int
    name: str
    animated: bool


class GuildSnapshot(BaseModel):
    guild_id: int
    name: str
    channels: list[ChannelInfo]
    roles: list[RoleInfo]
    emojis: list[EmojiInfo]


def _key(guild_id: int) -> str:
    return key("snapshot", guild_id)


async def save_snapshot(redis: Redis, snapshot: GuildSnapshot) -> None:
    await redis.set(_key(snapshot.guild_id), snapshot.model_dump_json(), ex=TTL_SECONDS)


async def load_snapshot(redis: Redis, guild_id: int) -> GuildSnapshot | None:
    raw = await get_str(redis, _key(guild_id))
    return GuildSnapshot.model_validate_json(raw) if raw is not None else None
