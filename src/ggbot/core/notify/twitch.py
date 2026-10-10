"""Twitch über die Helix-API mit App-Token (Client-Credentials). Abfrage in Paketen zu 100."""

import time
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

import httpx

from ggbot.core.notify.rules import TWITCH_LOGIN
from ggbot.core.notify.types import ProviderError, Resolved, StreamInfo

OAUTH_ENDPOINT = "https://id.twitch.tv/oauth2/token"
API = "https://api.twitch.tv/helix"
BATCH = 100
TOKEN_MARGIN = 60  # Sekunden vor Ablauf erneuern


def parse_stream(item: dict[str, Any]) -> StreamInfo | None:
    """Ein Eintrag aus `/streams`; None, wenn er unbrauchbar oder kein Live-Stream ist."""
    try:
        if item.get("type", "live") != "live":
            return None
        return StreamInfo(
            id=str(item["id"]),
            login=str(item["user_login"]).lower(),
            name=str(item.get("user_name") or item["user_login"]),
            title=str(item.get("title") or ""),
            game=str(item.get("game_name") or ""),
            viewers=int(item.get("viewer_count") or 0),
            started_at=datetime.fromisoformat(str(item["started_at"]).replace("Z", "+00:00")),
            thumbnail=str(item.get("thumbnail_url") or "")
            .replace("{width}", "440")
            .replace("{height}", "248"),
        )
    except (KeyError, ValueError, TypeError):
        return None


def normalize_login(text: str) -> str:
    """Der Twitch-Login aus Name, @Name oder Kanal-Adresse; sonst `ProviderError`."""
    login = text.strip().lower().removeprefix("@")
    if "twitch.tv/" in login:
        login = login.split("twitch.tv/", 1)[-1].split("/", 1)[0].split("?", 1)[0]
    if not TWITCH_LOGIN.match(login):
        raise ProviderError("not_found", "ungültiger Name")
    return login


class TwitchClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        client_id: str,
        client_secret: str,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = http
        self._client_id = client_id
        self._client_secret = client_secret
        self._clock = clock
        self._token: str | None = None
        self._expires = 0.0

    @property
    def configured(self) -> bool:
        return bool(self._client_id and self._client_secret)

    async def _ensure_token(self) -> str:
        if self._token is not None and self._clock() < self._expires:
            return self._token
        try:
            response = await self._http.post(
                OAUTH_ENDPOINT,
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "grant_type": "client_credentials",
                },
            )
        except httpx.HTTPError as exc:
            raise ProviderError("unavailable", type(exc).__name__) from exc
        if response.status_code in (400, 401, 403):
            # Falsche Zugangsdaten: nicht mit der Antwort loggen, sie könnte das Secret spiegeln.
            raise ProviderError("not_configured", f"Token abgelehnt (HTTP {response.status_code})")
        if response.status_code != 200:
            raise ProviderError("unavailable", f"HTTP {response.status_code}")
        data = response.json()
        self._token = str(data["access_token"])
        self._expires = self._clock() + max(int(data.get("expires_in", 3600)) - TOKEN_MARGIN, 0)
        return self._token

    async def _get(self, path: str, params: Sequence[tuple[str, str]]) -> dict[str, Any]:
        for attempt in (1, 2):
            token = await self._ensure_token()
            try:
                response = await self._http.get(
                    f"{API}{path}",
                    params=list(params),
                    headers={"Client-Id": self._client_id, "Authorization": f"Bearer {token}"},
                )
            except httpx.HTTPError as exc:
                raise ProviderError("unavailable", type(exc).__name__) from exc
            if response.status_code == 401 and attempt == 1:
                self._token = None  # abgelaufen oder widerrufen: einmal neu holen
                continue
            if response.status_code == 429:
                raise ProviderError("unavailable", "Rate Limit")
            if response.status_code != 200:
                raise ProviderError("unavailable", f"HTTP {response.status_code}")
            body: dict[str, Any] = response.json()
            return body
        raise ProviderError("unavailable", "Token")

    async def live(self, logins: list[str]) -> dict[str, StreamInfo]:
        """Wer von den Logins gerade live ist (Schlüssel: Login in Kleinbuchstaben)."""
        result: dict[str, StreamInfo] = {}
        for start in range(0, len(logins), BATCH):
            chunk = logins[start : start + BATCH]
            body = await self._get(
                "/streams", [("user_login", login) for login in chunk] + [("first", str(BATCH))]
            )
            for item in body.get("data", []):
                stream = parse_stream(item)
                if stream is not None:
                    result[stream.login] = stream
        return result

    async def resolve(self, text: str) -> Resolved:
        login = normalize_login(text)
        body = await self._get("/users", [("login", login)])
        users = body.get("data", [])
        if not users:
            raise ProviderError("not_found", login)
        live = await self.live([login])
        stream = live.get(login)
        return Resolved(
            provider="twitch",
            ref=login,
            display_name=str(users[0].get("display_name") or login),
            live_stream_id=stream.id if stream else None,
        )
