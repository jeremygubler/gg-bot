"""Gemeinsame Datentypen der Benachrichtigungen. Reine Daten, kein Netzwerk."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Provider = Literal["twitch", "youtube"]


class ProviderError(Exception):
    """Ein Anbieter ist nicht erreichbar oder antwortet unbrauchbar. `key` ist ein i18n-Schlüssel
    ohne Präfix: "not_found" (gibt es nicht), "unavailable" (später nochmal), "not_configured"."""

    def __init__(self, key: str, detail: str = "") -> None:
        super().__init__(f"{key}: {detail}" if detail else key)
        self.key = key


@dataclass(frozen=True)
class StreamInfo:
    id: str
    login: str
    name: str
    title: str
    game: str
    viewers: int
    started_at: datetime
    thumbnail: str


@dataclass(frozen=True)
class VideoInfo:
    id: str
    channel_id: str
    channel_name: str
    title: str
    published: datetime
    thumbnail: str

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.id}"


@dataclass(frozen=True)
class Resolved:
    """Ergebnis beim Hinzufügen: Was gibt es, und was gilt schon als bekannt?

    Das Bekannte wird nicht angekündigt: wer einen Streamer hinzufügt, während er live ist, oder
    einen Kanal mit alten Videos, soll nicht sofort Meldungen bekommen.
    """

    provider: Provider
    ref: str  # Twitch: Login in Kleinbuchstaben; YouTube: Kanal-ID (UC…)
    display_name: str
    live_stream_id: str | None = None
    seen_videos: tuple[str, ...] = ()
