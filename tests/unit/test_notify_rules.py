from datetime import UTC, datetime, timedelta

import pytest

from ggbot.core.notify.rules import (
    MAX_SEEN,
    MAX_VIDEO_AGE,
    MAX_VIDEOS_PER_POLL,
    OFFLINE_AFTER_MISSES,
    TWITCH_LOGIN,
    YOUTUBE_CHANNEL_ID,
    YOUTUBE_HANDLE,
    StreamState,
    new_videos,
    plan_stream,
)
from ggbot.core.notify.types import StreamInfo, VideoInfo

NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def stream(stream_id: str = "s1") -> StreamInfo:
    return StreamInfo(stream_id, "alice", "Alice", "Titel", "Spiel", 10, NOW, "")


def video(video_id: str, age_hours: float = 1) -> VideoInfo:
    return VideoInfo(
        video_id, "UC" + "a" * 22, "Kanal", f"t{video_id}", NOW - timedelta(hours=age_hours), ""
    )


OFF = StreamState(None, False, 0)


# --- Twitch ---


def test_neuer_stream_wird_angekuendigt() -> None:
    plan = plan_stream(OFF, stream("s1"))
    assert plan.announce is not None
    assert plan.announce.id == "s1"
    assert plan.state == StreamState("s1", True, 0)
    assert plan.changed


def test_laufender_stream_wird_nicht_erneut_angekuendigt() -> None:
    plan = plan_stream(StreamState("s1", True, 0), stream("s1"))
    assert plan.announce is None
    assert not plan.changed
    assert plan.state == StreamState("s1", True, 0)


def test_neuer_stream_nach_dem_alten_wird_angekuendigt() -> None:
    plan = plan_stream(StreamState("s1", False, 0), stream("s2"))
    assert plan.announce is not None
    assert plan.state.last_key == "s2"


def test_kurze_unterbrechung_ist_kein_neuer_stream() -> None:
    state = StreamState("s1", True, 0)
    for _ in range(OFFLINE_AFTER_MISSES - 1):
        plan = plan_stream(state, None)
        assert not plan.ended
        state = plan.state
    assert state.live is True
    back = plan_stream(state, stream("s1"))  # kommt mit derselben ID zurück
    assert back.announce is None
    assert back.state.misses == 0  # Zähler beginnt neu


def test_stream_endet_erst_nach_mehreren_abfragen() -> None:
    state = StreamState("s1", True, 0)
    plans = []
    for _ in range(OFFLINE_AFTER_MISSES):
        plans.append(plan_stream(state, None))
        state = plans[-1].state
    assert [p.ended for p in plans] == [False] * (OFFLINE_AFTER_MISSES - 1) + [True]
    assert state == StreamState(
        "s1", False, 0
    )  # die ID bleibt: derselbe Stream kommt nicht nochmal


def test_offline_bleibt_offline() -> None:
    plan = plan_stream(StreamState("s1", False, 0), None)
    assert not plan.changed
    assert plan.state == StreamState("s1", False, 0)


def test_nie_live_gewesen() -> None:
    assert plan_stream(OFF, None).state == OFF


# --- YouTube ---


def test_neue_videos_aelteste_zuerst() -> None:
    entries = [video("c", 1), video("a", 5), video("b", 3)]
    announce, seen = new_videos(entries, (), NOW)
    assert [v.id for v in announce] == ["a", "b", "c"]
    assert set(seen) == {"a", "b", "c"}


def test_bekannte_videos_werden_uebersprungen() -> None:
    announce, seen = new_videos([video("a"), video("b")], ("a",), NOW)
    assert [v.id for v in announce] == ["b"]
    assert seen[0] == "b"  # Neues steht vorn
    assert "a" in seen


def test_nichts_neues_aendert_nichts() -> None:
    announce, seen = new_videos([video("a")], ("a", "x"), NOW)
    assert announce == []
    assert seen == ("a", "x")


def test_alte_videos_gelten_als_bekannt_ohne_meldung() -> None:
    old = video("old", MAX_VIDEO_AGE.total_seconds() / 3600 + 1)
    announce, seen = new_videos([old, video("fresh")], (), NOW)
    assert [v.id for v in announce] == ["fresh"]
    assert "old" in seen  # sonst käme es bei der nächsten Abfrage wieder
    announce2, _ = new_videos([old, video("fresh")], seen, NOW)
    assert announce2 == []


def test_alter_grenze_ist_inklusive() -> None:
    edge = VideoInfo("e", "c", "k", "t", NOW - MAX_VIDEO_AGE, "")
    assert [v.id for v in new_videos([edge], (), NOW)[0]] == ["e"]


def test_obergrenze_pro_abfrage_behaelt_die_neuesten() -> None:
    entries = [video(f"v{i}", age_hours=10 - i) for i in range(MAX_VIDEOS_PER_POLL + 3)]
    announce, seen = new_videos(entries, (), NOW)
    assert len(announce) == MAX_VIDEOS_PER_POLL
    assert [v.id for v in announce] == [f"v{i}" for i in range(3, 6)]  # die jüngsten drei
    assert {e.id for e in entries} <= set(seen)  # auch die übergangenen sind bekannt


def test_gedaechtnis_ist_begrenzt_und_neues_bleibt() -> None:
    old = tuple(f"o{i}" for i in range(MAX_SEEN))
    announce, seen = new_videos([video("neu")], old, NOW)
    assert len(seen) == MAX_SEEN
    assert seen[0] == "neu"
    assert [v.id for v in announce] == ["neu"]
    assert f"o{MAX_SEEN - 1}" not in seen


def test_leerer_feed() -> None:
    assert new_videos([], ("a",), NOW) == ([], ("a",))


# --- Eingabeformate ---


@pytest.mark.parametrize("text", ["abc", "a_b_c", "user_123", "x" * 25])
def test_twitch_login_gueltig(text: str) -> None:
    assert TWITCH_LOGIN.match(text)


@pytest.mark.parametrize("text", ["ab", "x" * 26, "a b", "A_B", "a-b", "a/b", "", "a\n", "ä_ö_ü"])
def test_twitch_login_ungueltig(text: str) -> None:
    assert not TWITCH_LOGIN.match(text)


def test_youtube_kanal_id_und_handle() -> None:
    assert YOUTUBE_CHANNEL_ID.match("UC" + "a" * 22)
    assert not YOUTUBE_CHANNEL_ID.match("UC" + "a" * 21)
    assert not YOUTUBE_CHANNEL_ID.match("XX" + "a" * 22)
    assert not YOUTUBE_CHANNEL_ID.match("UC" + "a" * 22 + "\n")
    assert YOUTUBE_HANDLE.match("@mein.kanal-1_x")
    assert not YOUTUBE_HANDLE.match("mein")
    assert not YOUTUBE_HANDLE.match("@a/b")
    assert not YOUTUBE_HANDLE.match("@ab")
