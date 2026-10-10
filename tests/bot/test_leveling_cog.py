"""Leveling vom Ereignis bis zur Wirkung, mit Fake-Discord-Objekten und echter DB/Redis."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from redis.asyncio import Redis

from ggbot.bot.client import GGBot
from ggbot.bot.cogs.leveling import LevelingCog, parse_color
from ggbot.bot.services import Services
from ggbot.core.config import AppConfig
from ggbot.core.db.engine import Database
from ggbot.core.errors import UserError
from ggbot.core.leveling.xp import total_xp_for_level
from ggbot.core.settings.leveling import LevelingSettings
from tests.bot.test_automod_cog import make_author, make_guild_with, make_message, make_text_channel
from tests.bot.test_cog_flows import GUILD_ID, make_interaction, make_member, sent_text
from tests.fakes import invoke

CH = 70


@pytest.fixture
async def bot(db: Database, redis: Redis) -> GGBot:
    instance = GGBot(Services.build(AppConfig(), db, redis))
    instance.services.leveling._rng = lambda low, high: low  # vorhersagbar
    return instance


async def configure(bot: GGBot, **kwargs: object) -> None:
    await bot.services.settings.save(
        GUILD_ID,
        LevelingSettings.model_validate({"enabled": True, "cooldown_seconds": 0, **kwargs}),
    )
    bot.services.leveling.invalidate(GUILD_ID)


def role(role_id: int, *, position: int = 1, managed: bool = False) -> MagicMock:
    mock = MagicMock(spec=discord.Role)
    mock.id = role_id
    mock.position = position
    mock.managed = managed
    mock.mention = f"<@&{role_id}>"
    mock.name = f"rolle{role_id}"
    mock.is_default = MagicMock(return_value=False)
    return mock


def world(bot: GGBot, *, roles: dict[int, MagicMock] | None = None) -> tuple[MagicMock, MagicMock]:
    """(Server, Kanal). Die Rolle des Bots steht auf Position 10."""
    channel = make_text_channel(CH)
    guild = make_guild_with(channel)
    guild.me.top_role = SimpleNamespace(position=10)
    table = roles or {}
    guild.get_role = MagicMock(side_effect=table.get)
    return guild, channel


def author_with(*role_mocks: MagicMock) -> MagicMock:
    author = make_author()
    author.roles = list(role_mocks)
    author.display_name = "Alice"
    author.add_roles = AsyncMock()
    author.remove_roles = AsyncMock()
    author.send = AsyncMock()
    return author


async def say(
    bot: GGBot, server: MagicMock, author: MagicMock, channel: MagicMock, **kw: Any
) -> None:
    message = make_message(server, author, channel, "hallo")
    for name, value in kw.items():
        setattr(message, name, value)
    await LevelingCog(bot).on_message(message)


# --- Text-XP ---


async def test_nachricht_gibt_xp(bot: GGBot) -> None:
    guild, channel = world(bot)
    await configure(bot)
    await say(bot, guild, author_with(), channel)
    assert (await bot.services.leveling.rank(GUILD_ID, 500)).progress.xp == 15


async def test_ausgeschaltet_und_ignorierte_quellen_geben_nichts(bot: GGBot) -> None:
    guild, channel = world(bot)
    await say(bot, guild, author_with(), channel)  # nie konfiguriert
    await configure(bot)
    robot = author_with()
    robot.bot = True
    await say(bot, guild, robot, channel)
    await say(bot, guild, author_with(), channel, webhook_id=1)
    await say(bot, guild, author_with(), channel, type=discord.MessageType.new_member)
    await say(bot, guild, author_with(), channel, guild=None)
    assert (await bot.services.leveling.rank(GUILD_ID, 500)).progress.xp == 0


# --- Level-Up ---


async def test_level_up_wird_im_selben_kanal_mit_standardtext_angekuendigt(bot: GGBot) -> None:
    guild, channel = world(bot)
    await configure(bot, xp_min=100, xp_max=100)
    await say(bot, guild, author_with(), channel)
    text = channel.send.await_args.args[0]
    assert "Level 1" in text
    assert "<@500>" in text
    mentions = channel.send.await_args.kwargs["allowed_mentions"]
    assert mentions.everyone is False  # ein Nickname wie "@everyone" darf nichts anpingen
    assert mentions.roles is False
    assert mentions.replied_user is False
    assert len(mentions.users) == 1  # nur das Mitglied selbst


async def test_kein_text_ohne_aufstieg(bot: GGBot) -> None:
    guild, channel = world(bot)
    await configure(bot, xp_min=10, xp_max=10)
    await say(bot, guild, author_with(), channel)
    channel.send.assert_not_awaited()


async def test_eigene_vorlage_und_andere_ziele(bot: GGBot) -> None:
    guild, channel = world(bot)
    other = make_text_channel(88)
    guild.get_channel = MagicMock(side_effect={88: other, CH: channel}.get)
    await configure(
        bot,
        xp_min=100,
        xp_max=100,
        announce="channel",
        announce_channel_id=88,
        announce_message="GG {name}: Level {level} ({xp} XP)",
    )
    await say(bot, guild, author_with(), channel)
    assert other.send.await_args.args[0] == "GG Alice: Level 1 (100 XP)"
    channel.send.assert_not_awaited()

    member = author_with()
    member.id = 501
    await configure(bot, xp_min=100, xp_max=100, announce="dm")
    await say(bot, guild, member, channel)
    member.send.assert_awaited_once()

    await configure(bot, xp_min=100, xp_max=100, announce="off")
    quiet = author_with()
    quiet.id = 502
    await say(bot, guild, quiet, channel)
    quiet.send.assert_not_awaited()


async def test_geschlossene_dms_und_fehlendes_schreibrecht_sind_kein_fehler(bot: GGBot) -> None:
    guild, channel = world(bot)
    forbidden = discord.Forbidden(cast(Any, SimpleNamespace(status=403, reason="x")), "x")
    channel.send = AsyncMock(side_effect=forbidden)
    await configure(bot, xp_min=100, xp_max=100)
    await say(bot, guild, author_with(), channel)  # wirft nicht
    assert (await bot.services.leveling.rank(GUILD_ID, 500)).progress.level == 1


# --- Belohnungsrollen ---


async def test_belohnung_wird_beim_aufstieg_vergeben(bot: GGBot) -> None:
    reward = role(900, position=3)
    guild, channel = world(bot, roles={900: reward})
    await configure(bot, xp_min=100, xp_max=100, rewards={1: 900})
    author = author_with()
    await say(bot, guild, author, channel)
    author.add_roles.assert_awaited_once()
    assert author.add_roles.await_args.args[0] is reward


async def test_ersetzen_nimmt_die_alte_belohnung_weg(bot: GGBot) -> None:
    old, new = role(900, position=3), role(901, position=3)
    guild, channel = world(bot, roles={900: old, 901: new})
    await configure(bot, xp_min=100, xp_max=100, rewards={1: 900, 2: 901}, reward_mode="replace")
    author = author_with(old)  # hat die Rolle von Level 1 schon
    await bot.services.leveling.give(GUILD_ID, 500, 150)  # Level 1
    await say(bot, guild, author, channel)  # 250 XP: Level 2 knapp nicht (255), also noch Level 1
    await say(bot, guild, author, channel)  # 350 XP: Level 2
    assert author.add_roles.await_args.args[0] is new
    assert author.remove_roles.await_args.args[0] is old


async def test_nicht_vergebbare_rolle_wird_uebersprungen_und_nichts_stuerzt_ab(bot: GGBot) -> None:
    too_high = role(900, position=10)  # gleich hoch wie der Bot
    managed = role(901, position=2, managed=True)
    guild, channel = world(bot, roles={900: too_high, 901: managed})
    await configure(bot, xp_min=100, xp_max=100, rewards={1: 900, 2: 901})
    author = author_with()
    await say(bot, guild, author, channel)
    author.add_roles.assert_not_awaited()
    assert (await bot.services.leveling.rank(GUILD_ID, 500)).progress.level == 1


async def test_gelöschte_rolle_wird_uebersprungen(bot: GGBot) -> None:
    guild, channel = world(bot, roles={})  # Rolle 900 gibt es nicht mehr
    await configure(bot, xp_min=100, xp_max=100, rewards={1: 900})
    author = author_with()
    await say(bot, guild, author, channel)
    author.add_roles.assert_not_awaited()


async def test_fehlendes_recht_beim_vergeben_wird_gemeldet_nicht_geworfen(bot: GGBot) -> None:
    reward = role(900, position=3)
    guild, channel = world(bot, roles={900: reward})
    await configure(bot, xp_min=100, xp_max=100, rewards={1: 900})
    author = author_with()
    author.add_roles = AsyncMock(
        side_effect=discord.Forbidden(cast(Any, SimpleNamespace(status=403, reason="x")), "x")
    )
    await say(bot, guild, author, channel)  # wirft nicht
    channel.send.assert_awaited()  # die Ankündigung kommt trotzdem


# --- Voice ---


def voice_channel(channel_id: int, members: list[tuple[MagicMock, dict[str, bool]]]) -> MagicMock:
    channel = MagicMock(spec=discord.VoiceChannel)
    channel.id = channel_id
    channel.send = AsyncMock()
    channel.voice_states = {
        member.id: SimpleNamespace(
            self_mute=flags.get("self_mute", False),
            self_deaf=flags.get("self_deaf", False),
            mute=flags.get("mute", False),
            deaf=flags.get("deaf", False),
        )
        for member, flags in members
    }
    return channel


def voice_member(user_id: int, *, bot_user: bool = False) -> MagicMock:
    member = make_author(user_id)
    member.bot = bot_user
    member.add_roles = AsyncMock()
    member.remove_roles = AsyncMock()
    member.display_name = f"u{user_id}"
    return member


def voice_guild(
    *channels: MagicMock, members: list[MagicMock], afk: MagicMock | None = None
) -> MagicMock:
    guild = MagicMock(spec=discord.Guild)
    guild.id = GUILD_ID
    guild.me = SimpleNamespace(top_role=SimpleNamespace(position=10))
    guild.voice_channels = list(channels)
    guild.stage_channels = []
    guild.afk_channel = afk
    by_id = {m.id: m for m in members}
    guild.get_member = MagicMock(side_effect=by_id.get)
    guild.get_role = MagicMock(return_value=None)
    return guild


async def tick(bot: GGBot, monkeypatch: pytest.MonkeyPatch, guild: MagicMock) -> None:
    monkeypatch.setattr(type(bot), "guilds", property(lambda self: [guild]))
    await LevelingCog(bot).voice_tick()


async def xp_of(bot: GGBot, user_id: int) -> int:
    return (await bot.services.leveling.rank(GUILD_ID, user_id)).progress.xp


async def test_voice_zu_zweit_gibt_xp_allein_nicht(
    bot: GGBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    await configure(bot, voice={"enabled": True, "xp_min": 10, "xp_max": 10})
    a, b, c = voice_member(1), voice_member(2), voice_member(3)
    pair = voice_channel(10, [(a, {}), (b, {})])
    alone = voice_channel(11, [(c, {})])
    await tick(bot, monkeypatch, voice_guild(pair, alone, members=[a, b, c]))
    assert (await xp_of(bot, 1), await xp_of(bot, 2), await xp_of(bot, 3)) == (10, 10, 0)


async def test_voice_stumme_bots_und_afk_bekommen_nichts(
    bot: GGBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    await configure(bot, voice={"enabled": True, "xp_min": 10, "xp_max": 10})
    talker, muted, deaf, robot = (
        voice_member(1),
        voice_member(2),
        voice_member(3),
        voice_member(4, bot_user=True),
    )
    lounge = voice_channel(
        10, [(talker, {}), (muted, {"self_mute": True}), (deaf, {"deaf": True}), (robot, {})]
    )
    sleepers = [voice_member(5), voice_member(6)]
    afk = voice_channel(11, [(sleepers[0], {}), (sleepers[1], {})])
    guild = voice_guild(lounge, afk, members=[talker, muted, deaf, robot, *sleepers], afk=afk)
    await tick(bot, monkeypatch, guild)
    assert await xp_of(bot, 1) == 10
    assert (await xp_of(bot, 2), await xp_of(bot, 3), await xp_of(bot, 4)) == (0, 0, 0)
    assert (await xp_of(bot, 5), await xp_of(bot, 6)) == (0, 0)


async def test_voice_ohne_gesellschaftspflicht(bot: GGBot, monkeypatch: pytest.MonkeyPatch) -> None:
    await configure(
        bot, voice={"enabled": True, "xp_min": 10, "xp_max": 10, "requires_company": False}
    )
    solo = voice_member(1)
    await tick(bot, monkeypatch, voice_guild(voice_channel(10, [(solo, {})]), members=[solo]))
    assert await xp_of(bot, 1) == 10


async def test_voice_aus_oder_leveling_aus(bot: GGBot, monkeypatch: pytest.MonkeyPatch) -> None:
    a, b = voice_member(1), voice_member(2)
    guild = voice_guild(voice_channel(10, [(a, {}), (b, {})]), members=[a, b])
    await configure(bot, voice={"enabled": False})
    await tick(bot, monkeypatch, guild)
    await configure(bot, enabled=False, voice={"enabled": True})
    await tick(bot, monkeypatch, guild)
    assert await xp_of(bot, 1) == 0


async def test_voice_unbekannte_mitglieder_werden_uebersprungen(
    bot: GGBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    await configure(
        bot, voice={"enabled": True, "xp_min": 10, "xp_max": 10, "requires_company": False}
    )
    known = voice_member(1)
    stranger = voice_member(2)
    channel = voice_channel(10, [(known, {}), (stranger, {})])
    await tick(bot, monkeypatch, voice_guild(channel, members=[known]))  # 2 ist nicht im Cache
    assert (await xp_of(bot, 1), await xp_of(bot, 2)) == (10, 0)


async def test_voice_level_up_kommt_in_den_sprachkanal(
    bot: GGBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    await configure(bot, voice={"enabled": True, "xp_min": 100, "xp_max": 100})
    a, b = voice_member(1), voice_member(2)
    channel = voice_channel(10, [(a, {}), (b, {})])
    await tick(bot, monkeypatch, voice_guild(channel, members=[a, b]))
    assert channel.send.await_count == 2  # beide steigen auf Level 1 auf
    assert "Level 1" in channel.send.await_args.args[0]


async def test_voice_multiplikator_des_kanals(bot: GGBot, monkeypatch: pytest.MonkeyPatch) -> None:
    await configure(
        bot,
        voice={"enabled": True, "xp_min": 10, "xp_max": 10, "requires_company": False},
        channel_multipliers={10: 3.0},
    )
    a = voice_member(1)
    await tick(bot, monkeypatch, voice_guild(voice_channel(10, [(a, {})]), members=[a]))
    assert await xp_of(bot, 1) == 30


# --- Anzeige ---


def user_interaction(bot: GGBot, guild: MagicMock, user: MagicMock) -> MagicMock:
    interaction = make_interaction(bot, guild, user)
    interaction.guild_id = GUILD_ID
    return interaction


def rank_member(user_id: int = 500, *, bot_user: bool = False) -> MagicMock:
    member = make_member(user_id, 1)
    member.bot = bot_user
    member.display_name = "Alice"
    avatar = MagicMock()
    avatar.replace = MagicMock(return_value=avatar)
    avatar.read = AsyncMock(return_value=b"")
    member.display_avatar = avatar
    return member


async def test_rank_liefert_ein_png(bot: GGBot) -> None:
    guild, _ = world(bot)
    await configure(bot)
    await bot.services.leveling.give(GUILD_ID, 500, 150)
    me = rank_member()
    interaction = user_interaction(bot, guild, me)
    await invoke(LevelingCog(bot).rank, LevelingCog(bot), interaction, None)
    file = interaction.followup.send.await_args.kwargs["file"]
    assert file.filename == "rank.png"
    assert file.fp.read().startswith(b"\x89PNG")


async def test_rank_fuer_anderes_mitglied_und_fehler(bot: GGBot) -> None:
    guild, _ = world(bot)
    cog = LevelingCog(bot)
    me = rank_member()
    interaction = user_interaction(bot, guild, me)
    with pytest.raises(UserError) as exc:  # Leveling aus
        await invoke(cog.rank, cog, interaction, None)
    assert exc.value.key == "error.leveling_disabled"
    await configure(bot)
    with pytest.raises(UserError) as exc:  # Bots haben keine XP
        await invoke(cog.rank, cog, interaction, rank_member(9, bot_user=True))
    assert exc.value.key == "error.leveling_bot"
    await invoke(cog.rank, cog, interaction, rank_member(77))  # fremdes Mitglied ohne XP


async def test_bestenliste(bot: GGBot) -> None:
    guild, _ = world(bot)
    guild.name = "Testserver"
    cog = LevelingCog(bot)
    interaction = user_interaction(bot, guild, rank_member())
    await configure(bot)
    await invoke(cog.leaderboard, cog, interaction, 1)
    assert (
        "Noch niemand" in interaction.response.send_message.await_args.kwargs["embed"].description
    )

    await bot.services.leveling.give(GUILD_ID, 1, 5000)
    await bot.services.leveling.give(GUILD_ID, 2, 100)
    await invoke(cog.leaderboard, cog, interaction, 1)
    embed = interaction.response.send_message.await_args.kwargs["embed"]
    lines = embed.description.splitlines()
    assert lines[0].startswith("`#1` <@1>")
    assert "Level 1" in lines[1]
    assert "Seite 1/1" in embed.footer.text
    assert interaction.response.send_message.await_args.kwargs["allowed_mentions"].users is False


# --- Persönliche Karte ---


def test_farben_parsen() -> None:
    assert parse_color("#ff8800") == 0xFF8800
    assert parse_color("FF8800") == 0xFF8800
    assert parse_color(" #000000 ") == 0
    for bad in ("", "red", "#fff", "#gggggg", "#1234567", "0xff8800"):
        with pytest.raises(UserError) as exc:
            parse_color(bad)
        assert exc.value.key == "error.card_color_invalid"


async def test_karte_aendern_zuruecksetzen(bot: GGBot) -> None:
    guild, _ = world(bot)
    await configure(bot)
    cog = LevelingCog(bot)
    me = rank_member()
    interaction = user_interaction(bot, guild, me)
    with pytest.raises(UserError) as exc:
        await invoke(cog.card_set, cog, interaction, None, None, None)
    assert exc.value.key == "error.card_nothing_to_change"
    with pytest.raises(UserError):
        await invoke(cog.card_set, cog, interaction, "kein-hex", None, None)

    await invoke(cog.card_set, cog, interaction, "#112233", None, SimpleNamespace(value="dots"))
    style = await bot.services.leveling.card_style(GUILD_ID, 500)
    assert (style.accent, style.template) == (0x112233, "dots")
    assert interaction.followup.send.await_args.kwargs["file"].filename == "rank.png"
    # Ein weiterer Aufruf ändert nur, was angegeben ist.
    await invoke(cog.card_set, cog, interaction, None, "#000000", None)
    style = await bot.services.leveling.card_style(GUILD_ID, 500)
    assert (style.accent, style.background, style.template) == (0x112233, 0, "dots")

    await invoke(cog.card_reset, cog, interaction)
    assert (await bot.services.leveling.card_style(GUILD_ID, 500)).accent == 0x5865F2


async def test_einstellungs_event_leert_den_cache(bot: GGBot) -> None:
    from ggbot.core import events

    await configure(bot)
    cog = LevelingCog(bot)
    assert (await bot.services.leveling.settings(GUILD_ID)).enabled is True
    await bot.services.settings.save(GUILD_ID, LevelingSettings(enabled=False))
    await cog.on_ggbot_settings_changed(events.SettingsChanged(guild_id=GUILD_ID, module="automod"))
    assert (await bot.services.leveling.settings(GUILD_ID)).enabled is True
    await cog.on_ggbot_settings_changed(
        events.SettingsChanged(guild_id=GUILD_ID, module="leveling")
    )
    assert (await bot.services.leveling.settings(GUILD_ID)).enabled is False


def test_vollstaendigkeit_der_hilfsfunktionen() -> None:
    assert total_xp_for_level(1) == 100
    assert sent_text is not None
