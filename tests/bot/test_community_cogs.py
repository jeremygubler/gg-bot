"""Starboard, Geburtstage, Privatsphäre, Stat-Kanäle und geplante Nachrichten."""

import asyncio
import datetime as dt
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from redis.asyncio import Redis
from sqlalchemy import text

from ggbot.bot.client import GGBot
from ggbot.bot.cogs import starboard as starboard_module
from ggbot.bot.cogs.birthday import BirthdayCog, PrivacyCog
from ggbot.bot.cogs.schedule import ScheduleCog
from ggbot.bot.cogs.starboard import StarboardCog
from ggbot.bot.cogs.statchannels import StatChannelCog
from ggbot.bot.services import Services
from ggbot.core.config import AppConfig
from ggbot.core.db.engine import Database
from ggbot.core.errors import UserError
from ggbot.core.settings.birthday import BirthdaySettings
from ggbot.core.settings.starboard import StarboardSettings
from tests.bot.test_cog_flows import sent_text
from tests.bot.test_panels import role
from tests.bot.ticket_fakes import GUILD_ID, click, make_world, member, text_channel
from tests.fakes import invoke

STAR_CH = 80
SRC_CH = 70
MSG = 9000
AUTHOR = 1
STAFF = 11


@pytest.fixture
async def bot(db: Database, redis: Redis) -> GGBot:
    return GGBot(Services.build(AppConfig(enable_members_intent=True), db, redis))


# ======================= Starboard =======================


def src_message() -> MagicMock:
    message = MagicMock()
    message.id = MSG
    message.content = "Eine tolle Nachricht"
    message.author = SimpleNamespace(
        id=AUTHOR,
        display_name="Alice",
        display_avatar=SimpleNamespace(url="https://a/x.png"),
        bot=False,
    )
    message.attachments = [SimpleNamespace(content_type="image/png", url="https://a/pic.png")]
    message.jump_url = "https://discord.com/channels/1/2/3"
    message.created_at = dt.datetime(2026, 3, 1, tzinfo=dt.UTC)
    message.channel = SimpleNamespace(mention=f"<#{SRC_CH}>")
    return message


def sb_world(bot: GGBot) -> tuple[MagicMock, MagicMock, MagicMock]:
    guild = make_world(bot)
    star_channel, src_channel = text_channel(STAR_CH), text_channel(SRC_CH)
    src_channel.nsfw = False
    star_channel.send = AsyncMock(return_value=SimpleNamespace(id=555))
    partial = MagicMock()
    partial.edit = AsyncMock()
    partial.delete = AsyncMock()
    star_channel.get_partial_message = MagicMock(return_value=partial)
    star_channel.partial = partial
    src_channel.fetch_message = AsyncMock(return_value=src_message())
    channels = {STAR_CH: star_channel, SRC_CH: src_channel}
    guild.get_channel = MagicMock(side_effect=channels.get)
    guild.get_channel_or_thread = MagicMock(side_effect=channels.get)
    return guild, star_channel, src_channel


async def configure(bot: GGBot, **kw: Any) -> None:
    data = {"enabled": True, "channel_id": STAR_CH, "threshold": 3} | kw
    await bot.services.settings.save(GUILD_ID, StarboardSettings.model_validate(data))
    bot.services.starboard.invalidate(GUILD_ID)


def reaction(
    user: int,
    emoji: str = "⭐",
    *,
    bot_user: bool = False,
    channel: int = SRC_CH,
    author: int | None = AUTHOR,
) -> Any:
    who = SimpleNamespace(bot=bot_user)
    return SimpleNamespace(
        guild_id=GUILD_ID, channel_id=channel, message_id=MSG, user_id=user, emoji=emoji,
        member=who, message_author_id=author,
    )  # fmt: skip


@pytest.fixture(autouse=True)
def open_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    # Die Bearbeitungspause ist hier nicht Thema; die eigenen Tests prüfen sie.
    monkeypatch.setattr(starboard_module, "EDIT_PAUSE_SECONDS", 3)


async def star(cog: StarboardCog, *users: int, **kw: Any) -> None:
    for u in users:
        await cog.on_raw_reaction_add(reaction(u, **kw))


async def test_unter_der_schwelle_passiert_nichts(bot: GGBot) -> None:
    _, star_channel, _ = sb_world(bot)
    await configure(bot)
    await star(StarboardCog(bot), 2, 3)
    star_channel.send.assert_not_awaited()
    assert await bot.services.starboard.count(GUILD_ID, MSG) == 2


