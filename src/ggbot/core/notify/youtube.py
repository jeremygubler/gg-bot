"""YouTube über den öffentlichen Atom-Feed eines Kanals. Kein Schlüssel, keine Quote."""

import re
from datetime import datetime

import httpx
from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from ggbot.core.notify.rules import YOUTUBE_CHANNEL_ID, YOUTUBE_HANDLE
from ggbot.core.notify.types import ProviderError, Resolved, VideoInfo

FEED_URL = "https://www.youtube.com/feeds/videos.xml"
MAX_BYTES = 1_000_000
ATOM = "{http://www.w3.org/2005/Atom}"
YT = "{http://www.youtube.com/xml/schemas/2015}"
MEDIA = "{http://search.yahoo.com/mrss/}"
_CHANNEL_URL = re.compile(
    r"\A(?:https?://)?(?:www\.|m\.)?youtube\.com/(?P<path>channel/UC[A-Za-z0-9_-]{22}|@[A-Za-z0-9._-]{3,30})/?(?:[?#].*)?\Z"
)
_PAGE_CHANNEL_ID = re.compile(r'"(?:channelId|externalId)":"(UC[A-Za-z0-9_-]{22})"')


def parse_channel_input(text: str) -> tuple[str, str]:
    """("id", "UC…") oder ("handle", "@name") aus einer Eingabe; sonst `ProviderError`.

    Erlaubt sind eine Kanal-ID, ein @Handle und die youtube.com-Adressen dazu. Alles andere wird
    abgelehnt: nur so bleibt der Server, den der Bot anfragt, immer youtube.com.
    """
    text = text.strip()
    if YOUTUBE_CHANNEL_ID.match(text):
        return "id", text
    if YOUTUBE_HANDLE.match(text):
        return "handle", text
    match = _CHANNEL_URL.match(text)
    if match:
        path = match["path"]
        return (
            ("id", path.removeprefix("channel/"))
            if path.startswith("channel/")
            else ("handle", path)
        )
    raise ProviderError("not_found", "unbekanntes Format")


def parse_feed(xml: bytes, channel_id: str) -> tuple[str, list[VideoInfo]]:
    """(Kanalname, Videos) aus einem Feed. Wirft `ProviderError`, wenn er unbrauchbar ist."""
    if len(xml) > MAX_BYTES:
        raise ProviderError("unavailable", "Feed zu gross")
    try:
        # defusedxml lehnt Entity-Tricks (Billion Laughs u. ä.) und externe Entitäten ab.
        root = ElementTree.fromstring(xml)
    except (ElementTree.ParseError, DefusedXmlException) as exc:
        raise ProviderError("unavailable", "Feed nicht lesbar") from exc
    name = root.findtext(f"{ATOM}author/{ATOM}name") or root.findtext(f"{ATOM}title") or channel_id
    videos: list[VideoInfo] = []
    for entry in root.findall(f"{ATOM}entry"):
        video_id = entry.findtext(f"{YT}videoId")
        published = entry.findtext(f"{ATOM}published")
        title = entry.findtext(f"{ATOM}title")
        if (
            not video_id
            or not published
            or not title
            or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id)
        ):
            continue  # ein kaputter Eintrag soll die übrigen nicht verhindern
        try:
            when = datetime.fromisoformat(published)
        except ValueError:
            continue
        thumb = entry.find(f"{MEDIA}group/{MEDIA}thumbnail")
        videos.append(
            VideoInfo(
                id=video_id,
                channel_id=channel_id,
                channel_name=entry.findtext(f"{ATOM}author/{ATOM}name") or name,
                title=title,
                published=when,
                thumbnail=(thumb.get("url") or "") if thumb is not None else "",
            )
        )
    return name, videos


class YouTubeClient:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def _get(self, url: str, params: dict[str, str] | None = None) -> httpx.Response:
        try:
            response = await self._http.get(url, params=params, follow_redirects=True)
        except httpx.HTTPError as exc:
            raise ProviderError("unavailable", type(exc).__name__) from exc
        return response

    async def videos(self, channel_id: str) -> tuple[str, list[VideoInfo]]:
        response = await self._get(FEED_URL, {"channel_id": channel_id})
        if response.status_code == 404:
            raise ProviderError("not_found", channel_id)
        if response.status_code != 200:
            raise ProviderError("unavailable", f"HTTP {response.status_code}")
        return parse_feed(response.content, channel_id)

    async def _channel_id_from_handle(self, handle: str) -> str:
        # Der Handle ist durch YOUTUBE_HANDLE eingeschränkt: der Pfad bleibt auf youtube.com.
        response = await self._get(f"https://www.youtube.com/{handle}")
        if response.status_code == 404:
            raise ProviderError("not_found", handle)
        if response.status_code != 200:
            raise ProviderError("unavailable", f"HTTP {response.status_code}")
        match = _PAGE_CHANNEL_ID.search(response.text[:2_000_000])
        if match is None:
            raise ProviderError("not_found", handle)
        return match.group(1)

    async def resolve(self, text: str) -> Resolved:
        kind, value = parse_channel_input(text)
        channel_id = value if kind == "id" else await self._channel_id_from_handle(value)
        name, videos = await self.videos(channel_id)
        return Resolved(
            provider="youtube",
            ref=channel_id,
            display_name=name,
            seen_videos=tuple(
                v.id for v in sorted(videos, key=lambda v: v.published, reverse=True)
            ),
        )
