"""Ankündigungen auf Discord und die /notify-Befehle, mit Fake-Discord-Objekten."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import httpx
import pytest
from redis.asyncio import Redis

from ggbot.bot.client import GGBot
from ggbot.bot.cogs.notify import NotifyCog
from ggbot.bot.notifier import DiscordAnnouncer, stream_embed, stream_text, video_embed, video_text
from ggbot.bot.services import Services
from ggbot.core.config import AppConfig
from ggbot.core.db.engine import Database
from ggbot.core.errors import UserError
from ggbot.core.notify.types import Resolved, StreamInfo, VideoInfo
from tests.bot.test_cog_flows import sent_text
from tests.bot.test_panels import role
from tests.bot.ticket_fakes import GUILD_ID, click, make_world, member, text_channel
from tests.fakes import invoke

CH = 70
CID = "UC" + "a" * 22
NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def stream(**kw: Any) -> StreamInfo:
    data: dict[str, Any] = (
        {"id": "s1", "login": "alice", "name": "Alice", "title": "Hallo @everyone", "game": "Spiel",
         "viewers": 7, "started_at": NOW, "thumbnail": "https://x/y.jpg"} | kw
    )  # fmt: skip
    return StreamInfo(**data)


def video(**kw: Any) -> VideoInfo:
    data: dict[str, Any] = (
        {"id": "abcdefghijk", "channel_id": CID, "channel_name": "Kanal", "title": "Neu <b>",
         "published": NOW, "thumbnail": "https://i.ytimg.com/x.jpg"} | kw
    )  # fmt: skip
    return VideoInfo(**data)


def handler(request: httpx.Request) -> httpx.Response:
    if request.url.host == "id.twitch.tv":
        return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
    if request.url.path.endswith("/users"):
        login = request.url.params["login"]
        if login == "niemand":
            return httpx.Response(200, json={"data": []})
        return httpx.Response(200, json={"data": [{"login": login, "display_name": login.title()}]})
    if request.url.host == "api.twitch.tv":
        return httpx.Response(200, json={"data": []})
    return httpx.Response(
        200,
        content=(
            b'<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns="http://www.w3.org/2005/Atom">'
            b"<title>Mein Kanal</title><author><name>Mein Kanal</name></author></feed>"
        ),
    )


@pytest.fixture
async def bot(db: Database, redis: Redis) -> GGBot:
    config = AppConfig(twitch_client_id="cid", twitch_client_secret="secret")
    instance = GGBot(Services.build(config, db, redis))
    instance.services.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    instance.services.twitch._http = instance.services.http
    instance.services.youtube._http = instance.services.http
    return instance


def setup(bot: GGBot) -> tuple[MagicMock, MagicMock, NotifyCog]:
    guild = make_world(bot, role(20, position=2))
    channel = text_channel(CH)
    guild.channels_by_id[CH] = channel
    return guild, channel, NotifyCog(bot)


def admin(guild: MagicMock) -> MagicMock:
    return member(guild, 11, manager=True)


async def add_twitch(bot: GGBot, guild_id: int = GUILD_ID, **kw: Any) -> Any:
    return await bot.services.notify.add(
        guild_id, Resolved("twitch", "alice", "Alice"), channel_id=CH, **kw
    )


# --- Nachrichten ---


def test_twitch_text_und_standardvorlage(bot: GGBot) -> None:
    sub: Any = SimpleNamespace(message=None, role_id=None)
    text = stream_text(sub, stream(), "de")
    assert "Alice" in text
    assert "https://www.twitch.tv/alice" in text
    custom: Any = SimpleNamespace(
        message="{streamer} spielt {game} ({viewers}) {url}", role_id=None
    )
    assert (
        stream_text(custom, stream(), "en") == "Alice spielt Spiel (7) https://www.twitch.tv/alice"
    )
    no_game = stream_text(custom, stream(game=""), "en")
    assert "spielt —" in no_game


def test_youtube_text(bot: GGBot) -> None:
    sub: Any = SimpleNamespace(message=None, role_id=None)
    text = video_text(sub, video(), "en")
    assert "Kanal" in text
    assert "https://www.youtube.com/watch?v=abcdefghijk" in text


def test_embeds() -> None:
    embed = stream_embed(stream(), "de")
    assert embed.url == "https://www.twitch.tv/alice"
    assert embed.title == "Hallo @everyone"
    assert [f.name for f in embed.fields] == ["Spiel", "Zuschauer"]
    assert embed.image.url is not None
    assert embed.image.url.startswith("https://x/y.jpg?t=")  # Zusatz gegen das Bild-Caching
    assert stream_embed(stream(thumbnail="javascript:x"), "de").image.url is None
    assert stream_embed(stream(game=""), "de").fields[0].name == "Zuschauer"
    v = video_embed(video())
    assert v.url == "https://www.youtube.com/watch?v=abcdefghijk"
    assert v.image.url == "https://i.ytimg.com/x.jpg"
    assert video_embed(video(thumbnail="")).image.url is None
    assert len(stream_embed(stream(title="x" * 500), "de").title or "") <= 256


async def test_senden_pingt_hoechstens_die_rolle(bot: GGBot) -> None:
    _guild, channel, _ = setup(bot)
    sub = await add_twitch(bot, role_id=20)
    result = await DiscordAnnouncer(bot).announce_stream(sub, stream())
    assert (result.kind, result.message_id) == ("sent", channel.send.return_value.id)
    call = channel.send.await_args
    assert call.args[0].startswith("<@&20> ")
    assert "@everyone" not in call.args[0] or call.kwargs["allowed_mentions"].everyone is False
    mentions = call.kwargs["allowed_mentions"]
    assert (mentions.everyone, mentions.users) == (False, False)
    assert [r.id for r in mentions.roles] == [20]


async def test_senden_ohne_rolle_pingt_niemanden(bot: GGBot) -> None:
    _, channel, _ = setup(bot)
    sub = await add_twitch(bot)
    await DiscordAnnouncer(bot).announce_stream(sub, stream(title="@everyone @here"))
    mentions = channel.send.await_args.kwargs["allowed_mentions"]
    assert (mentions.everyone, mentions.roles, mentions.users) == (False, [], False)


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (discord.Forbidden(MagicMock(status=403), "x"), "drop"),
        (discord.HTTPException(MagicMock(status=500), "x"), "retry"),
    ],
)
async def test_sendefehler(bot: GGBot, error: Exception, kind: str) -> None:
    _, channel, _ = setup(bot)
    channel.send = AsyncMock(side_effect=error)
    sub = await add_twitch(bot)
    assert (await DiscordAnnouncer(bot).announce_stream(sub, stream())).kind == kind
    assert (await DiscordAnnouncer(bot).announce_video(sub, video())).kind == kind


async def test_kanal_fehlt(bot: GGBot) -> None:
    guild, _, _ = setup(bot)
    del guild.channels_by_id[CH]
    sub = await add_twitch(bot)
    assert (await DiscordAnnouncer(bot).announce_stream(sub, stream())).kind == "drop"


async def test_verfuegbarkeit(bot: GGBot) -> None:
    setup(bot)
    announcer = DiscordAnnouncer(bot)
    assert announcer.available(GUILD_ID) is True
    bot.get_guild = MagicMock(return_value=None)  # type: ignore[method-assign]
    assert announcer.available(GUILD_ID) is False


async def test_ende_markiert_die_nachricht(bot: GGBot) -> None:
    _, channel, _ = setup(bot)
    message = MagicMock()
    message.embeds = [discord.Embed(title="Hallo", color=0x9146FF)]
    message.edit = AsyncMock()
    channel.fetch_message = AsyncMock(return_value=message)
    sub = await add_twitch(bot)
    await bot.services.notify.set_message(sub.id, 555)
    sub = await bot.services.notify.get(GUILD_ID, sub.id)
    await DiscordAnnouncer(bot).end_stream(sub)
    channel.fetch_message.assert_awaited_once_with(555)
    edited = message.edit.await_args.kwargs["embed"]
    assert edited.footer.text == "Stream beendet"
    assert edited.color.value == 0x4F545C
    assert edited.title == "Hallo"


@pytest.mark.parametrize("case", ["no_message", "gone", "forbidden", "no_embed"])
async def test_ende_ohne_nachricht_ist_kein_fehler(bot: GGBot, case: str) -> None:
    _, channel, _ = setup(bot)
    sub = await add_twitch(bot)
    if case != "no_message":
        await bot.services.notify.set_message(sub.id, 555)
        sub = await bot.services.notify.get(GUILD_ID, sub.id)
    if case == "gone":
        channel.fetch_message = AsyncMock(side_effect=discord.NotFound(MagicMock(status=404), "x"))
    elif case == "forbidden":
        channel.fetch_message = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "x"))
    elif case == "no_embed":
        message = MagicMock()
        message.embeds = []
        message.edit = AsyncMock()
        channel.fetch_message = AsyncMock(return_value=message)
    await DiscordAnnouncer(bot).end_stream(sub)


# --- Befehle ---


async def test_twitch_hinzufuegen_und_entfernen(bot: GGBot) -> None:
    guild, channel, cog = setup(bot)
    interaction = click(bot, guild, admin(guild))
    await invoke(
        cog.twitch_add, cog, interaction, "@Alice", channel, "Hi {streamer}", guild.get_role(20)
    )
    sub = (await bot.services.notify.for_guild(GUILD_ID))[0]
    assert (sub.provider, sub.ref, sub.display_name, sub.channel_id) == (
        "twitch",
        "alice",
        "Alice",
        CH,
    )
    assert (sub.message, sub.role_id) == ("Hi {streamer}", 20)
    interaction.response.defer.assert_awaited_once()
    assert "Alice" in sent_text(interaction.followup.send)

    for text in ("alice", "Alice", "https://www.twitch.tv/alice"):
        again = click(bot, guild, admin(guild))
        with pytest.raises(UserError):
            await invoke(cog.twitch_add, cog, again, text, channel, None, None)  # schon vorhanden
    removal = click(bot, guild, admin(guild))
    await invoke(cog.twitch_remove, cog, removal, "ALICE")
    assert await bot.services.notify.for_guild(GUILD_ID) == []
    with pytest.raises(UserError) as exc:
        await invoke(cog.twitch_remove, cog, click(bot, guild, admin(guild)), "alice")
    assert exc.value.key == "error.notify_unknown"


async def test_youtube_hinzufuegen_und_entfernen(bot: GGBot) -> None:
    guild, channel, cog = setup(bot)
    await invoke(
        cog.youtube_add,
        cog,
        click(bot, guild, admin(guild)),
        f"https://www.youtube.com/channel/{CID}",
        channel,
        None,
        None,
    )
    sub = (await bot.services.notify.for_guild(GUILD_ID))[0]
    assert (sub.provider, sub.ref, sub.display_name) == ("youtube", CID, "Mein Kanal")
    await invoke(cog.youtube_remove, cog, click(bot, guild, admin(guild)), "mein kanal")
    assert await bot.services.notify.for_guild(GUILD_ID) == []
    await invoke(cog.youtube_add, cog, click(bot, guild, admin(guild)), CID, channel, None, None)
    await invoke(cog.youtube_remove, cog, click(bot, guild, admin(guild)), CID)
    assert await bot.services.notify.for_guild(GUILD_ID) == []


async def test_entfernen_betrifft_nur_den_eigenen_anbieter(bot: GGBot) -> None:
    guild, _channel, cog = setup(bot)
    await add_twitch(bot)
    with pytest.raises(UserError):
        await invoke(cog.youtube_remove, cog, click(bot, guild, admin(guild)), "alice")
    assert len(await bot.services.notify.for_guild(GUILD_ID)) == 1


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("niemand", "error.notify_not_found"),
        ("a b", "error.notify_not_found"),
        ("../x", "error.notify_not_found"),
    ],
)
async def test_twitch_unbekannt(bot: GGBot, text: str, key: str) -> None:
    guild, channel, cog = setup(bot)
    with pytest.raises(UserError) as exc:
        await invoke(
            cog.twitch_add, cog, click(bot, guild, admin(guild)), text, channel, None, None
        )
    assert exc.value.key == key
    assert await bot.services.notify.for_guild(GUILD_ID) == []


async def test_twitch_ohne_zugangsdaten(bot: GGBot) -> None:
    guild, channel, cog = setup(bot)
    bot.services.twitch._client_id = ""
    with pytest.raises(UserError) as exc:
        await invoke(
            cog.twitch_add, cog, click(bot, guild, admin(guild)), "alice", channel, None, None
        )
    assert exc.value.key == "error.notify_twitch_off"


async def test_youtube_ungueltige_eingabe(bot: GGBot) -> None:
    guild, channel, cog = setup(bot)
    for text in ("mein kanal", "https://evil.example/channel/" + CID, "http://169.254.169.254/"):
        with pytest.raises(UserError) as exc:
            await invoke(
                cog.youtube_add, cog, click(bot, guild, admin(guild)), text, channel, None, None
            )
        assert exc.value.key == "error.notify_not_found"


async def test_anbieter_nicht_erreichbar(bot: GGBot) -> None:
    guild, channel, cog = setup(bot)
    bot.services.youtube._http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(503))
    )
    with pytest.raises(UserError) as exc:
        await invoke(
            cog.youtube_add, cog, click(bot, guild, admin(guild)), CID, channel, None, None
        )
    assert exc.value.key == "error.notify_unavailable"


async def test_ziel_rechte_und_rolle(bot: GGBot) -> None:
    guild, channel, cog = setup(bot)
    channel.permissions_for = MagicMock(return_value=discord.Permissions(view_channel=True))
    with pytest.raises(UserError) as exc:
        await invoke(
            cog.twitch_add, cog, click(bot, guild, admin(guild)), "alice", channel, None, None
        )
    assert exc.value.key == "error.missing_bot_permissions"
    assert "Send Messages" in str(exc.value.params["permissions"])
    channel.permissions_for = MagicMock(return_value=discord.Permissions.all())
    with pytest.raises(UserError) as exc:
        await invoke(
            cog.twitch_add,
            cog,
            click(bot, guild, admin(guild)),
            "alice",
            channel,
            None,
            guild.default_role,
        )
    assert exc.value.key == "error.notify_role_everyone"
    assert await bot.services.notify.for_guild(GUILD_ID) == []


async def test_ungueltige_vorlage_wird_abgelehnt(bot: GGBot) -> None:
    guild, channel, cog = setup(bot)
    with pytest.raises(UserError) as exc:
        await invoke(
            cog.twitch_add,
            cog,
            click(bot, guild, admin(guild)),
            "alice",
            channel,
            "{channel}",
            None,
        )
    assert exc.value.key == "error.notify_message_invalid"


async def test_liste(bot: GGBot) -> None:
    guild, _, cog = setup(bot)
    empty = click(bot, guild, admin(guild))
    await invoke(cog.list_, cog, empty)
    assert "/notify twitch add" in sent_text(empty.response.send_message)
    sub = await add_twitch(bot, role_id=20)
    interaction = click(bot, guild, admin(guild))
    await invoke(cog.list_, cog, interaction)
    text = sent_text(interaction.response.send_message)
    assert f"#{sub.id}" in text
    assert "Twitch" in text
    assert "<@&20>" in text
    assert interaction.response.send_message.await_args.kwargs["allowed_mentions"].roles is False


async def test_test_befehl(bot: GGBot) -> None:
    guild, channel, cog = setup(bot)
    sub = await add_twitch(bot, role_id=20)
    interaction = click(bot, guild, admin(guild))
    await invoke(cog.test, cog, interaction, sub.id)
    channel.send.assert_awaited_once()
    assert channel.send.await_args.kwargs["embed"].url == "https://www.twitch.tv/alice"
    assert str(CH) in sent_text(interaction.response.send_message)

    yt = await bot.services.notify.add(
        GUILD_ID, Resolved("youtube", CID, "Mein Kanal"), channel_id=CH
    )
    channel.send.reset_mock()
    await invoke(cog.test, cog, click(bot, guild, admin(guild)), yt.id)
    assert "youtube.com" in channel.send.await_args.kwargs["embed"].url


async def test_test_befehl_fehler(bot: GGBot) -> None:
    guild, channel, cog = setup(bot)
    other = await add_twitch(bot, guild_id=GUILD_ID + 1)
    with pytest.raises(UserError) as exc:
        await invoke(cog.test, cog, click(bot, guild, admin(guild)), other.id)  # fremder Server
    assert exc.value.key == "error.notify_unknown"
    sub = await add_twitch(bot)
    channel.send = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "x"))
    with pytest.raises(UserError) as exc:
        await invoke(cog.test, cog, click(bot, guild, admin(guild)), sub.id)
    assert exc.value.key == "error.notify_test_failed"
    del guild.channels_by_id[CH]
    with pytest.raises(UserError) as exc:
        await invoke(cog.test, cog, click(bot, guild, admin(guild)), sub.id)
    assert exc.value.key == "error.notify_channel_gone"


async def test_autocomplete(bot: GGBot) -> None:
    guild, _, cog = setup(bot)
    await add_twitch(bot)
    await bot.services.notify.add(GUILD_ID, Resolved("youtube", CID, "Mein Kanal"), channel_id=CH)
    await add_twitch(bot, guild_id=GUILD_ID + 1)
    interaction = click(bot, guild, admin(guild))
    ac_twitch: Any = getattr(cog, "_ac_twitch")  # noqa: B009
    ac_youtube: Any = getattr(cog, "_ac_youtube")  # noqa: B009
    ac_sub: Any = getattr(cog, "_ac_sub")  # noqa: B009
    assert [c.value for c in await ac_twitch(interaction, "ali")] == ["alice"]
    assert [c.value for c in await ac_youtube(interaction, "")] == [CID]
    assert await ac_twitch(interaction, "zzz") == []
    assert len(await ac_sub(interaction, "")) == 2  # nur dieser Server
    interaction.guild_id = None
    assert await ac_twitch(interaction, "") == []
    assert await ac_sub(interaction, "") == []


async def test_abfrage_schleife_laeuft_nur_mit_gate(bot: GGBot) -> None:
    _, _, cog = setup(bot)
    assert await cog._gate("UCx") is True
    assert await cog._gate("UCx") is False  # 5 Minuten Pause pro Feed
    assert await cog._gate("UCy") is True
