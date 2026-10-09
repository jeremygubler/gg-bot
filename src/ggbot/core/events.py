"""Bot↔Dashboard-Events über Redis Pub/Sub.

Ein Event ist nur ein Hinweis zum Neuladen. Die Daten liegen bereits validiert
in der Datenbank (siehe docs/ARCHITECTURE.md, Abschnitt 5).
"""

from collections.abc import AsyncIterator
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter
from redis.asyncio import Redis

from ggbot.core.logging import get_logger
from ggbot.core.redis import key

log = get_logger(__name__)

CHANNEL = key("events")


class SettingsChanged(BaseModel):
    type: Literal["settings.changed"] = "settings.changed"
    guild_id: int
    module: str


class GuildSync(BaseModel):
    type: Literal["guild.sync"] = "guild.sync"
    guild_id: int


class PanelPublish(BaseModel):
    type: Literal["panel.publish"] = "panel.publish"
    guild_id: int
    panel_id: int


Event = Annotated[SettingsChanged | GuildSync | PanelPublish, Field(discriminator="type")]
_adapter: TypeAdapter[Event] = TypeAdapter(Event)


async def publish(redis: Redis, event: SettingsChanged | GuildSync | PanelPublish) -> None:
    await redis.publish(CHANNEL, event.model_dump_json())


async def subscribe(redis: Redis) -> AsyncIterator[Event]:
    """Liefert Events, bis der Generator geschlossen wird.

    Unlesbare Nachrichten werden geloggt und übersprungen: ein fehlerhafter Sender
    soll den Bot nicht lahmlegen, und das Event ist nur ein Hinweis (die Wahrheit
    liegt in der DB).
    """
    pubsub = redis.pubsub()
    await pubsub.subscribe(CHANNEL)
    try:
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            try:
                yield _adapter.validate_json(message["data"])
            except ValueError:
                log.warning("event_invalid", data=str(message["data"])[:200])
    finally:
        await pubsub.unsubscribe(CHANNEL)
        await pubsub.aclose()  # type: ignore[no-untyped-call]
