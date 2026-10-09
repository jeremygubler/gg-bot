"""Kurzer Prozess-Cache für Einstellungen, die bei jedem Ereignis gebraucht werden."""

import time

from ggbot.core.settings.schema import SettingsModel
from ggbot.core.settings.store import SettingsStore

DEFAULT_TTL_SECONDS = 5.0


class SettingsCache[M: SettingsModel]:
    """Redis bleibt die gemeinsame Quelle. Der Cache spart nur die Anfrage pro Ereignis, und
    `invalidate` (Event aus dem Dashboard, Befehle) macht Änderungen sofort sichtbar."""

    def __init__(self, store: SettingsStore, model: type[M], ttl: float = DEFAULT_TTL_SECONDS):
        self._store = store
        self._model = model
        self._ttl = ttl
        self._entries: dict[int, tuple[float, M]] = {}

    async def get(self, guild_id: int) -> M:
        now = time.monotonic()
        cached = self._entries.get(guild_id)
        if cached is not None and cached[0] > now:
            return cached[1]
        settings = await self._store.get(guild_id, self._model)
        self._entries[guild_id] = (now + self._ttl, settings)
        return settings

    def invalidate(self, guild_id: int) -> None:
        self._entries.pop(guild_id, None)
