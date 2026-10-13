"""Temporäre Sprachkanäle: Buchführung, wem welcher Kanal gehört. Ohne Discord."""

from ggbot.core.db.engine import Database
from ggbot.core.db.repos import channels as repo
from ggbot.core.settings.cache import SettingsCache
from ggbot.core.settings.store import SettingsStore
from ggbot.core.settings.tempvoice import TempVoiceSettings


class TempVoiceService:
    def __init__(self, db: Database, store: SettingsStore) -> None:
        self._db = db
        self._settings = SettingsCache(store, TempVoiceSettings)

    async def settings(self, guild_id: int) -> TempVoiceSettings:
        return await self._settings.get(guild_id)

    def invalidate(self, guild_id: int) -> None:
        self._settings.invalidate(guild_id)

    async def register(self, guild_id: int, channel_id: int, owner_id: int) -> None:
        async with self._db.session() as session:
            await repo.voice_add(session, guild_id, channel_id, owner_id)

    async def owner(self, channel_id: int) -> int | None:
        async with self._db.session() as session:
            return await repo.voice_owner(session, channel_id)

    async def remove(self, channel_id: int) -> bool:
        """True, wenn dieser Aufruf den Eintrag entfernt hat (wer es war, löscht den Kanal)."""
        async with self._db.session() as session:
            return await repo.voice_remove(session, channel_id)

    async def channel_ids(self, guild_id: int) -> list[int]:
        async with self._db.session() as session:
            return [r.channel_id for r in await repo.voice_list(session, guild_id)]

    async def count(self, guild_id: int) -> int:
        async with self._db.session() as session:
            return await repo.voice_count(session, guild_id)