async def test_ab_der_schwelle_wird_einmal_gepostet(bot: GGBot) -> None:
    _, star_channel, _ = sb_world(bot)
    await configure(bot)
    await star(StarboardCog(bot), 2, 3, 4)
    star_channel.send.assert_awaited_once()
    call = star_channel.send.await_args
    assert call.args[0] == f"⭐ **3** | <#{SRC_CH}>"
    embed = call.kwargs["embed"]
    assert embed.description == "Eine tolle Nachricht"
    assert embed.author.name == "Alice"
    assert embed.image.url == "https://a/pic.png"
    assert "discord.com/channels" in embed.fields[0].value
    mentions = call.kwargs["allowed_mentions"]
    assert (mentions.everyone, mentions.users, mentions.roles) == (False, False, False)
    entry = await bot.services.starboard.entry(GUILD_ID, MSG)
    assert entry is not None
    assert entry.star_message_id == 555


async def test_gleichzeitige_sterne_posten_nur_einmal(bot: GGBot) -> None:
    _, star_channel, _ = sb_world(bot)
    await configure(bot)
    cog = StarboardCog(bot)
    await asyncio.gather(*(cog.on_raw_reaction_add(reaction(u)) for u in range(2, 12)))
    star_channel.send.assert_awaited_once()  # Schwelle auch bei Gleichzeitigkeit erreicht


async def test_weitere_sterne_aktualisieren_die_zahl(bot: GGBot) -> None:
    _, star_channel, _ = sb_world(bot)
    await configure(bot)
    cog = StarboardCog(bot)
    await star(cog, 2, 3, 4)
    await bot.services.redis.flushdb()  # Bearbeitungspause zurücksetzen
    await star(cog, 5)
    star_channel.partial.edit.assert_awaited_once()
    assert star_channel.partial.edit.await_args.kwargs["content"] == f"⭐ **4** | <#{SRC_CH}>"
    star_channel.send.assert_awaited_once()  # keine zweite Kopie


async def test_schnelle_folge_wird_gedrosselt_und_per_job_nachgezogen(bot: GGBot) -> None:
    _, star_channel, _ = sb_world(bot)
    await configure(bot)
    cog = StarboardCog(bot)
    await star(cog, 2, 3, 4)
    await bot.services.redis.flushdb()
    await star(cog, 5, 6, 7, 8)  # vier Sterne in schneller Folge
    assert star_channel.partial.edit.await_count == 1  # nur die erste Bearbeitung sofort
    async with bot.services.db.session() as session:
        jobs = (await session.execute(text("SELECT kind, payload FROM scheduled_jobs"))).all()
    assert [(j.kind, j.payload) for j in jobs] == [
        ("starboard.refresh", {"message_id": MSG})
    ]  # ein Job, nicht drei
    job: Any = SimpleNamespace(guild_id=GUILD_ID, payload={"message_id": MSG})
    await cog._refresh_job(job)
    assert (
        star_channel.partial.edit.await_args.kwargs["content"] == f"⭐ **7** | <#{SRC_CH}>"
    )  # Endstand


async def test_unter_die_schwelle_entfernt_die_kopie_und_sie_kommt_wieder(bot: GGBot) -> None:
    _, star_channel, _ = sb_world(bot)
    await configure(bot)
    cog = StarboardCog(bot)
    await star(cog, 2, 3, 4)
    await cog.on_raw_reaction_remove(reaction(4))
    star_channel.partial.delete.assert_awaited_once()
    entry = await bot.services.starboard.entry(GUILD_ID, MSG)
    assert entry is not None
    assert entry.star_message_id is None
    await star(cog, 4)  # wieder drei
    assert star_channel.send.await_count == 2


async def test_sterne_zuruecknehmen_ohne_eintrag(bot: GGBot) -> None:
    sb_world(bot)
    await configure(bot)
    await StarboardCog(bot).on_raw_reaction_remove(reaction(2))  # kein Absturz


@pytest.mark.parametrize(
    "case",
    ["other_emoji", "disabled", "bot", "starboard_channel", "ignored_channel", "own_star", "dm"],
)
async def test_diese_sterne_zaehlen_nicht(bot: GGBot, case: str) -> None:
    sb_world(bot)
    await configure(
        bot,
        enabled=case != "disabled",
        ignored_channels=[SRC_CH] if case == "ignored_channel" else [],
    )
    kwargs: dict[str, Any] = {}
    user = 2
    if case == "other_emoji":
        kwargs["emoji"] = "👍"
    elif case == "bot":
        kwargs["bot_user"] = True
    elif case == "starboard_channel":
        kwargs["channel"] = STAR_CH
    elif case == "own_star":
        user = AUTHOR
    event = reaction(user, **kwargs)
    if case == "dm":
        event.guild_id = None
    await StarboardCog(bot).on_raw_reaction_add(event)
    assert await bot.services.starboard.count(GUILD_ID, MSG) == 0


async def test_eigener_stern_wenn_erlaubt(bot: GGBot) -> None:
    sb_world(bot)
    await configure(bot, self_star=True)
    await star(StarboardCog(bot), AUTHOR)
    assert await bot.services.starboard.count(GUILD_ID, MSG) == 1


