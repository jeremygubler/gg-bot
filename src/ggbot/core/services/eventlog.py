"""Entscheidet, ob und wohin ein Ereignis protokolliert wird. Ohne Discord."""

from ggbot.core.settings.cache import SettingsCache
from ggbot.core.settings.eventlog import (
    NEEDS_MEMBERS,
    NEEDS_MESSAGE_CONTENT,
    LoggingSettings,
)
from ggbot.core.settings.store import SettingsStore

# Diese Ereignisarten betreffen Nachrichten bzw. Sprachkanäle, für die die Ignorierlisten gelten.
_MESSAGE_EVENTS = frozenset({"message_delete", "message_edit", "message_bulk"})
_BOT_FILTERED = _MESSAGE_EVENTS | {"voice"}


def required_intent(event: str) -> str | None:
    """Der nötige privilegierte Intent ("message_content", "members") oder None."""
    if event in NEEDS_MESSAGE_CONTENT:
        return "message_content"
    if event in NEEDS_MEMBERS:
        return "members"
    return None


class EventLogService:
    def __init__(self, store: SettingsStore) -> None:
        self._cache = SettingsCache(store, LoggingSettings)

    async def settings(self, guild_id: int) -> LoggingSettings:
        return await self._cache.get(guild_id)

    def invalidate(self, guild_id: int) -> None:
        self._cache.invalidate(guild_id)

    async def route(
        self,
        guild_id: int,
        event: str,
        *,
        channel_id: int | None = None,
        is_bot: bool = False,
    ) -> int | None:
        """Der Zielkanal oder None, wenn nichts protokolliert werden soll.

        `channel_id` ist der Kanal, in dem das Ereignis stattfand (für die Ignorierliste).
        """
        settings = await self._cache.get(guild_id)
        target = settings.channels.get(event)
        if target is None:
            return None
        if is_bot and settings.ignore_bots and event in _BOT_FILTERED:
            return None
        if event in _MESSAGE_EVENTS and channel_id is not None:
            if channel_id in settings.ignored_channels:
                return None
            # Löschen in einem Log-Kanal selbst nicht protokollieren: das gäbe Rückkopplung.
            if channel_id in settings.channels.values():
                return None
        return target
