"""Minimaler Discord-OAuth2-Client fürs Dashboard (Scopes `identify guilds`)."""

from typing import Any
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel

from ggbot.core.config import AppConfig

API = "https://discord.com/api/v10"
AUTHORIZE_URL = "https://discord.com/oauth2/authorize"
SCOPES = "identify guilds"


# Rechte, die der Bot laut README braucht; das Dashboard hängt sie an den Einladungslink.
# Als Zahl (Discord-Bits), damit die API discord.py nicht importieren muss; ein Test gleicht
# sie mit discord.Permissions ab.
INVITE_PERMISSION_BITS = {
    "manage_channels": 1 << 4,
    "add_reactions": 1 << 6,
    "view_audit_log": 1 << 7,
    "view_channel": 1 << 10,
    "send_messages": 1 << 11,
    "manage_messages": 1 << 13,
    "embed_links": 1 << 14,
    "attach_files": 1 << 15,
    "read_message_history": 1 << 16,
    "manage_roles": 1 << 28,
    "manage_threads": 1 << 34,
    "kick_members": 1 << 1,
    "ban_members": 1 << 2,
    "moderate_members": 1 << 40,
    "use_application_commands": 1 << 31,
}
INVITE_PERMISSIONS = sum(INVITE_PERMISSION_BITS.values())


class DiscordError(Exception):
    """Discord hat abgelehnt oder war nicht erreichbar. `status` 401 = Token ungültig."""

    def __init__(self, status: int | None, message: str) -> None:
        super().__init__(message)
        self.status = status


class Token(BaseModel):
    access_token: str
    expires_in: int


class DiscordUser(BaseModel):
    id: int
    username: str
    global_name: str | None = None
    avatar: str | None = None


class UserGuild(BaseModel):
    id: int
    name: str
    icon: str | None = None
    owner: bool = False
    permissions: int = 0


class DiscordOAuth:
    def __init__(self, http: httpx.AsyncClient, config: AppConfig) -> None:
        self._http = http
        self._config = config

    def authorize_url(self, state: str) -> str:
        query = urlencode(
            {
                "client_id": self._config.discord_client_id,
                "redirect_uri": self._config.oauth_redirect_uri,
                "response_type": "code",
                "scope": SCOPES,
                "state": state,
            }
        )
        return f"{AUTHORIZE_URL}?{query}"

    async def exchange(self, code: str) -> Token:
        data = await self._request(
            "POST",
            "/oauth2/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self._config.oauth_redirect_uri,
            },
            auth=self._client_auth(),
        )
        return Token.model_validate(data)

    async def user(self, token: str) -> DiscordUser:
        return DiscordUser.model_validate(await self._bearer(token, "/users/@me"))

    async def guilds(self, token: str) -> list[UserGuild]:
        data = await self._bearer(token, "/users/@me/guilds")
        if not isinstance(data, list):
            raise DiscordError(None, "unerwartete Antwort von /users/@me/guilds")
        return [UserGuild.model_validate(item) for item in data]

    async def revoke(self, token: str) -> None:
        await self._request(
            "POST",
            "/oauth2/token/revoke",
            data={"token": token, "token_type_hint": "access_token"},
            auth=self._client_auth(),
        )

    def _client_auth(self) -> httpx.BasicAuth:
        return httpx.BasicAuth(
            self._config.discord_client_id, self._config.discord_client_secret.get_secret_value()
        )

    async def _bearer(self, token: str, path: str) -> Any:
        return await self._request("GET", path, headers={"Authorization": f"Bearer {token}"})

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = await self._http.request(method, f"{API}{path}", **kwargs)
        except httpx.HTTPError as exc:
            raise DiscordError(None, f"Discord nicht erreichbar: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            # Den Antworttext nicht in die Meldung: bei Token-Fehlern kann er Eingaben spiegeln.
            raise DiscordError(response.status_code, f"Discord antwortete {response.status_code}")
        if not response.content:
            return None
        return response.json()
