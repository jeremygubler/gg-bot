"""Die Abfrage von Anfang bis Ende: echte DB, simulierte Anbieter, Sender zum Mitschreiben."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from ggbot.core.db.engine import Database
from ggbot.core.notify.poller import Poller, SendResult
from ggbot.core.notify.rules import OFFLINE_AFTER_MISSES
from ggbot.core.notify.twitch import TwitchClient
from ggbot.core.notify.types import Resolved, StreamInfo, VideoInfo
from ggbot.core.notify.youtube import YouTubeClient
from ggbot.core.services.notify import NotifyService, SubView

G = 100
CID = "UC" + "a" * 22
NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


@dataclass
class FakeAnnouncer:
    guilds: set[int] = field(default_factory=lambda: {G, G + 1})
    result: SendResult = field(default_factory=lambda: SendResult("sent", 555))
    streams: list[tuple[int, str]] = field(default_factory=list)
    videos: list[tuple[int, str]] = field(default_factory=list)
    ended: list[int] = field(default_factory=list)

    def available(self, guild_id: int) -> bool:
        return guild_id in self.guilds

    async def announce_stream(self, sub: SubView, stream: StreamInfo) -> SendResult:
        self.streams.append((sub.guild_id, stream.id))
        return self.result

    async def announce_video(self, sub: SubView, video: VideoInfo) -> SendResult:
        self.videos.append((sub.guild_id, video.id))
        return self.result

    async def end_stream(self, sub: SubView) -> None:
        self.ended.append(sub.guild_id)


class World:
    """Was Twitch und YouTube gerade liefern."""

    def __init__(self) -> None:
        self.live: dict[str, str] = {}  # Login → Stream-ID
        self.entries: list[tuple[str, datetime]] = []  # (Video-ID, Zeit)
        self.twitch_down = False
        self.youtube_status = 200
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "id.twitch.tv":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        if request.url.host == "api.twitch.tv":
            if self.twitch_down:
                return httpx.Response(503)
            wanted = {v for k, v in request.url.params.multi_items() if k == "user_login"}
            data = [
                {
                    "id": sid, "user_login": login, "user_name": login.title(), "title": "T",
                    "game_name": "G", "viewer_count": 1, "started_at": "2026-03-01T10:00:00Z",
                    "thumbnail_url": "", "type": "live",
                }
                for login, sid in self.live.items()
                if login in wanted
            ]  # fmt: skip
            return httpx.Response(200, json={"data": data})
        if self.youtube_status != 200:
            return httpx.Response(self.youtube_status)
        entries = "".join(
            f"<entry><yt:videoId>{vid}</yt:videoId><title>Titel {vid}</title>"
            f"<published>{at.isoformat()}</published><author><name>K</name></author></entry>"
            for vid, at in self.entries
        )
        xml = (
            '<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" '
            'xmlns="http://www.w3.org/2005/Atom"><title>K</title><author><name>K</name></author>'
            f"{entries}</feed>"
        )
        return httpx.Response(200, content=xml.encode())


@pytest.fixture
def world() -> World:
    return World()


@pytest.fixture
def svc(db: Database) -> NotifyService:
    return NotifyService(db)


def make(
    svc: NotifyService, world: World, announcer: FakeAnnouncer, *, gate: bool = True
) -> Poller:
    http = httpx.AsyncClient(transport=httpx.MockTransport(world.handle))

    async def open_gate(ref: str) -> bool:
        return gate

    return Poller(
        svc, announcer, TwitchClient(http, "cid", "secret"), YouTubeClient(http),
        youtube_gate=open_gate, clock=lambda: NOW,
    )  # fmt: skip


async def add_twitch(
    svc: NotifyService, guild: int = G, ref: str = "alice", **kw: object
) -> SubView:
    return await svc.add(guild, Resolved("twitch", ref, ref.title(), **kw), channel_id=7)  # type: ignore[arg-type]


async def add_youtube(svc: NotifyService, guild: int = G, seen: tuple[str, ...] = ()) -> SubView:
    return await svc.add(guild, Resolved("youtube", CID, "K", seen_videos=seen), channel_id=7)


async def state(svc: NotifyService, guild: int = G) -> SubView:
    return (await svc.for_guild(guild))[0]


# --- Twitch ---


async def test_stream_wird_einmal_angekuendigt(svc: NotifyService, world: World) -> None:
    announcer = FakeAnnouncer()
    poller = make(svc, world, announcer)
    await add_twitch(svc)
    assert await poller.poll_twitch() == 0  # offline: nichts
    world.live["alice"] = "s1"
    assert await poller.poll_twitch() == 1
    assert await poller.poll_twitch() == 0  # läuft weiter: keine zweite Meldung
    assert await poller.poll_twitch() == 0
    assert announcer.streams == [(G, "s1")]
    after = await state(svc)
    assert (after.last_key, after.live, after.message_id) == ("s1", True, 555)


async def test_beim_hinzufuegen_laufender_stream_wird_nicht_angekuendigt(
    svc: NotifyService, world: World
) -> None:
    announcer = FakeAnnouncer()
    poller = make(svc, world, announcer)
    world.live["alice"] = "s1"
    await add_twitch(svc, live_stream_id="s1")
    assert await poller.poll_twitch() == 0
    world.live["alice"] = "s2"  # später ein neuer Stream
    assert await poller.poll_twitch() == 1
    assert announcer.streams == [(G, "s2")]


async def test_stream_ende_und_neuer_stream(svc: NotifyService, world: World) -> None:
    announcer = FakeAnnouncer()
    poller = make(svc, world, announcer)
    await add_twitch(svc)
    world.live["alice"] = "s1"
    await poller.poll_twitch()
    del world.live["alice"]
    for _ in range(OFFLINE_AFTER_MISSES - 1):
        await poller.poll_twitch()
        assert announcer.ended == []  # noch kein Ende
        assert (await state(svc)).live is True
    await poller.poll_twitch()
    assert announcer.ended == [G]
    assert (await state(svc)).live is False
    await poller.poll_twitch()
    assert announcer.ended == [G]  # nur einmal
    world.live["alice"] = "s2"
    assert await poller.poll_twitch() == 1
    assert announcer.streams == [(G, "s1"), (G, "s2")]


async def test_kurze_unterbrechung_gibt_keine_zweite_meldung(
    svc: NotifyService, world: World
) -> None:
    announcer = FakeAnnouncer()
    poller = make(svc, world, announcer)
    await add_twitch(svc)
    world.live["alice"] = "s1"
    await poller.poll_twitch()
    del world.live["alice"]
    await poller.poll_twitch()
    world.live["alice"] = "s1"
    await poller.poll_twitch()
    assert announcer.streams == [(G, "s1")]
    assert announcer.ended == []
    assert (await state(svc)).misses == 0


async def test_anbieter_ausfall_aendert_nichts(svc: NotifyService, world: World) -> None:
    announcer = FakeAnnouncer()
    poller = make(svc, world, announcer)
    await add_twitch(svc)
    world.live["alice"] = "s1"
    await poller.poll_twitch()
    world.twitch_down = True
    for _ in range(OFFLINE_AFTER_MISSES + 2):
        assert await poller.poll_twitch() == 0
    after = await state(svc)
    assert (after.live, after.misses) == (True, 0)  # "nicht erreichbar" ist nicht "offline"
    assert announcer.ended == []


async def test_senden_scheitert_vorlaeufig_dann_nochmal(svc: NotifyService, world: World) -> None:
    announcer = FakeAnnouncer(result=SendResult("retry"))
    poller = make(svc, world, announcer)
    await add_twitch(svc)
    world.live["alice"] = "s1"
    assert await poller.poll_twitch() == 0
    assert (await state(svc)).last_key is None  # nichts bleibt hängen
    announcer.result = SendResult("sent", 9)
    assert await poller.poll_twitch() == 1
    assert [s for _, s in announcer.streams] == ["s1", "s1"]  # zweiter Versuch
    assert (await state(svc)).message_id == 9


async def test_senden_unmoeglich_wird_nicht_endlos_wiederholt(
    svc: NotifyService, world: World
) -> None:
    announcer = FakeAnnouncer(result=SendResult("drop"))
    poller = make(svc, world, announcer)
    await add_twitch(svc)
    world.live["alice"] = "s1"
    await poller.poll_twitch()
    await poller.poll_twitch()
    await poller.poll_twitch()
    assert announcer.streams == [(G, "s1")]  # ein Versuch, kein Dauerfeuer


async def test_mehrere_server_ein_streamer_eine_anfrage(svc: NotifyService, world: World) -> None:
    announcer = FakeAnnouncer()
    poller = make(svc, world, announcer)
    await add_twitch(svc, G)
    await add_twitch(svc, G + 1)
    await add_twitch(svc, G, "bob")
    world.live.update({"alice": "s1", "bob": "b1"})
    assert await poller.poll_twitch() == 3
    api = [r for r in world.requests if r.url.host == "api.twitch.tv"]
    assert len(api) == 1  # alles in einer Anfrage
    assert sorted(announcer.streams) == [(G, "b1"), (G, "s1"), (G + 1, "s1")]


async def test_fremde_server_dieses_prozesses_werden_uebergangen(
    svc: NotifyService, world: World
) -> None:
    announcer = FakeAnnouncer(guilds={G})  # G+1 gehört einem anderen Prozess
    poller = make(svc, world, announcer)
    await add_twitch(svc, G)
    other = await add_twitch(svc, G + 1)
    world.live["alice"] = "s1"
    assert await poller.poll_twitch() == 1
    assert announcer.streams == [(G, "s1")]
    assert (await svc.get(G + 1, other.id)).last_key is None  # nicht angefasst


async def test_ohne_abos_keine_anfrage(svc: NotifyService, world: World) -> None:
    poller = make(svc, world, FakeAnnouncer())
    assert await poller.poll_twitch() == 0
    assert world.requests == []


async def test_ohne_twitch_zugang(svc: NotifyService, world: World) -> None:
    http = httpx.AsyncClient(transport=httpx.MockTransport(world.handle))

    async def gate(ref: str) -> bool:
        return True

    poller = Poller(
        svc, FakeAnnouncer(), TwitchClient(http, "", ""), YouTubeClient(http), youtube_gate=gate
    )
    await add_twitch(svc)
    assert await poller.poll_twitch() == 0
    assert world.requests == []


async def test_ein_fehlerhafter_eintrag_stoppt_die_uebrigen_nicht(
    svc: NotifyService, world: World
) -> None:
    class Flaky(FakeAnnouncer):
        async def announce_stream(self, sub: SubView, stream: StreamInfo) -> SendResult:
            if sub.guild_id == G:
                raise RuntimeError("kaputt")
            return await super().announce_stream(sub, stream)

    announcer = Flaky()
    poller = make(svc, world, announcer)
    await add_twitch(svc, G)
    await add_twitch(svc, G + 1)
    world.live["alice"] = "s1"
    assert await poller.poll_twitch() == 1
    assert announcer.streams == [(G + 1, "s1")]


# --- YouTube ---


def video_at(vid: str, hours_ago: float) -> tuple[str, datetime]:
    return vid, NOW - timedelta(hours=hours_ago)


async def test_neues_video_wird_einmal_angekuendigt(svc: NotifyService, world: World) -> None:
    announcer = FakeAnnouncer()
    poller = make(svc, world, announcer)
    world.entries = [video_at("aaaaaaaaaaa", 30)]
    await add_youtube(svc, seen=("aaaaaaaaaaa",))
    assert await poller.poll_youtube() == 0  # nichts Neues
    world.entries = [video_at("bbbbbbbbbbb", 1), video_at("aaaaaaaaaaa", 30)]
    assert await poller.poll_youtube() == 1
    assert await poller.poll_youtube() == 0
    assert announcer.videos == [(G, "bbbbbbbbbbb")]
    assert (await state(svc)).seen[0] == "bbbbbbbbbbb"


async def test_mehrere_neue_videos_aelteste_zuerst(svc: NotifyService, world: World) -> None:
    announcer = FakeAnnouncer()
    poller = make(svc, world, announcer)
    await add_youtube(svc)
    world.entries = [
        video_at("ccccccccccc", 1),
        video_at("aaaaaaaaaaa", 5),
        video_at("bbbbbbbbbbb", 3),
    ]
    assert await poller.poll_youtube() == 3
    assert [v for _, v in announcer.videos] == ["aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc"]


async def test_alte_videos_werden_nicht_angekuendigt_aber_gemerkt(
    svc: NotifyService, world: World
) -> None:
    announcer = FakeAnnouncer()
    poller = make(svc, world, announcer)
    await add_youtube(svc)
    world.entries = [video_at("aaaaaaaaaaa", 24 * 10)]
    assert await poller.poll_youtube() == 0
    assert await poller.poll_youtube() == 0
    assert announcer.videos == []
    assert (await state(svc)).seen == ("aaaaaaaaaaa",)


async def test_zwei_server_ein_feed_eine_anfrage(svc: NotifyService, world: World) -> None:
    announcer = FakeAnnouncer()
    poller = make(svc, world, announcer)
    await add_youtube(svc, G)
    await add_youtube(svc, G + 1)
    world.entries = [video_at("aaaaaaaaaaa", 1)]
    assert await poller.poll_youtube() == 2
    feeds = [r for r in world.requests if r.url.host == "www.youtube.com"]
    assert len(feeds) == 1
    assert sorted(announcer.videos) == [(G, "aaaaaaaaaaa"), (G + 1, "aaaaaaaaaaa")]


async def test_tor_begrenzt_die_abfragen(svc: NotifyService, world: World) -> None:
    poller = make(svc, world, FakeAnnouncer(), gate=False)
    await add_youtube(svc)
    world.entries = [video_at("aaaaaaaaaaa", 1)]
    assert await poller.poll_youtube() == 0
    assert world.requests == []  # zu früh: gar keine Anfrage


@pytest.mark.parametrize("status", [404, 429, 500])
async def test_feed_fehler_aendern_nichts(svc: NotifyService, world: World, status: int) -> None:
    announcer = FakeAnnouncer()
    poller = make(svc, world, announcer)
    await add_youtube(svc, seen=("aaaaaaaaaaa",))
    world.youtube_status = status
    assert await poller.poll_youtube() == 0
    assert (await state(svc)).seen == ("aaaaaaaaaaa",)
    world.youtube_status = 200
    world.entries = [video_at("bbbbbbbbbbb", 1)]
    assert await poller.poll_youtube() == 1  # erholt sich von selbst


async def test_video_senden_scheitert_vorlaeufig(svc: NotifyService, world: World) -> None:
    announcer = FakeAnnouncer(result=SendResult("retry"))
    poller = make(svc, world, announcer)
    await add_youtube(svc)
    world.entries = [video_at("aaaaaaaaaaa", 1)]
    assert await poller.poll_youtube() == 0
    assert (await state(svc)).seen == ()  # zurückgedreht
    announcer.result = SendResult("sent", 1)
    assert await poller.poll_youtube() == 1


async def test_gesamtrunde(svc: NotifyService, world: World) -> None:
    announcer = FakeAnnouncer()
    poller = make(svc, world, announcer)
    await add_twitch(svc)
    await add_youtube(svc)
    world.live["alice"] = "s1"
    world.entries = [video_at("aaaaaaaaaaa", 1)]
    assert await poller.poll() == 2
