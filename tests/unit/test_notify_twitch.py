import json

import httpx
import pytest

from ggbot.core.notify.twitch import TwitchClient, parse_stream
from ggbot.core.notify.types import ProviderError


def item(**kw: object) -> dict[str, object]:
    return {
        "id": "111",
        "user_login": "Alice",
        "user_name": "Alice",
        "title": "Hallo",
        "game_name": "Spiel",
        "viewer_count": 42,
        "started_at": "2026-03-01T10:00:00Z",
        "thumbnail_url": "https://x/live_{width}x{height}.jpg",
        "type": "live",
    } | kw


def test_stream_lesen() -> None:
    s = parse_stream(item())
    assert s is not None
    assert (s.id, s.login, s.name, s.title, s.game, s.viewers) == (
        "111",
        "alice",
        "Alice",
        "Hallo",
        "Spiel",
        42,
    )
    assert s.thumbnail == "https://x/live_440x248.jpg"
    assert s.started_at.tzinfo is not None


@pytest.mark.parametrize(
    "bad",
    [item(type="rerun"), {"id": "1"}, item(started_at="gestern"), item(viewer_count="viele"), {}],
)
def test_unbrauchbare_eintraege(bad: dict[str, object]) -> None:
    assert parse_stream(bad) is None


def test_fehlende_felder_haben_standardwerte() -> None:
    s = parse_stream(item(title=None, game_name=None, viewer_count=None, thumbnail_url=None))
    assert s is not None
    assert (s.title, s.game, s.viewers, s.thumbnail) == ("", "", 0, "")


class Fake:
    """Ein Twitch, der Anfragen mitschreibt."""

    def __init__(
        self,
        streams: list[dict[str, object]] | None = None,
        users: list[dict[str, object]] | None = None,
    ) -> None:
        self.streams = streams or []
        self.users = users if users is not None else [{"login": "alice", "display_name": "Alice"}]
        self.token_requests = 0
        self.requests: list[httpx.Request] = []
        self.token_status = 200
        self.unauthorized_once = False
        self.expires_in = 3600

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "id.twitch.tv":
            self.token_requests += 1
            if self.token_status != 200:
                return httpx.Response(
                    self.token_status, json={"message": "invalid client secret geheim"}
                )
            return httpx.Response(
                200,
                json={"access_token": f"tok{self.token_requests}", "expires_in": self.expires_in},
            )
        if self.unauthorized_once:
            self.unauthorized_once = False
            return httpx.Response(401)
        if request.url.path.endswith("/users"):
            return httpx.Response(200, json={"data": self.users})
        return httpx.Response(200, json={"data": self.streams})


def make(fake: Fake, clock: list[float] | None = None) -> TwitchClient:
    now = clock if clock is not None else [0.0]
    return TwitchClient(
        httpx.AsyncClient(transport=httpx.MockTransport(fake)),
        "cid",
        "csecret",
        clock=lambda: now[0],
    )


async def test_live_abfrage_und_header() -> None:
    fake = Fake([item(), item(id="222", user_login="bob", user_name="Bob")])
    result = await make(fake).live(["alice", "bob", "carol"])
    assert set(result) == {"alice", "bob"}
    request = fake.requests[-1]
    assert request.headers["Client-Id"] == "cid"
    assert request.headers["Authorization"] == "Bearer tok1"
    assert [v for k, v in request.url.params.multi_items() if k == "user_login"] == [
        "alice",
        "bob",
        "carol",
    ]


async def test_pakete_zu_100() -> None:
    fake = Fake()
    logins = [f"user_{i:03d}" for i in range(250)]
    await make(fake).live(logins)
    calls = [r for r in fake.requests if r.url.path.endswith("/streams")]
    assert [
        len([1 for k, _ in c.url.params.multi_items() if k == "user_login"]) for c in calls
    ] == [100, 100, 50]


async def test_ohne_logins_keine_anfrage() -> None:
    fake = Fake()
    assert await make(fake).live([]) == {}
    assert fake.requests == []


