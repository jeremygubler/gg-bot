"""Sitzungen: Daten in Redis, im Cookie nur eine signierte Sitzungs-ID."""

import hashlib
import hmac
import secrets

from pydantic import BaseModel
from redis.asyncio import Redis

from ggbot.api.discord import UserGuild
from ggbot.core.redis import get_str, key

TTL_SECONDS = 7 * 24 * 3600
COOKIE_NAME = "gg_session"
STATE_COOKIE = "gg_oauth_state"


class Session(BaseModel):
    user_id: int
    username: str
    avatar: str | None = None
    # Bleibt serverseitig. Wird gebraucht, um die Server-Liste neu zu laden, damit
    # entzogene Rechte binnen einer Minute wirken und nicht erst nach 7 Tagen.
    access_token: str
    guilds: list[UserGuild]
    guilds_at: float


def _sign(secret: str, sid: str) -> str:
    return hmac.new(secret.encode(), sid.encode(), hashlib.sha256).hexdigest()


def sign_value(secret: str, value: str) -> str:
    return f"{value}.{_sign(secret, value)}"


def verify_value(secret: str, signed: str) -> str | None:
    value, _, signature = signed.rpartition(".")
    if not value or not hmac.compare_digest(signature, _sign(secret, value)):
        return None
    return value


class SessionStore:
    def __init__(self, redis: Redis, secret: str) -> None:
        self._redis = redis
        self._secret = secret

    @staticmethod
    def _key(sid: str) -> str:
        return key("session", sid)

    async def create(self, session: Session) -> str:
        """Legt die Sitzung an und liefert den Cookie-Wert."""
        sid = secrets.token_urlsafe(32)
        await self._redis.set(self._key(sid), session.model_dump_json(), ex=TTL_SECONDS)
        return sign_value(self._secret, sid)

    async def load(self, cookie: str | None) -> tuple[str, Session] | None:
        sid = verify_value(self._secret, cookie) if cookie else None
        if sid is None:
            return None
        raw = await get_str(self._redis, self._key(sid))
        return (sid, Session.model_validate_json(raw)) if raw is not None else None

    async def save(self, sid: str, session: Session) -> None:
        # Ablaufzeit beibehalten: ein Neuladen der Server-Liste verlängert die Sitzung nicht.
        await self._redis.set(self._key(sid), session.model_dump_json(), keepttl=True)

    async def delete(self, sid: str) -> None:
        await self._redis.delete(self._key(sid))
