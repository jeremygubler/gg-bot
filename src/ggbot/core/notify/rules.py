"""Was bei einer Abfrage zu tun ist. Reine Entscheidungen über Zustand und Antwort."""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from ggbot.core.notify.types import StreamInfo, VideoInfo

# Wie oft eine Abfrage hintereinander "offline" liefern muss, bevor der Stream als beendet gilt.
# Twitch lässt Streams bei Verbindungsabbrüchen kurz verschwinden; ohne Puffer gäbe es Meldungen
# in Serie und die Beendet-Anzeige flackerte.
OFFLINE_AFTER_MISSES = 3
MAX_SEEN = 50
# Ein Video, das älter ist, kommt nur durch Wiederveröffentlichen oder Löschen und Rückgängig in
# den Feed und ist keine Neuigkeit.
MAX_VIDEO_AGE = timedelta(hours=48)
# Nach einer langen Pause (Bot aus) nicht mit Meldungen überschütten.
MAX_VIDEOS_PER_POLL = 3

TWITCH_LOGIN = re.compile(r"\A[a-z0-9_]{3,25}\Z")
YOUTUBE_CHANNEL_ID = re.compile(r"\AUC[A-Za-z0-9_-]{22}\Z")
YOUTUBE_HANDLE = re.compile(r"\A@[A-Za-z0-9._-]{3,30}\Z")


@dataclass(frozen=True)
class StreamState:
    last_key: str | None  # ID des zuletzt angekündigten Streams
    live: bool
    misses: int


@dataclass(frozen=True)
class StreamPlan:
    state: StreamState
    announce: StreamInfo | None = None
    ended: bool = False  # jetzt als beendet markieren (Nachricht anpassen)

    @property
    def changed(self) -> bool:
        return self.announce is not None or self.ended


def plan_stream(state: StreamState, stream: StreamInfo | None) -> StreamPlan:
    """Eine Twitch-Abfrage für einen Streamer: `stream` ist None, wenn er offline ist."""
    if stream is not None:
        new = StreamState(stream.id, True, 0)
        # Gleiche Stream-ID = derselbe Stream (auch nach einem kurzen Abbruch): keine neue Meldung.
        return StreamPlan(new, announce=stream if stream.id != state.last_key else None)
    if not state.live:
        return StreamPlan(StreamState(state.last_key, False, 0))
    misses = state.misses + 1
    if misses >= OFFLINE_AFTER_MISSES:
        return StreamPlan(StreamState(state.last_key, False, 0), ended=True)
    return StreamPlan(StreamState(state.last_key, True, misses))


def new_videos(
    entries: list[VideoInfo], seen: tuple[str, ...], now: datetime
) -> tuple[list[VideoInfo], tuple[str, ...]]:
    """Neue Videos (älteste zuerst, höchstens 3) und der neue Stand der bekannten IDs.

    Alles Neue gilt danach als bekannt, auch was wegen Alter oder Obergrenze nicht angekündigt wird:
    sonst käme es bei der nächsten Abfrage wieder.
    """
    known = set(seen)
    fresh = [v for v in entries if v.id not in known]
    ordered = sorted(fresh, key=lambda v: v.published)
    announce = [v for v in ordered if now - v.published <= MAX_VIDEO_AGE]
    announce = announce[-MAX_VIDEOS_PER_POLL:]
    # Neueste zuerst im Gedächtnis; alte fallen hinten heraus.
    updated = tuple(dict.fromkeys([*(v.id for v in reversed(ordered)), *seen]))[:MAX_SEEN]
    return announce, updated
