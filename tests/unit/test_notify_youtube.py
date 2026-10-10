import httpx
import pytest

from ggbot.core.notify.types import ProviderError
from ggbot.core.notify.youtube import YouTubeClient, parse_channel_input, parse_feed

CID = "UC" + "a" * 22


def feed(*entries: str, name: str = "Mein Kanal") -> bytes:
    body = "".join(entries)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" '
        'xmlns:media="http://search.yahoo.com/mrss/" xmlns="http://www.w3.org/2005/Atom">'
        f"<title>{name}</title><author><name>{name}</name></author>{body}</feed>"
    ).encode()


def entry(
    video_id: str = "abcdefghijk",
    title: str = "Titel",
    published: str = "2026-03-01T10:00:00+00:00",
) -> str:
    return (
        f"<entry><yt:videoId>{video_id}</yt:videoId><title>{title}</title>"
        f"<published>{published}</published>"
        f"<author><name>Mein Kanal</name></author>"
        f'<media:group><media:thumbnail url="https://i.ytimg.com/vi/{video_id}/hq.jpg"/></media:group></entry>'
    )


# --- Eingabe ---


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (CID, ("id", CID)),
        (f"  {CID}  ", ("id", CID)),
        ("@meinkanal", ("handle", "@meinkanal")),
        (f"https://www.youtube.com/channel/{CID}", ("id", CID)),
        (f"youtube.com/channel/{CID}/", ("id", CID)),
        (f"https://m.youtube.com/channel/{CID}?si=x", ("id", CID)),
        ("https://www.youtube.com/@meinkanal", ("handle", "@meinkanal")),
        ("https://youtube.com/@mein.kanal/videos"[: -len("/videos")], ("handle", "@mein.kanal")),
    ],
)
def test_eingabe_erkannt(text: str, expected: tuple[str, str]) -> None:
    assert parse_channel_input(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "mein kanal",
        "UC123",
        "https://evil.example/channel/" + CID,
        "https://youtube.com.evil.example/@x" + "y" * 3,
        "http://169.254.169.254/latest",
        "file:///etc/passwd",
        "https://www.youtube.com/watch?v=abcdefghijk",
        "https://www.youtube.com/@../etc",
        "@a",
    ],
)
def test_eingabe_abgelehnt(text: str) -> None:
    with pytest.raises(ProviderError) as exc:
        parse_channel_input(text)
    assert exc.value.key == "not_found"


# --- Feed ---


def test_feed_lesen() -> None:
    name, videos = parse_feed(feed(entry("aaaaaaaaaaa", "Eins"), entry("bbbbbbbbbbb", "Zwei")), CID)
    assert name == "Mein Kanal"
    assert [v.id for v in videos] == ["aaaaaaaaaaa", "bbbbbbbbbbb"]
    assert videos[0].title == "Eins"
    assert videos[0].url == "https://www.youtube.com/watch?v=aaaaaaaaaaa"
    assert videos[0].thumbnail.startswith("https://i.ytimg.com/")
    assert videos[0].channel_id == CID
    assert videos[0].published.tzinfo is not None


def test_kaputte_eintraege_werden_uebersprungen() -> None:
    xml = feed(
        entry("aaaaaaaaaaa"),
        entry("kurz"),  # keine gültige Video-ID
        entry("bbbbbbbbbbb", published="gestern"),  # kein Datum
        "<entry><title>ohne id</title></entry>",
        entry("ccccccccccc"),
    )
    _, videos = parse_feed(xml, CID)
    assert [v.id for v in videos] == ["aaaaaaaaaaa", "ccccccccccc"]


def test_leerer_feed() -> None:
    assert parse_feed(feed(), CID) == ("Mein Kanal", [])


@pytest.mark.parametrize(
    "xml",
    [
        b"",
        b"kein xml",
        b"<feed><entry></feed>",
        b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">'
        b'<!ENTITY b "&a;&a;">]><feed>&b;</feed>',
        b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><feed>&e;</feed>',
        b"x" * 1_000_001,
    ],
)
def test_unbrauchbarer_feed(xml: bytes) -> None:
    with pytest.raises(ProviderError) as exc:
        parse_feed(xml, CID)
    assert exc.value.key == "unavailable"


# --- Client ---


def client(handler: httpx.MockTransport) -> YouTubeClient:
    return YouTubeClient(httpx.AsyncClient(transport=handler))


async def test_resolve_mit_kanal_id() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            content=feed(
                entry("aaaaaaaaaaa", published="2026-03-01T10:00:00+00:00"),
                entry("bbbbbbbbbbb", published="2026-03-01T11:00:00+00:00"),
            ),
        )

    resolved = await client(httpx.MockTransport(handle)).resolve(CID)
    assert (resolved.provider, resolved.ref, resolved.display_name) == (
        "youtube",
        CID,
        "Mein Kanal",
    )
    assert resolved.seen_videos == ("bbbbbbbbbbb", "aaaaaaaaaaa")  # bekannt, neueste zuerst
    assert seen[0].url.host == "www.youtube.com"
    assert seen[0].url.params["channel_id"] == CID


async def test_resolve_mit_handle() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/@meinkanal":
            return httpx.Response(200, text=f'<html>...{{"channelId":"{CID}"}}...</html>')
        return httpx.Response(200, content=feed(entry()))

    resolved = await client(httpx.MockTransport(handle)).resolve("@meinkanal")
    assert resolved.ref == CID


async def test_handle_ohne_kanal_id_auf_der_seite() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(200, text="<html>nichts</html>"))
    with pytest.raises(ProviderError) as exc:
        await client(transport).resolve("@meinkanal")
    assert exc.value.key == "not_found"


@pytest.mark.parametrize(
    ("status", "key"),
    [(404, "not_found"), (500, "unavailable"), (429, "unavailable"), (403, "unavailable")],
)
async def test_http_fehler(status: int, key: str) -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(status))
    with pytest.raises(ProviderError) as exc:
        await client(transport).videos(CID)
    assert exc.value.key == key


async def test_netzwerkfehler_ist_nicht_verfuegbar() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("zu langsam")

    with pytest.raises(ProviderError) as exc:
        await client(httpx.MockTransport(boom)).videos(CID)
    assert exc.value.key == "unavailable"


async def test_fragt_nur_youtube_ab() -> None:
    hosts: set[str] = set()

    def handle(request: httpx.Request) -> httpx.Response:
        hosts.add(request.url.host)
        return httpx.Response(200, content=feed(entry()))

    for text in (CID, f"https://www.youtube.com/channel/{CID}"):
        await client(httpx.MockTransport(handle)).resolve(text)
    assert hosts == {"www.youtube.com"}
