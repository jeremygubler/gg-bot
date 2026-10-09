from unittest.mock import MagicMock

import discord
import pytest
from discord import app_commands
from redis.asyncio import Redis

from ggbot.bot.client import GGBot
from ggbot.bot.cogs.eventlog_config import EventLogConfigCog
from ggbot.bot.services import Services
from ggbot.core.config import AppConfig
from ggbot.core.db.engine import Database
from ggbot.core.errors import UserError
from ggbot.core.settings.eventlog import (
    EVENT_TYPES,
    NEEDS_MEMBERS,
    NEEDS_MESSAGE_CONTENT,
    LoggingSettings,
)
from tests.bot.test_cog_flows import GUILD_ID, make_guild, make_interaction, make_member, sent_text
from tests.fakes import invoke


def choice(value: str) -> app_commands.Choice[str]:
    return app_commands.Choice(name=value, value=value)


@pytest.fixture
async def bot(db: Database, redis: Redis) -> GGBot:
    config = AppConfig(enable_message_content_intent=True, enable_members_intent=True)
    return GGBot(Services.build(config, db, redis))


@pytest.fixture
async def bot_without_intents(db: Database, redis: Redis) -> GGBot:
    return GGBot(Services.build(AppConfig(), db, redis))


def text_channel(channel_id: int = 800, *, perms: discord.Permissions | None = None) -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    channel.mention = f"<#{channel_id}>"
    channel.permissions_for = MagicMock(return_value=perms or discord.Permissions.all())
    return channel


def setup(bot: GGBot) -> tuple[EventLogConfigCog, MagicMock]:
    guild = make_guild()
    guild.me.guild_permissions = discord.Permissions(view_audit_log=True)
    guild.get_channel = MagicMock(return_value=text_channel())
    return EventLogConfigCog(bot), make_interaction(bot, guild, make_member(1, 5))


async def load(bot: GGBot) -> LoggingSettings:
    return await bot.services.settings.get(GUILD_ID, LoggingSettings)


async def test_kanal_pro_art_waehlen_und_ausschalten(bot: GGBot) -> None:
    cog, interaction = setup(bot)
    await invoke(cog.set_channel, cog, interaction, choice("voice"), text_channel(801))
    await invoke(cog.set_channel, cog, interaction, choice("member_ban"), text_channel(802))
    assert (await load(bot)).channels == {"voice": 801, "member_ban": 802}
    await invoke(cog.set_channel, cog, interaction, choice("voice"), text_channel(803))  # ersetzt
    assert (await load(bot)).channels["voice"] == 803
    await invoke(cog.off, cog, interaction, choice("voice"))
    assert (await load(bot)).channels == {"member_ban": 802}
    await invoke(cog.off, cog, interaction, choice("voice"))  # schon aus: kein Fehler


async def test_alle_arten_auf_einmal(bot: GGBot) -> None:
    cog, interaction = setup(bot)
    await invoke(cog.set_channel, cog, interaction, choice("all"), text_channel(800))
    assert (await load(bot)).enabled() == set(EVENT_TYPES)
    await invoke(cog.off, cog, interaction, choice("all"))
    assert (await load(bot)).channels == {}


async def test_ohne_intent_wird_eine_einzelne_art_abgelehnt(bot_without_intents: GGBot) -> None:
    cog, interaction = setup(bot_without_intents)
    with pytest.raises(UserError) as exc:
        await invoke(cog.set_channel, cog, interaction, choice("message_delete"), text_channel())
    assert exc.value.key == "error.logging_needs_message_content"
    with pytest.raises(UserError) as exc:
        await invoke(cog.set_channel, cog, interaction, choice("member_join"), text_channel())
    assert exc.value.key == "error.logging_needs_members"
    assert (await load(bot_without_intents)).channels == {}
    await invoke(cog.set_channel, cog, interaction, choice("voice"), text_channel())  # geht ohne


async def test_alle_ueberspringt_nur_was_nicht_geht_und_sagt_es(bot_without_intents: GGBot) -> None:
    cog, interaction = setup(bot_without_intents)
    await invoke(cog.set_channel, cog, interaction, choice("all"), text_channel())
    enabled = (await load(bot_without_intents)).enabled()
    assert enabled == set(EVENT_TYPES) - NEEDS_MESSAGE_CONTENT - NEEDS_MEMBERS
    reply = sent_text(interaction.response.send_message)
    assert "message_delete" in reply
    assert "member_join" in reply


