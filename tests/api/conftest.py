"""Fixtures fürs Dashboard: echte API-App, echte DB/Redis, gespieltes Discord."""

import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from redis.asyncio import Redis

from ggbot.api.main import create_app
from ggbot.core.config import AppConfig
from ggbot.core.db.engine import Database
from ggbot.core.snapshot import ChannelInfo, GuildSnapshot, RoleInfo, save_snapshot
from tests.conftest import TEST_DB_URL, TEST_REDIS_URL

MANAGE_GUILD = 0x20
GUILD_A = 111111111111111111  # Nutzer verwaltet, Bot da
GUILD_B = 222222222222222222  # Nutzer ohne Recht, Bot da
GUILD_C = 333333333333333333  # Nutzer verwaltet, Bot fehlt
BIG_CHANNEL = 987654321098765432  # > 2**53
USER_ID = 444444444444444444


@dataclass
class FakeDiscord:
    """Antwortet wie Discord; Tests ändern Felder, um Fälle zu spielen."""

    guilds: list[dict[str, Any]] = field(default_factory=list)
    token_status: int = 200
    guilds_status: int = 200
    revoke_status: int = 200
    calls: list[tuple[str, str]] = field(default_factory=list)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path))
        path = request.url.path
        if path.endswith("/oauth2/token/revoke"):
            return httpx.Response(self.revoke_status)
        if path.endswith("/oauth2/token"):
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"access_token": "tok-123", "expires_in": 604800})
        if path.endswith("/users/@me/guilds"):
            if self.guilds_status != 200:
                return httpx.Response(self.guilds_status)
            return httpx.Response(200, json=self.guilds)
        if path.endswith("/users/@me"):
            return httpx.Response(
                200, json={"id": str(USER_ID), "username": "jeremy", "global_name": "Jeremy"}
            )
        return httpx.Response(404)


def default_guilds() -> list[dict[str, Any]]:
    return [
        {
            "id": str(GUILD_A),
            "name": "Alpha",
            "icon": None,
            "owner": False,
            "permissions": str(MANAGE_GUILD),
        },
        {"id": str(GUILD_B), "name": "Beta", "icon": None, "owner": False, "permissions": "0"},
        {"id": str(GUILD_C), "name": "Gamma", "icon": None, "owner": True, "permissions": "0"},
    ]


@pytest.fixture
def discord() -> FakeDiscord:
    return FakeDiscord(guilds=default_guilds())


@pytest.fixture
def config() -> AppConfig:
    return AppConfig(
        database_url=TEST_DB_URL,
        redis_url=TEST_REDIS_URL,
        discord_client_id="1234",
        discord_client_secret="client-secret-xyz",
        session_secret="s" * 40,
        dashboard_url="https://bot.example.org",
        cookie_secure=True,
    )


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


@dataclass
class Api:
    client: httpx.AsyncClient
    app: Any
    clock: Clock
    redis: Redis
    db: Database


@pytest.fixture
async def api(
    config: AppConfig, discord: FakeDiscord, db: Database, redis: Redis
) -> AsyncIterator[Api]:
    app = create_app(config, transport=httpx.MockTransport(discord.handler))
    async with app.router.lifespan_context(app):
        clock = Clock()
        app.state.clock = clock
        for guild_id, name in ((GUILD_A, "Alpha"), (GUILD_B, "Beta")):
            await save_snapshot(
                redis,
                GuildSnapshot(
                    guild_id=guild_id,
                    name=name,
                    channels=[ChannelInfo(id=BIG_CHANNEL, name="general", type="text", position=0)],
                    roles=[RoleInfo(id=5, name="Mod", color=0, position=1, managed=False)],
                    emojis=[],
                ),
            )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="https://bot.example.org", follow_redirects=False
        ) as client:
            yield Api(client, app, clock, redis, db)


async def login(api: Api) -> None:
    """Spielt den kompletten Login; danach trägt `api.client` das Sitzungs-Cookie."""
    response = await api.client.get("/auth/login")
    assert response.status_code == 302
    state = httpx.URL(response.headers["location"]).params["state"]
    response = await api.client.get("/auth/callback", params={"code": "abc", "state": state})
    assert response.status_code == 302, response.text


CSRF = {"X-Requested-With": "ggbot"}


def dump(value: Any) -> str:
    return json.dumps(value)


Login = Callable[[Api], Any]