async def test_nsfw_kanal(bot: GGBot) -> None:
    _, _, src = sb_world(bot)
    src.nsfw = True
    await configure(bot)
    await star(StarboardCog(bot), 2)
    assert await bot.services.starboard.count(GUILD_ID, MSG) == 0
    await configure(bot, ignore_nsfw=False)
    await star(StarboardCog(bot), 2)
    assert await bot.services.starboard.count(GUILD_ID, MSG) == 1


async def test_autor_unbekannt_wird_nachgeladen(bot: GGBot) -> None:
    _, _, src = sb_world(bot)
    await configure(bot)
    await star(
        StarboardCog(bot), AUTHOR, author=None
    )  # Autor kommt aus der Nachricht → eigener Stern
    src.fetch_message.assert_awaited_once()
    assert await bot.services.starboard.count(GUILD_ID, MSG) == 0
    src.fetch_message = AsyncMock(side_effect=discord.NotFound(MagicMock(status=404), "x"))
    await star(StarboardCog(bot), 2, author=None)  # Nachricht weg → nichts
    assert await bot.services.starboard.count(GUILD_ID, MSG) == 0


async def test_posten_scheitert_und_wird_beim_naechsten_stern_nochmal_versucht(bot: GGBot) -> None:
    _, star_channel, _ = sb_world(bot)
    await configure(bot)
    cog = StarboardCog(bot)
    star_channel.send = AsyncMock(side_effect=discord.HTTPException(MagicMock(status=500), "x"))
    await star(cog, 2, 3, 4)
    entry = await bot.services.starboard.entry(GUILD_ID, MSG)
    assert entry is not None
    assert entry.star_message_id is None
    star_channel.send = AsyncMock(return_value=SimpleNamespace(id=556))
    await star(cog, 5)
    star_channel.send.assert_awaited_once()
    entry = await bot.services.starboard.entry(GUILD_ID, MSG)
    assert entry is not None
    assert entry.star_message_id == 556


async def test_nachricht_geloescht_nimmt_die_kopie_mit(bot: GGBot) -> None:
    _, star_channel, _ = sb_world(bot)
    await configure(bot)
    cog = StarboardCog(bot)
    await star(cog, 2, 3, 4)
    await cog.on_raw_message_delete(SimpleNamespace(guild_id=None, message_id=MSG))  # type: ignore[arg-type]
    star_channel.partial.delete.assert_not_awaited()
    await cog.on_raw_message_delete(SimpleNamespace(guild_id=GUILD_ID, message_id=MSG + 1))  # type: ignore[arg-type]
    star_channel.partial.delete.assert_not_awaited()
    await cog.on_raw_message_delete(SimpleNamespace(guild_id=GUILD_ID, message_id=MSG))  # type: ignore[arg-type]
    star_channel.partial.delete.assert_awaited_once()
    assert await bot.services.starboard.entry(GUILD_ID, MSG) is None


async def test_kopie_von_hand_geloescht(bot: GGBot) -> None:
    _, star_channel, _ = sb_world(bot)
    await configure(bot)
    cog = StarboardCog(bot)
    await star(cog, 2, 3, 4)
    star_channel.partial.edit = AsyncMock(
        side_effect=discord.NotFound(MagicMock(status=404), "weg")
    )
    await bot.services.redis.flushdb()
    await star(cog, 5)
    entry = await bot.services.starboard.entry(GUILD_ID, MSG)
    assert entry is not None
    assert entry.star_message_id is None  # beim nächsten Stern wird neu gepostet


async def test_refresh_job_ohne_server(bot: GGBot) -> None:
    sb_world(bot)
    bot.get_guild = MagicMock(return_value=None)  # type: ignore[method-assign]
    job: Any = SimpleNamespace(guild_id=GUILD_ID, payload={"message_id": MSG})
    with pytest.raises(LookupError):
        await StarboardCog(bot)._refresh_job(job)


