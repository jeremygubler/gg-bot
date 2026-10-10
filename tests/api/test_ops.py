import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from ggbot import __version__
from ggbot.api.main import create_app
from ggbot.api.metrics import Exposition
from ggbot.core.config import AppConfig
from ggbot.core.db.engine import Database
from ggbot.core.db.repos import jobs as jobs_repo
from ggbot.core.status import BotStatus, count, write_status
from tests.api.conftest import Api
from tests.conftest import TEST_DB_URL, TEST_REDIS_URL

TOKEN = "metrics-token-1234"


@pytest.fixture
async def secured(db: Database, redis: object) -> httpx.AsyncClient:  # type: ignore[misc]
    app = create_app(
        AppConfig(
            database_url=TEST_DB_URL,
            redis_url=TEST_REDIS_URL,
            metrics_token=TOKEN,
        )
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client,
    ):
        client.app = app  # type: ignore[attr-defined]
        yield client


def auth(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def sample(text: str, name: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith(name)]


async def test_metrics_sind_ohne_token_aus(api: Api) -> None:
    assert (await api.client.get("/metrics")).status_code == 404
    assert (await api.client.get("/metrics", headers=auth("irgendwas"))).status_code == 404


@pytest.mark.parametrize("header", [None, "Bearer falsch", "Basic " + TOKEN, TOKEN, "Bearer "])
async def test_metrics_verlangen_das_richtige_token(
    secured: httpx.AsyncClient, header: str | None
) -> None:
    headers = {} if header is None else {"Authorization": header}

    response = await secured.get("/metrics", headers=headers)

    assert response.status_code == 401


async def test_metrics_liefern_prometheus_text(secured: httpx.AsyncClient) -> None:
    response = await secured.get("/metrics", headers=auth())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain; version=0.0.4")
    body = response.text
    assert f'gg_info{{version="{__version__}"}} 1' in body
    assert "gg_db_up 1" in body
    assert "gg_redis_up 1" in body
    assert "gg_bot_up 0" in body  # kein Lebenszeichen im Test
    assert "# TYPE gg_scheduler_jobs gauge" in body
    assert body.endswith("\n")


async def test_metrics_zeigen_den_bot_wenn_er_lebt(secured: httpx.AsyncClient) -> None:
    redis = secured.app.state.redis  # type: ignore[attr-defined]
    now = time.time()
    await write_status(
        redis,
        BotStatus(
            version="9", started_at=now - 120, guilds=7, shards=2, latency_ms=45.5, written_at=now
        ),
    )
    await count(redis, "commands")
    await count(redis, "commands")
    await count(redis, "command_errors")

    body = (await secured.get("/metrics", headers=auth())).text

    assert "gg_bot_up 1" in body
    assert "gg_bot_guilds 7" in body
    assert "gg_bot_shards 2" in body
    assert "gg_bot_latency_seconds 0.0455" in body
    assert float(sample(body, "gg_bot_uptime_seconds")[0].split()[-1]) >= 120
    assert "gg_bot_commands_total 2" in body
    assert "gg_bot_command_errors_total 1" in body


async def test_metrics_zaehlen_jobs_und_verspaetung(secured: httpx.AsyncClient) -> None:
    db: Database = secured.app.state.db  # type: ignore[attr-defined]
    now = datetime.now(UTC)
    async with db.session() as session:
        await jobs_repo.enqueue(
            session, 1, "x", now + timedelta(hours=1)
        )  # später: pending, nicht fällig
        await jobs_repo.enqueue(
            session, 1, "x", now - timedelta(seconds=30)
        )  # fällig, aber noch ok
        await jobs_repo.enqueue(session, 1, "x", now - timedelta(seconds=300))  # überfällig

    body = (await secured.get("/metrics", headers=auth())).text

    assert 'gg_scheduler_jobs{state="pending"} 3' in body
    assert 'gg_scheduler_jobs{state="failed"} 0' in body
    assert "gg_scheduler_overdue_jobs 1" in body
    assert float(sample(body, "gg_scheduler_oldest_overdue_seconds")[0].split()[-1]) >= 299


def test_label_werte_werden_maskiert() -> None:
    out = Exposition()

    out.metric("m", "h", "gauge", [({"a": 'x"y\\z\n'}, 1)])

    assert 'm{a="x\\"y\\\\z\\n"} 1' in out.render()


async def test_healthz_nennt_version_und_botzustand(api: Api) -> None:
    down = (await api.client.get("/healthz")).json()
    await write_status(
        api.redis,
        BotStatus(version="1", started_at=1, guilds=1, shards=1, latency_ms=None, written_at=1),
    )
    up = (await api.client.get("/healthz")).json()

    assert down["bot"] == "down"
    assert up["bot"] == "ok"
    assert up["version"] == __version__
    assert (await api.client.get("/healthz")).status_code == 200  # Bot aus ≠ API ungesund


async def test_metriken_token_steht_nicht_im_log(
    secured: httpx.AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    await secured.get("/metrics", headers=auth("falsch"))

    out = capsys.readouterr()
    assert TOKEN not in out.out + out.err
