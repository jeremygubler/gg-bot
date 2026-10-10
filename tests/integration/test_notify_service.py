import asyncio

import pytest

from ggbot.core.db.engine import Database
from ggbot.core.errors import UserError
from ggbot.core.notify.types import Resolved
from ggbot.core.services.notify import MAX_SUBSCRIPTIONS, NotifyService

G = 100


@pytest.fixture
def svc(db: Database) -> NotifyService:
    return NotifyService(db)


def twitch(ref: str = "alice", live: str | None = None) -> Resolved:
    return Resolved("twitch", ref, ref.title(), live_stream_id=live)


def youtube(ref: str = "UC" + "a" * 22, seen: tuple[str, ...] = ()) -> Resolved:
    return Resolved("youtube", ref, "Kanal", seen_videos=seen)


async def test_hinzufuegen_und_lesen(svc: NotifyService) -> None:
    sub = await svc.add(G, twitch(), channel_id=7, message="Hallo {streamer}", role_id=9)
    assert (sub.provider, sub.ref, sub.display_name, sub.channel_id) == (
        "twitch",
        "alice",
        "Alice",
        7,
    )
    assert (sub.message, sub.role_id, sub.live, sub.last_key, sub.seen) == (
        "Hallo {streamer}",
        9,
        False,
        None,
        (),
    )
    assert await svc.get(G, sub.id) == sub
    assert [s.id for s in await svc.for_guild(G)] == [sub.id]
    assert await svc.for_guild(G + 1) == []


async def test_bekanntes_gilt_als_gesehen(svc: NotifyService) -> None:
    live = await svc.add(G, twitch("bob", live="stream-9"), channel_id=7)
    assert (live.live, live.last_key) == (True, "stream-9")
    yt = await svc.add(G, youtube(seen=("v1", "v2")), channel_id=7)
    assert yt.seen == ("v1", "v2")


async def test_doppelte_und_grenze(svc: NotifyService) -> None:
    await svc.add(G, twitch(), channel_id=7)
    with pytest.raises(UserError) as exc:
        await svc.add(G, twitch(), channel_id=8)
    assert exc.value.key == "error.notify_duplicate"
    await svc.add(G + 1, twitch(), channel_id=8)  # anderer Server darf denselben Streamer
    await svc.add(
        G, youtube(), channel_id=7
    )  # gleiche Referenz bei anderem Anbieter ist etwas anderes
    for i in range(MAX_SUBSCRIPTIONS - 2):
        await svc.add(G, twitch(f"user_{i:03d}"), channel_id=7)
    with pytest.raises(UserError) as exc:
        await svc.add(G, twitch("noch_einer"), channel_id=7)
    assert exc.value.key == "error.notify_limit"


@pytest.mark.parametrize(
    ("provider", "message", "key"),
    [
        ("twitch", "{level}", "error.notify_message_invalid"),
        ("twitch", "{channel}", "error.notify_message_invalid"),  # YouTube-Feld bei Twitch
        ("youtube", "{streamer}", "error.notify_message_invalid"),
        ("youtube", "{user.__class__}", "error.notify_message_invalid"),
        ("twitch", "{title!r}", "error.notify_message_invalid"),
        ("twitch", "x" * 301, "error.notify_message_long"),
    ],
)
async def test_vorlage_pro_anbieter_geprueft(
    svc: NotifyService, provider: str, message: str, key: str
) -> None:
    resolved = twitch() if provider == "twitch" else youtube()
    with pytest.raises(UserError) as exc:
        await svc.add(G, resolved, channel_id=7, message=message)
    assert exc.value.key == key
    assert await svc.for_guild(G) == []


async def test_gueltige_vorlagen(svc: NotifyService) -> None:
    await svc.add(G, twitch(), channel_id=7, message="{streamer} {title} {game} {url} {viewers}")
    await svc.add(G, youtube(), channel_id=7, message="{channel} {title} {url}")