async def test_token_wird_wiederverwendet_und_erneuert() -> None:
    fake = Fake()
    clock = [0.0]
    client = make(fake, clock)
    await client.live(["alice"])
    await client.live(["alice"])
    assert fake.token_requests == 1
    clock[0] = 3600.0  # abgelaufen
    await client.live(["alice"])
    assert fake.token_requests == 2


async def test_abgelehntes_token_wird_einmal_erneuert() -> None:
    fake = Fake()
    client = make(fake)
    await client.live(["alice"])
    fake.unauthorized_once = True
    await client.live(["alice"])
    assert fake.token_requests == 2


async def test_dauerhaft_401_wird_ein_fehler() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "id.twitch.tv":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return httpx.Response(401)

    client = TwitchClient(
        httpx.AsyncClient(transport=httpx.MockTransport(handler)), "cid", "csecret"
    )
    with pytest.raises(ProviderError) as exc:
        await client.live(["alice"])
    assert exc.value.key == "unavailable"


@pytest.mark.parametrize("status", [400, 401, 403])
async def test_falsche_zugangsdaten_verraten_nichts(status: int) -> None:
    fake = Fake()
    fake.token_status = status
    with pytest.raises(ProviderError) as exc:
        await make(fake).live(["alice"])
    assert exc.value.key == "not_configured"
    assert "geheim" not in str(exc.value)  # die Antwort des Servers wird nicht weitergegeben
    assert "csecret" not in str(exc.value)


@pytest.mark.parametrize(
    ("status", "key"), [(429, "unavailable"), (500, "unavailable"), (503, "unavailable")]
)
async def test_api_fehler(status: int, key: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "id.twitch.tv":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return httpx.Response(status)

    client = TwitchClient(
        httpx.AsyncClient(transport=httpx.MockTransport(handler)), "cid", "csecret"
    )
    with pytest.raises(ProviderError) as exc:
        await client.live(["alice"])
    assert exc.value.key == key


async def test_netzwerkfehler() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("weg")

    client = TwitchClient(httpx.AsyncClient(transport=httpx.MockTransport(boom)), "cid", "csecret")
    with pytest.raises(ProviderError) as exc:
        await client.live(["alice"])
    assert exc.value.key == "unavailable"


@pytest.mark.parametrize(
    "text",
    [
        "Alice",
        "@alice",
        " alice ",
        "https://www.twitch.tv/alice",
        "https://twitch.tv/alice?x=1",
        "twitch.tv/alice/videos",
    ],
)
async def test_resolve_formate(text: str) -> None:
    fake = Fake()
    resolved = await make(fake).resolve(text)
    assert (resolved.provider, resolved.ref, resolved.display_name) == ("twitch", "alice", "Alice")
    assert resolved.live_stream_id is None


async def test_resolve_live_streamer_ist_bekannt() -> None:
    fake = Fake([item()])
    resolved = await make(fake).resolve("alice")
    assert resolved.live_stream_id == "111"


@pytest.mark.parametrize("text", ["", "ab", "a b", "../x", "alice/../../x", "x" * 40, "ä"])
async def test_resolve_ungueltiger_name_ohne_anfrage(text: str) -> None:
    fake = Fake()
    with pytest.raises(ProviderError) as exc:
        await make(fake).resolve(text)
    assert exc.value.key == "not_found"
    assert fake.requests == []


async def test_resolve_unbekannter_streamer() -> None:
    fake = Fake(users=[])
    with pytest.raises(ProviderError) as exc:
        await make(fake).resolve("niemand")
    assert exc.value.key == "not_found"


async def test_token_anfrage_enthaelt_zugangsdaten_nur_im_body() -> None:
    fake = Fake()
    await make(fake).live(["alice"])
    token_request = next(r for r in fake.requests if r.url.host == "id.twitch.tv")
    assert "csecret" not in str(token_request.url)
    assert b"csecret" in token_request.content
    assert json.dumps(dict(token_request.headers)).find("csecret") == -1