async def test_starboard_befehle(bot: GGBot) -> None:
    guild, star_channel, src = sb_world(bot)
    cog = StarboardCog(bot)

    def ia() -> MagicMock:
        return click(bot, guild, member(guild, STAFF, manager=True))

    await invoke(cog.set_, cog, ia(), star_channel, "🌟", 5)
    s = await bot.services.starboard.settings(GUILD_ID)
    assert (s.enabled, s.channel_id, s.emoji, s.threshold) == (True, STAR_CH, "🌟", 5)
    await invoke(cog.options, cog, ia(), True, False)
    s = await bot.services.starboard.settings(GUILD_ID)
    assert (s.self_star, s.ignore_nsfw) == (True, False)
    with pytest.raises(UserError):
        await invoke(cog.options, cog, ia(), None, None)
    await invoke(cog.ignore, cog, ia(), src)
    assert (await bot.services.starboard.settings(GUILD_ID)).ignored_channels == [SRC_CH]
    await invoke(cog.ignore, cog, ia(), src)  # umschalten
    assert (await bot.services.starboard.settings(GUILD_ID)).ignored_channels == []
    status = ia()
    await invoke(cog.status, cog, status)
    assert "🌟" in sent_text(status.response.send_message)
    await invoke(cog.off, cog, ia())
    assert (await bot.services.starboard.settings(GUILD_ID)).enabled is False
    with pytest.raises(UserError) as exc:
        await invoke(cog.set_, cog, ia(), star_channel, "kein emoji", None)
    assert exc.value.key == "error.starboard_invalid"
    star_channel.permissions_for = MagicMock(return_value=discord.Permissions(view_channel=True))
    with pytest.raises(UserError) as exc2:
        await invoke(cog.set_, cog, ia(), star_channel, None, None)
    assert exc2.value.key == "error.missing_bot_permissions"


# ======================= Geburtstage =======================

NOW = dt.datetime(2026, 3, 14, 10, 0, tzinfo=dt.UTC)


async def bd_config(bot: GGBot, **kw: Any) -> None:
    data = {"enabled": True, "channel_id": STAR_CH} | kw
    await bot.services.settings.save(GUILD_ID, BirthdaySettings.model_validate(data))
    bot.services.birthdays.invalidate(GUILD_ID)


def bd_world(bot: GGBot) -> tuple[MagicMock, MagicMock]:
    guild = make_world(bot, role(30, position=2))
    channel = text_channel(STAR_CH)
    channel.send = AsyncMock()
    guild.get_channel = MagicMock(side_effect={STAR_CH: channel}.get)
    guild.members = []
    bot.get_guild = MagicMock(return_value=guild)  # type: ignore[method-assign]
    return guild, channel


def with_guilds(monkeypatch: pytest.MonkeyPatch, bot: GGBot, guild: MagicMock) -> None:
    monkeypatch.setattr(type(bot), "guilds", property(lambda self: [guild]))