async def test_entfernen(svc: NotifyService) -> None:
    await svc.add(G, twitch(), channel_id=7)
    other = await svc.add(G + 1, twitch(), channel_id=7)
    with pytest.raises(UserError) as exc:
        await svc.remove(G, "youtube", "alice")
    assert exc.value.key == "error.notify_unknown"
    removed = await svc.remove(G, "twitch", "alice")
    assert removed.ref == "alice"
    assert await svc.for_guild(G) == []
    assert [s.id for s in await svc.for_guild(G + 1)] == [other.id]  # der andere Server bleibt
    with pytest.raises(UserError):
        await svc.remove(G, "twitch", "alice")


async def test_fremde_abos_sind_nicht_lesbar(svc: NotifyService) -> None:
    other = await svc.add(G + 1, twitch(), channel_id=7)
    with pytest.raises(UserError) as exc:
        await svc.get(G, other.id)
    assert exc.value.key == "error.notify_unknown"


async def test_alle_eines_anbieters_ueber_server_hinweg(svc: NotifyService) -> None:
    await svc.add(G, twitch(), channel_id=7)
    await svc.add(G + 1, twitch(), channel_id=8)
    await svc.add(G, youtube(), channel_id=7)
    assert sorted(s.guild_id for s in await svc.all_for_provider("twitch")) == [G, G + 1]
    assert len(await svc.all_for_provider("youtube")) == 1


# --- Beanspruchen ---


async def test_stream_beanspruchen_gewinnt_einer(svc: NotifyService) -> None:
    sub = await svc.add(G, twitch(), channel_id=7)
    results = await asyncio.gather(*(svc.claim_stream(sub, "s1") for _ in range(8)))
    assert sum(results) == 1
    after = (await svc.for_guild(G))[0]
    assert (after.last_key, after.live, after.misses) == ("s1", True, 0)
    assert await svc.claim_stream(sub, "s1") is False  # veralteter Blick


async def test_stream_zuruecknehmen(svc: NotifyService) -> None:
    sub = await svc.add(G, twitch(), channel_id=7)
    await svc.claim_stream(sub, "s1")
    await svc.revert_stream(sub, "s1")
    after = (await svc.for_guild(G))[0]
    assert (after.last_key, after.live) == (None, False)
    assert await svc.claim_stream(sub, "s1") is True  # beim nächsten Mal wieder möglich


async def test_zuruecknehmen_trifft_nur_den_eigenen_stand(svc: NotifyService) -> None:
    sub = await svc.add(G, twitch(), channel_id=7)
    await svc.claim_stream(sub, "s1")
    newer = (await svc.for_guild(G))[0]
    await svc.claim_stream(newer, "s2")  # inzwischen ein neuerer Stream
    await svc.revert_stream(sub, "s1")  # darf s2 nicht überschreiben
    assert (await svc.for_guild(G))[0].last_key == "s2"


async def test_video_stand_beanspruchen(svc: NotifyService) -> None:
    sub = await svc.add(G, youtube(seen=("a",)), channel_id=7)
    results = await asyncio.gather(*(svc.claim_videos(sub, ("b", "a")) for _ in range(6)))
    assert sum(results) == 1
    assert (await svc.for_guild(G))[0].seen == ("b", "a")
    assert await svc.claim_videos(sub, ("c", "a")) is False  # sub zeigt noch den alten Stand
    await svc.revert_videos(sub, ("b", "a"))
    assert (await svc.for_guild(G))[0].seen == ("a",)


async def test_zustand_speichern_und_nachricht(svc: NotifyService) -> None:
    from ggbot.core.notify.rules import StreamState

    sub = await svc.add(G, twitch("bob", live="s1"), channel_id=7)
    await svc.save_stream_state(sub, StreamState("s1", True, 2))
    await svc.set_message(sub.id, 555)
    after = (await svc.for_guild(G))[0]
    assert (after.misses, after.live, after.message_id) == (2, True, 555)
