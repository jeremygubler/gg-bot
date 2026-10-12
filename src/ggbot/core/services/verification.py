"""Verifizierung: Einstellungen und der Zustand der Bild-Aufgabe (Redis). Ohne Discord."""

from dataclasses import dataclass
from enum import StrEnum

from redis.asyncio import Redis

from ggbot.core.errors import UserError
from ggbot.core.redis import get_str, key
from ggbot.core.settings.cache import SettingsCache
from ggbot.core.settings.store import SettingsStore
from ggbot.core.settings.verification import VerificationSettings
from ggbot.core.verification import rules


class Outcome(StrEnum):
    OK = "ok"
    WRONG = "wrong"
    EXPIRED = "expired"
    LOCKED = "locked"


@dataclass(frozen=True)
class Attempt:
    outcome: Outcome
    remaining: int = 0


class VerificationService:
    def __init__(self, redis: Redis, store: SettingsStore) -> None:
        self._redis = redis
        self._settings = SettingsCache(store, VerificationSettings)

    async def settings(self, guild_id: int) -> VerificationSettings:
        return await self._settings.get(guild_id)

    def invalidate(self, guild_id: int) -> None:
        self._settings.invalidate(guild_id)

    @staticmethod
    def _keys(guild_id: int, user_id: int) -> tuple[str, str, str]:
        return (
            key("verify", "code", guild_id, user_id),
            key("verify", "tries", guild_id, user_id),
            key("verify", "lock", guild_id, user_id),
        )

    async def begin(self, guild_id: int, user_id: int, code: str) -> None:
        """Merkt sich den Code der Aufgabe. Wer gesperrt ist, bekommt keine neue."""
        code_key, _tries, lock_key = self._keys(guild_id, user_id)
        if await self._redis.exists(lock_key):
            raise UserError("error.verify_locked", minutes=rules.LOCK_SECONDS // 60)
        await self._redis.set(code_key, code, ex=rules.CODE_TTL_SECONDS)

    async def answer(self, guild_id: int, user_id: int, given: str) -> Attempt:
        """Prüft eine Antwort. Insgesamt höchstens `MAX_ATTEMPTS` Versuche je Sperrfenster, auch
        über mehrere Bilder hinweg und auch bei gleichzeitigen Eingaben: der Zähler wird *vor* dem
        Vergleich atomar erhöht, ein zweiter Tab bekommt keinen zusätzlichen Versuch."""
        code_key, tries_key, lock_key = self._keys(guild_id, user_id)
        if await self._redis.exists(lock_key):
            return Attempt(Outcome.LOCKED)
        expected = await get_str(self._redis, code_key)
        if expected is None:
            return Attempt(Outcome.EXPIRED)
        tries = int(await self._redis.incr(tries_key))
        if tries == 1:
            await self._redis.expire(tries_key, rules.LOCK_SECONDS)
        if tries > rules.MAX_ATTEMPTS:
            await self._lock(code_key, lock_key)
            return Attempt(Outcome.LOCKED)
        if rules.matches(expected, given):
            await self._redis.delete(code_key, tries_key)
            return Attempt(Outcome.OK)
        if tries == rules.MAX_ATTEMPTS:
            await self._lock(code_key, lock_key)
            return Attempt(Outcome.LOCKED)
        return Attempt(Outcome.WRONG, remaining=rules.MAX_ATTEMPTS - tries)

    async def _lock(self, code_key: str, lock_key: str) -> None:
        await self._redis.set(lock_key, "1", ex=rules.LOCK_SECONDS)
        await self._redis.delete(code_key)