async def test_kanal_ohne_schreibrecht_wird_abgelehnt(bot: GGBot) -> None:
    cog, interaction = setup(bot)
    nope = text_channel(perms=discord.Permissions(view_channel=True))
    with pytest.raises(UserError) as exc:
        await invoke(cog.set_channel, cog, interaction, choice("voice"), nope)
    assert exc.value.key == "error.missing_bot_permissions"
    assert (await load(bot)).channels == {}


async def test_massenloeschung_braucht_zusaetzlich_das_dateirecht(bot: GGBot) -> None:
    cog, interaction = setup(bot)
    no_files = text_channel(perms=discord.Permissions(send_messages=True, embed_links=True))
    await invoke(cog.set_channel, cog, interaction, choice("voice"), no_files)  # ohne Dateien ok
    with pytest.raises(UserError) as exc:
        await invoke(cog.set_channel, cog, interaction, choice("message_bulk"), no_files)
    assert "Attach Files" in exc.value.params["permissions"]


async def test_ausnahmen(bot: GGBot) -> None:
    cog, interaction = setup(bot)
    spam = text_channel(55)
    await invoke(cog.ignore_channel_add, cog, interaction, spam)
    await invoke(cog.ignore_channel_add, cog, interaction, spam)  # doppelt
    assert (await load(bot)).ignored_channels == [55]
    await invoke(cog.ignore_channel_remove, cog, interaction, spam)
    assert (await load(bot)).ignored_channels == []
    await invoke(cog.ignore_bots, cog, interaction, False)
    assert (await load(bot)).ignore_bots is False


async def test_aendern_laesst_den_rest_unberuehrt(bot: GGBot) -> None:
    cog, interaction = setup(bot)
    await invoke(cog.ignore_bots, cog, interaction, False)
    await invoke(cog.set_channel, cog, interaction, choice("voice"), text_channel(801))
    settings = await load(bot)
    assert settings.ignore_bots is False
    assert settings.channels == {"voice": 801}


async def test_status_zeigt_zustand_und_probleme(bot_without_intents: GGBot) -> None:
    bot = bot_without_intents
    cog, interaction = setup(bot)
    # Von Hand (z. B. früher mit Intent) gesetzt: der Status muss die Lücke zeigen.
    await bot.services.settings.save(
        GUILD_ID,
        LoggingSettings(channels={"voice": 800, "message_delete": 800}, ignored_channels=[55]),
    )
    interaction.guild.me.guild_permissions = discord.Permissions.none()
    await invoke(cog.status, cog, interaction)
    embed = interaction.response.send_message.await_args.kwargs["embed"]
    lines = embed.description.splitlines()
    assert any(line.startswith("✅ **voice**") for line in lines)
    delete_line = next(line for line in lines if "message_delete" in line)
    assert delete_line.startswith("⚠️")
    assert "Message-Content" in delete_line
    assert any(line.startswith("⬜ **member_ban**") for line in lines)
    assert "<#55>" in next(f.value for f in embed.fields if f.name == "Ignorierte Kanäle")
    assert "Audit-Log" in next(f.value for f in embed.fields if f.name == "⚠️")


async def test_status_meldet_fehlende_schreibrechte_und_geloeschte_kanaele(bot: GGBot) -> None:
    cog, interaction = setup(bot)
    await bot.services.settings.save(
        GUILD_ID, LoggingSettings(channels={"voice": 800, "member_ban": 801, "message_bulk": 802})
    )
    channels = {
        800: text_channel(800, perms=discord.Permissions(view_channel=True)),
        802: text_channel(802, perms=discord.Permissions(send_messages=True, embed_links=True)),
    }
    interaction.guild.get_channel = MagicMock(side_effect=channels.get)  # 801 fehlt
    await invoke(cog.status, cog, interaction)
    lines = interaction.response.send_message.await_args.kwargs["embed"].description.splitlines()
    by_name = {line.split("**")[1]: line for line in lines}
    assert "nicht schreiben" in by_name["voice"]
    assert "nicht gefunden" in by_name["member_ban"]
    assert "Dateien" in by_name["message_bulk"]