async def test_geburtstag_wird_einmal_pro_tag_angekuendigt(
    bot: GGBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    guild, channel = bd_world(bot)
    with_guilds(monkeypatch, bot, guild)
    await bd_config(bot, message="Hoch soll {name} leben, {user} auf {server}!")
    alice = member(guild, 1)
    await bot.services.birthdays.set(GUILD_ID, 1, 14, 3)
    await bot.services.birthdays.set(GUILD_ID, 2, 15, 3)
    cog = BirthdayCog(bot)
    await cog.check(NOW)
    channel.send.assert_awaited_once()
    assert channel.send.await_args.args[0] == "Hoch soll User1 leben, <@1> auf Testserver!"
    mentions = channel.send.await_args.kwargs["allowed_mentions"]
    assert (mentions.everyone, mentions.roles, [u.id for u in mentions.users]) == (
        False,
        False,
        [alice.id],
    )
    await cog.check(NOW + dt.timedelta(hours=3))  # derselbe Tag
    channel.send.assert_awaited_once()
    await cog.check(NOW + dt.timedelta(days=1))  # nächster Tag: der 15.
    assert channel.send.await_count == 1  # Nutzer 2 ist nicht auf dem Server


async def test_kein_gluckwunsch_ohne_einstellung_oder_mitglied(
    bot: GGBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    guild, channel = bd_world(bot)
    with_guilds(monkeypatch, bot, guild)
    await bot.services.birthdays.set(GUILD_ID, 1, 14, 3)
    member(guild, 1)
    cog = BirthdayCog(bot)
    await cog.check(NOW)  # nicht eingeschaltet
    channel.send.assert_not_awaited()
    await bd_config(bot)
    guild.get_member = MagicMock(return_value=None)
    guild.fetch_member = AsyncMock(side_effect=discord.NotFound(MagicMock(status=404), "weg"))
    await cog.check(NOW)  # nicht mehr auf dem Server
    channel.send.assert_not_awaited()
    robot = member(guild, 2)
    robot.bot = True
    guild.get_member = MagicMock(return_value=robot)
    await bot.services.birthdays.set(GUILD_ID, 2, 14, 3)
    await cog.check(NOW)
    channel.send.assert_not_awaited()  # Bots haben keinen Geburtstag


async def test_tageswechsel_nach_serverzeitzone(
    bot: GGBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    guild, channel = bd_world(bot)
    with_guilds(monkeypatch, bot, guild)
    await bd_config(bot)
    member(guild, 1)
    await bot.services.birthdays.set(GUILD_ID, 1, 15, 3)
    await bot.services.guilds.ensure(GUILD_ID)
    await bot.services.guilds.set_timezone(GUILD_ID, "Europe/Zurich")
    late = dt.datetime(2026, 3, 14, 23, 30, tzinfo=dt.UTC)  # in Zürich schon der 15.
    await BirthdayCog(bot).check(late)
    channel.send.assert_awaited_once()


async def test_geburtstagsrolle(bot: GGBot, monkeypatch: pytest.MonkeyPatch) -> None:
    guild, _ = bd_world(bot)
    with_guilds(monkeypatch, bot, guild)
    await bd_config(bot, role_id=30)
    birthday_role = guild.get_role(30)
    child, stale = member(guild, 1), member(guild, 2)
    child.roles, stale.roles = [], [birthday_role]
    child.add_roles, stale.remove_roles = AsyncMock(), AsyncMock()
    birthday_role.members = [stale]
    await bot.services.birthdays.set(GUILD_ID, 1, 14, 3)
    await BirthdayCog(bot).check(NOW)
    child.add_roles.assert_awaited_once()
    stale.remove_roles.assert_awaited_once()  # gestern's Kind verliert die Rolle


async def test_geburtstagsrolle_ueber_dem_bot_wird_nicht_vergeben(
    bot: GGBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    guild, _ = bd_world(bot)
    with_guilds(monkeypatch, bot, guild)
    high = role(31, position=10)  # gleiche Position wie der Bot
    guild.get_role = MagicMock(side_effect={31: high}.get)
    await bd_config(bot, role_id=31)
    child = member(guild, 1)
    child.roles = []
    child.add_roles = AsyncMock()
    await bot.services.birthdays.set(GUILD_ID, 1, 14, 3)
    await BirthdayCog(bot).check(NOW)
    child.add_roles.assert_not_awaited()


async def test_geburtstags_befehle(bot: GGBot) -> None:
    guild, _channel = bd_world(bot)
    cog = BirthdayCog(bot)
    who = member(guild, 1)
    other = member(guild, 2)
    march = SimpleNamespace(value=3)
    await invoke(cog.set_, cog, click(bot, guild, who), 14, march)
    assert await bot.services.birthdays.get(GUILD_ID, 1) == (14, 3)
    with pytest.raises(UserError) as exc:
        await invoke(cog.set_, cog, click(bot, guild, who), 31, SimpleNamespace(value=4))
    assert exc.value.key == "error.bd_invalid"
    shown = click(bot, guild, who)
    await invoke(cog.show, cog, shown, None)
    assert "14.3." in sent_text(shown.response.send_message)
    with pytest.raises(UserError) as exc2:
        await invoke(cog.show, cog, click(bot, guild, who), other)
    assert exc2.value.key == "error.bd_unknown_user"
    upcoming = click(bot, guild, who)
    await invoke(cog.upcoming, cog, upcoming)
    assert "<@1>" in sent_text(upcoming.response.send_message)
    assert upcoming.response.send_message.await_args.kwargs["allowed_mentions"].users is False
    await invoke(cog.remove, cog, click(bot, guild, who))
    with pytest.raises(UserError):
        await invoke(cog.remove, cog, click(bot, guild, who))
    empty = click(bot, guild, who)
    await invoke(cog.upcoming, cog, empty)
    assert sent_text(empty.response.send_message)


async def test_geburtstags_konfiguration(bot: GGBot) -> None:
    guild, channel = bd_world(bot)
    cog = BirthdayCog(bot)

    def ia() -> MagicMock:
        staffer = member(guild, STAFF, manager=True)
        staffer.top_role = SimpleNamespace(position=8)  # über der Geburtstagsrolle
        return click(bot, guild, staffer)

    await invoke(cog.config_set, cog, ia(), channel, guild.get_role(30), "Hi {user}")
    s = await bot.services.birthdays.settings(GUILD_ID)
    assert (s.enabled, s.channel_id, s.role_id, s.message) == (True, STAR_CH, 30, "Hi {user}")
    await invoke(cog.clear_role, cog, ia())
    assert (await bot.services.birthdays.settings(GUILD_ID)).role_id is None
    with pytest.raises(UserError) as exc:
        await invoke(cog.config_set, cog, ia(), channel, None, "{level}")
    assert exc.value.key == "error.birthday_invalid"
    await invoke(cog.config_off, cog, ia())
    assert (await bot.services.birthdays.settings(GUILD_ID)).enabled is False


# ======================= Privatsphäre =======================


async def test_privatsphaere_zeigen_und_loeschen(bot: GGBot) -> None:
    guild, _ = bd_world(bot)
    cog = PrivacyCog(bot)
    who = member(guild, 1)
    other = member(guild, 2)
    await bot.services.birthdays.set(GUILD_ID, 1, 14, 3)
    await bot.services.birthdays.set(GUILD_ID, 2, 1, 1)
    await bot.services.engagement.create_reminder(GUILD_ID, 1, 7, "x", dt.timedelta(hours=1))
    await bot.services.engagement.create_reminder(GUILD_ID, 2, 7, "y", dt.timedelta(hours=1))
    shown = click(bot, guild, who)
    await invoke(cog.show, cog, shown)
    body = sent_text(shown.response.send_message)
    assert "14.3." in body
    assert shown.response.send_message.await_args.kwargs["ephemeral"] is True

    with pytest.raises(UserError) as exc:
        await invoke(cog.delete, cog, click(bot, guild, who), "nein")
    assert exc.value.key == "error.privacy_confirm"
    assert await bot.services.birthdays.get(GUILD_ID, 1) == (14, 3)  # ohne Bestätigung bleibt alles
    await invoke(cog.delete, cog, click(bot, guild, who), "DELETE")
    assert await bot.services.birthdays.get(GUILD_ID, 1) is None
    assert await bot.services.engagement.pending_reminders(GUILD_ID, 1) == []
    assert await bot.services.birthdays.get(GUILD_ID, 2) == (1, 1)  # fremde Daten bleiben
    assert len(await bot.services.engagement.pending_reminders(GUILD_ID, 2)) == 1
    _ = other


# ======================= Stat-Kanäle =======================


def stat_world(bot: GGBot, monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock]:
    guild = make_world(bot)
    guild.member_count = 1234
    guild.members = [SimpleNamespace(bot=False)] * 1200 + [SimpleNamespace(bot=True)] * 34
    guild.channels = [object()] * 20
    guild.roles = [object()] * 10
    guild.premium_subscription_count = 7
    voice = MagicMock(spec=discord.VoiceChannel)
    voice.id = 300
    voice.name = "alt"
    voice.mention = "<#300>"
    voice.edit = AsyncMock()
    guild.get_channel = MagicMock(side_effect={300: voice}.get)
    guild.create_voice_channel = AsyncMock(return_value=voice)
    with_guilds(monkeypatch, bot, guild)
    return guild, voice


async def test_stat_kanal_umbenennen_nur_bei_aenderung(
    bot: GGBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    _guild, voice = stat_world(bot, monkeypatch)
    await bot.services.statchannels.add(GUILD_ID, 300, "members", "Mitglieder: {count}")
    cog = StatChannelCog(bot)
    assert await cog.refresh_all() == 1
    assert voice.edit.await_args.kwargs["name"] == "Mitglieder: 1.234"
    voice.name = "Mitglieder: 1.234"
    assert await cog.refresh_all() == 0  # unverändert: keine Anfrage an Discord
    voice.edit.assert_awaited_once()


@pytest.mark.parametrize(
    ("kind", "expected"),
    [("humans", "1.200"), ("bots", "34"), ("channels", "20"), ("roles", "9"), ("boosts", "7")],
)
async def test_stat_werte(
    bot: GGBot, monkeypatch: pytest.MonkeyPatch, kind: str, expected: str
) -> None:
    _, voice = stat_world(bot, monkeypatch)
    await bot.services.statchannels.add(GUILD_ID, 300, kind, "{count}")
    await StatChannelCog(bot).refresh_all()
    assert voice.edit.await_args.kwargs["name"] == expected


async def test_ohne_members_intent_keine_bot_zahlen(
    bot: GGBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, voice = stat_world(bot, monkeypatch)
    bot.services.config.enable_members_intent = False
    await bot.services.statchannels.add(GUILD_ID, 300, "bots", "{count}")
    assert await StatChannelCog(bot).refresh_all() == 0  # lieber nichts als eine falsche Zahl
    voice.edit.assert_not_awaited()


async def test_geloeschter_stat_kanal_raeumt_den_eintrag_auf(
    bot: GGBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    _guild, _ = stat_world(bot, monkeypatch)
    await bot.services.statchannels.add(
        GUILD_ID, 999, "members", "{count}"
    )  # Kanal existiert nicht
    await StatChannelCog(bot).refresh_all()
    assert await bot.services.statchannels.list(GUILD_ID) == []


async def test_umbenennung_scheitert_ohne_absturz(
    bot: GGBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, voice = stat_world(bot, monkeypatch)
    voice.edit = AsyncMock(side_effect=discord.HTTPException(MagicMock(status=429), "rate"))
    await bot.services.statchannels.add(GUILD_ID, 300, "members", "{count}")
    assert await StatChannelCog(bot).refresh_all() == 0


async def test_stat_kanal_anlegen(bot: GGBot, monkeypatch: pytest.MonkeyPatch) -> None:
    guild, _voice = stat_world(bot, monkeypatch)
    cog = StatChannelCog(bot)
    interaction = click(bot, guild, member(guild, STAFF, manager=True))
    await invoke(cog.add, cog, interaction, SimpleNamespace(value="members"), None)
    name = guild.create_voice_channel.await_args.args[0]
    assert "1.234" in name
    overwrites = guild.create_voice_channel.await_args.kwargs["overwrites"]
    assert overwrites[guild.default_role].connect is False  # niemand soll in den Zähler-Kanal
    [stat] = await bot.services.statchannels.list(GUILD_ID)
    assert (stat.channel_id, stat.kind) == (300, "members")


async def test_stat_kanal_anlegen_fehler(bot: GGBot, monkeypatch: pytest.MonkeyPatch) -> None:
    guild, _voice = stat_world(bot, monkeypatch)
    cog = StatChannelCog(bot)

    def ia() -> MagicMock:
        return click(bot, guild, member(guild, STAFF, manager=True))

    with pytest.raises(UserError) as exc:
        await invoke(cog.add, cog, ia(), SimpleNamespace(value="members"), "ohne zahl")
    assert exc.value.key == "error.st_template"
    guild.create_voice_channel.assert_not_awaited()  # kein verwaister Kanal
    bot.services.config.enable_members_intent = False
    with pytest.raises(UserError) as exc2:
        await invoke(cog.add, cog, ia(), SimpleNamespace(value="bots"), None)
    assert exc2.value.key == "error.st_needs_members"
    bot.services.config.enable_members_intent = True
    guild.create_voice_channel = AsyncMock(
        side_effect=discord.Forbidden(MagicMock(status=403), "x")
    )
    with pytest.raises(UserError) as exc3:
        await invoke(cog.add, cog, ia(), SimpleNamespace(value="members"), None)
    assert exc3.value.key == "error.ticket_create_forbidden"
    assert await bot.services.statchannels.list(GUILD_ID) == []


async def test_stat_kanal_wird_geloescht_wenn_eintrag_scheitert(
    bot: GGBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    guild, voice = stat_world(bot, monkeypatch)
    voice.delete = AsyncMock()
    monkeypatch.setattr(
        bot.services.statchannels,
        "add",
        AsyncMock(side_effect=UserError("error.st_limit", limit=5)),
    )
    cog = StatChannelCog(bot)
    with pytest.raises(UserError):
        await invoke(
            cog.add,
            cog,
            click(bot, guild, member(guild, STAFF, manager=True)),
            SimpleNamespace(value="members"),
            None,
        )
    voice.delete.assert_awaited_once()


async def test_stat_kanal_entfernen_und_liste(bot: GGBot, monkeypatch: pytest.MonkeyPatch) -> None:
    guild, voice = stat_world(bot, monkeypatch)
    await bot.services.statchannels.add(GUILD_ID, 300, "members", "{count}")
    cog = StatChannelCog(bot)
    listing = click(bot, guild, member(guild, STAFF, manager=True))
    await invoke(cog.list_, cog, listing)
    assert "<#300>" in sent_text(listing.response.send_message)
    await invoke(cog.remove, cog, click(bot, guild, member(guild, STAFF, manager=True)), voice)
    assert await bot.services.statchannels.list(GUILD_ID) == []
    with pytest.raises(UserError):
        await invoke(cog.remove, cog, click(bot, guild, member(guild, STAFF, manager=True)), voice)


# ======================= Geplante Nachrichten =======================


def sm_world(bot: GGBot) -> tuple[MagicMock, MagicMock]:
    guild = make_world(bot)
    channel = text_channel(SRC_CH)
    channel.send = AsyncMock()
    guild.get_channel = MagicMock(side_effect={SRC_CH: channel}.get)
    return guild, channel


async def due_job(bot: GGBot, message_id: int) -> Any:
    async with bot.services.db.session() as session:
        rows = (await session.execute(text("SELECT payload FROM scheduled_jobs ORDER BY id"))).all()
    return SimpleNamespace(guild_id=GUILD_ID, payload=rows[0].payload)


async def test_einmal_nachricht_anlegen_und_senden(bot: GGBot) -> None:
    guild, channel = sm_world(bot)
    cog = ScheduleCog(bot)
    interaction = click(bot, guild, member(guild, STAFF, manager=True))
    await invoke(cog.once, cog, interaction, "2h", channel, "Hallo @everyone\\nZeile 2")
    [view] = await bot.services.schedules.list(GUILD_ID)
    assert view.text == "Hallo @everyone\nZeile 2"
    assert f"#{view.id}" in sent_text(interaction.response.send_message)
    job = await due_job(bot, view.id)
    await cog._job(job)
    await cog._job(job)  # zweiter Lauf desselben Jobs
    channel.send.assert_awaited_once()
    assert channel.send.await_args.args[0] == "Hallo @everyone\nZeile 2"
    mentions = channel.send.await_args.kwargs["allowed_mentions"]
    assert (mentions.everyone, mentions.roles, mentions.users) == (False, False, False)
    assert await bot.services.schedules.list(GUILD_ID) == []


async def test_wiederkehrende_nachricht(bot: GGBot) -> None:
    guild, channel = sm_world(bot)
    cog = ScheduleCog(bot)
    await invoke(
        cog.repeat,
        cog,
        click(bot, guild, member(guild, STAFF, manager=True)),
        "1h",
        channel,
        "Tick",
        "10m",
    )
    [view] = await bot.services.schedules.list(GUILD_ID)
    assert view.interval_seconds == 3600
    await cog._job(await due_job(bot, view.id))
    channel.send.assert_awaited_once()
    [after] = await bot.services.schedules.list(GUILD_ID)
    assert after.next_run == view.next_run + dt.timedelta(hours=1)


async def test_geplant_fehler(bot: GGBot) -> None:
    guild, channel = sm_world(bot)
    cog = ScheduleCog(bot)

    def ia() -> MagicMock:
        return click(bot, guild, member(guild, STAFF, manager=True))

    for call in (
        invoke(cog.once, cog, ia(), "xx", channel, "t"),
        invoke(cog.once, cog, ia(), "30s", channel, "t"),
        invoke(cog.repeat, cog, ia(), "1m", channel, "t", None),
        invoke(cog.repeat, cog, ia(), "1h", channel, "t", "400d"),
    ):
        with pytest.raises(UserError):
            await call
    channel.permissions_for = MagicMock(return_value=discord.Permissions(view_channel=True))
    with pytest.raises(UserError) as exc:
        await invoke(cog.once, cog, ia(), "2h", channel, "t")
    assert exc.value.key == "error.missing_bot_permissions"
    assert await bot.services.schedules.list(GUILD_ID) == []


async def test_senden_ohne_kanal_oder_mit_fehler(bot: GGBot) -> None:
    guild, channel = sm_world(bot)
    cog = ScheduleCog(bot)
    await invoke(
        cog.once, cog, click(bot, guild, member(guild, STAFF, manager=True)), "2h", channel, "t"
    )
    [view] = await bot.services.schedules.list(GUILD_ID)
    channel.send = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "x"))
    await cog.send(guild, view)  # kein Absturz
    guild.get_channel = MagicMock(return_value=None)
    await cog.send(guild, view)  # Kanal weg: auch kein Absturz


async def test_job_ohne_server_wird_wiederholt(bot: GGBot) -> None:
    guild, channel = sm_world(bot)
    cog = ScheduleCog(bot)
    await invoke(
        cog.once, cog, click(bot, guild, member(guild, STAFF, manager=True)), "2h", channel, "t"
    )
    [view] = await bot.services.schedules.list(GUILD_ID)
    job = await due_job(bot, view.id)
    bot.get_guild = MagicMock(return_value=None)  # type: ignore[method-assign]
    with pytest.raises(LookupError):
        await cog._job(job)
    assert len(await bot.services.schedules.list(GUILD_ID)) == 1  # nichts verloren


async def test_liste_abbrechen_autocomplete(bot: GGBot) -> None:
    guild, channel = sm_world(bot)
    cog = ScheduleCog(bot)

    def ia() -> MagicMock:
        return click(bot, guild, member(guild, STAFF, manager=True))

    empty = ia()
    await invoke(cog.list_, cog, empty)
    assert sent_text(empty.response.send_message)
    await invoke(cog.once, cog, ia(), "2h", channel, "Erste")
    await invoke(cog.repeat, cog, ia(), "1d", channel, "Zweite", None)
    listing = ia()
    await invoke(cog.list_, cog, listing)
    body = sent_text(listing.response.send_message)
    assert "Erste" in body
    assert "Zweite" in body
    assert listing.response.send_message.await_args.kwargs["allowed_mentions"].roles is False
    ac: Any = getattr(cog, "_ac")  # noqa: B009
    assert [c.name.split(" ", 1)[1] for c in await ac(ia(), "zwei")] == ["Zweite"]
    [first, _second] = await bot.services.schedules.list(GUILD_ID)
    await invoke(cog.remove, cog, ia(), first.id)
    assert len(await bot.services.schedules.list(GUILD_ID)) == 1
    with pytest.raises(UserError):
        await invoke(cog.remove, cog, ia(), first.id)


async def test_entscheidung_nutzt_den_frischen_stand_nicht_den_der_transaktion(
    bot: GGBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gleichzeitige Sterne sehen in ihrer Transaktion nur sich selbst. Der Cog darf der Zahl aus
    `vote` darum nicht trauen, sonst wird die Schwelle knapp verpasst."""
    _, star_channel, _ = sb_world(bot)
    await configure(bot)
    service = bot.services.starboard
    original = service.vote

    async def stale(*args: Any, **kwargs: Any) -> int:
        await original(*args, **kwargs)
        return 1  # so, als hätte keiner die anderen gesehen

    monkeypatch.setattr(service, "vote", stale)
    await star(StarboardCog(bot), 2, 3, 4)
    star_channel.send.assert_awaited_once()
