from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from ggbot.api.main import create_app
from ggbot.core.config import AppConfig
from tests.conftest import TEST_DB_URL, TEST_REDIS_URL


@pytest.fixture
async def client(migrated: None) -> AsyncIterator[tuple[httpx.AsyncClient, FastAPI]]:
    app = create_app(AppConfig(database_url=TEST_DB_URL, redis_url=TEST_REDIS_URL))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
            yield http, app


async def test_healthz_ok(client: tuple[httpx.AsyncClient, FastAPI]) -> None:
    http, _ = client
    response = await http.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"db": "ok", "redis": "ok"}


async def test_healthz_meldet_503_wenn_redis_fehlt(
    client: tuple[httpx.AsyncClient, FastAPI],
) -> None:
    http, app = client
    from ggbot.core.redis import create_redis

    await app.state.redis.aclose()
    app.state.redis = create_redis("redis://localhost:1/0")  # nichts hört dort zu
    response = await http.get("/healthz")
    assert response.status_code == 503
    assert response.json() == {"db": "ok", "redis": "error"}
