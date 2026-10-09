"""Merkt sich, wie ein Kanal vor `/lock` eingestellt war, damit `/unlock` ihn exakt zurückstellt."""

from ggbot.core.db.engine import Database
from ggbot.core.db.repos import moderation as repo
from ggbot.core.errors import UserError

# Zustand von `send_messages` für @everyone: True = erlaubt, False = verboten, None = geerbt.
Tristate = bool | None


class ChannelLockService:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def register_lock(
        self, guild_id: int, channel_id: int, *, previous: Tristate, locked_by: int
    ) -> None:
        """Muss vor dem Ändern der Rechte aufgerufen werden; bei Fehlschlag `forget` aufrufen."""
        if previous is False:
            raise UserError("error.lock_already")  # schon von Hand gesperrt
        async with self._db.session() as session:
            if await repo.get_lock(session, guild_id, channel_id) is not None:
                raise UserError("error.lock_already")
            await repo.save_lock(session, guild_id, channel_id, previous, locked_by)

    async def previous_state(self, guild_id: int, channel_id: int) -> Tristate:
        """Der Zustand vor dem Sperren. Wirft, wenn der Kanal nicht von uns gesperrt ist."""
        async with self._db.session() as session:
            lock = await repo.get_lock(session, guild_id, channel_id)
        if lock is None:
            raise UserError("error.lock_not_locked")
        return lock.previous_send_messages

    async def forget(self, guild_id: int, channel_id: int) -> None:
        async with self._db.session() as session:
            await repo.delete_lock(session, guild_id, channel_id)
