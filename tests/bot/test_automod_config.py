from unittest.mock import MagicMock

import pytest
from discord import app_commands
from redis.asyncio import Redis

from ggbot.bot.client import GGBot
from ggbot.bot.cogs.automod_config import AutomodConfigCog
from ggbot.bot.services import Services
from ggbot.core.config import AppConfig
from ggbot.core.db.engine import Database
from ggbot.core.duration import HOUR
from ggbot.core.errors import UserError
from ggbot.core.settings.automod import AutomodSettings
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


def setup(bot: GGBot) -> tuple[AutomodConfigCog, MagicMock]:
    return AutomodConfigCog(bot), make_interaction(bot, make_guild(), make_member(1, 5))


async def load(bot: GGBot) -> AutomodSettings:
    return await bot.services.settings.get(GUILD_ID, AutomodSettings)


async def test_regeln_ein_und_ausschalten(bot: GGBot) -> None:
    cog, interaction = setup(bot)
    await invoke(cog.enable, cog, interaction, choice("spam"))
    await invoke(cog.enable, cog, interaction, choice("raid"))
    settings = await load(bot)
    assert settings.spam.enabled
    assert settings.raid.enabled
    await invoke(cog.disable, cog, interaction, choice("spam"))
    assert (await load(bot)).spam.enabled is False
    assert (await load(bot)).raid.enabled is True


async def test_einschalten_ohne_intent_wird_abgelehnt_statt_still_zu_scheitern(
    bot_without_intents: GGBot,
) -> None:
    cog, interaction = setup(bot_without_intents)
    with pytest.raises(UserError) as exc:
        await invoke(cog.enable, cog, interaction, choice("words"))
    assert exc.value.key == "error.automod_needs_message_content"
    with pytest.raises(UserError) as exc:
        await invoke(cog.enable, cog, interaction, choice("raid"))
    assert exc.value.key == "error.automod_needs_members"
    assert (await load(bot_without_intents)).any_message_rule() is False
    # Ausschalten geht immer.
    await invoke(cog.disable, cog, interaction, choice("words"))


async def test_aktion_setzen_und_validieren(bot: GGBot) -> None:
    cog, interaction = setup(bot)
    await invoke(cog.action, cog, interaction, choice("caps"), False, True, "1h")
    action = (await load(bot)).caps.action
    assert (action.delete, action.warn, action.timeout_seconds) == (False, True, HOUR)
    await invoke(cog.action, cog, interaction, choice("caps"), True, False, "off")
    assert (await load(bot)).caps.action.timeout_seconds is None

    with pytest.raises(UserError) as exc:  # tut nichts
        await invoke(cog.action, cog, interaction, choice("caps"), False, False, None)
    assert exc.value.key == "error.automod_invalid"
    with pytest.raises(UserError) as exc:
        await invoke(cog.action, cog, interaction, choice("caps"), True, False, "bald")
    assert exc.value.key == "error.invalid_duration"
    with pytest.raises(UserError) as exc:  # über 28 Tagen
        await invoke(cog.action, cog, interaction, choice("caps"), True, False, "30d")
    assert exc.value.key == "error.automod_invalid"
    assert (await load(bot)).caps.action.delete is True  # nichts Ungültiges gespeichert


async def test_zahlenwerte(bot: GGBot) -> None:
    cog, interaction = setup(bot)
    await invoke(cog.spam, cog, interaction, 7, 10)
    await invoke(cog.duplicates, cog, interaction, 4, 60)
    await invoke(cog.caps, cog, interaction, 20, 80)
    await invoke(cog.mentions, cog, interaction, 8)
    settings = await load(bot)
    assert (settings.spam.messages, settings.spam.seconds) == (7, 10)
    assert (settings.duplicates.count, settings.duplicates.seconds) == (4, 60)
    assert (settings.caps.min_length, settings.caps.max_percent) == (20, 80)
    assert settings.mentions.max_mentions == 8


async def test_einstellung_aendern_laesst_den_rest_unberuehrt(bot: GGBot) -> None:
    cog, interaction = setup(bot)
    await invoke(cog.enable, cog, interaction, choice("caps"))
    await invoke(cog.spam, cog, interaction, 9, 9)
    settings = await load(bot)
    assert settings.caps.enabled is True
    assert settings.spam.messages == 9


async def test_ausnahmen_pro_regel_und_global(bot: GGBot) -> None:
    cog, interaction = setup(bot)
    channel = MagicMock(id=55, mention="<#55>")
    role = MagicMock(id=66, mention="<@&66>")
    await invoke(cog.exempt_channel_add, cog, interaction, choice("spam"), channel)
    await invoke(cog.exempt_channel_add, cog, interaction, choice("spam"), channel)  # doppelt
    await invoke(cog.exempt_channel_add, cog, interaction, choice("all"), channel)
    await invoke(cog.exempt_role_add, cog, interaction, choice("words"), role)
    settings = await load(bot)
    assert settings.spam.exempt_channels == [55]
    assert settings.exempt_channels == [55]
    assert settings.words.exempt_roles == [66]
    assert settings.caps.exempt_channels == []  # andere Regeln unberührt

    await invoke(cog.exempt_channel_remove, cog, interaction, choice("spam"), channel)
    await invoke(cog.exempt_role_remove, cog, interaction, choice("words"), role)
    settings = await load(bot)
    assert settings.spam.exempt_channels == []
    assert settings.words.exempt_roles == []
    assert settings.exempt_channels == [55]


async def test_zu_viele_ausnahmen_werden_abgelehnt(bot: GGBot) -> None:
    cog, interaction = setup(bot)
    for channel_id in range(1, 101):
        channel = MagicMock(id=channel_id, mention=f"<#{channel_id}>")
        await invoke(cog.exempt_channel_add, cog, interaction, choice("all"), channel)
    one_too_many = MagicMock(id=101, mention="<#101>")
    with pytest.raises(UserError) as exc:
        await invoke(cog.exempt_channel_add, cog, interaction, choice("all"), one_too_many)
    assert exc.value.key == "error.automod_invalid"
    assert len((await load(bot)).exempt_channels) == 100


async def test_einladungen_aus_code_oder_link(bot: GGBot) -> None:
    cog, interaction = setup(bot)
    await invoke(cog.invite_allow, cog, interaction, "https://discord.gg/MeinServer")
    await invoke(cog.invite_allow, cog, interaction, "Zweiter")
    assert (await load(bot)).invites.allowed_codes == ["meinserver", "zweiter"]
    await invoke(cog.invite_disallow, cog, interaction, "discord.gg/meinserver")
    assert (await load(bot)).invites.allowed_codes == ["zweiter"]


async def test_links_domains_modus_und_liste(bot: GGBot) -> None:
    cog, interaction = setup(bot)
    await invoke(cog.link_mode, cog, interaction, choice("whitelist"))
    await invoke(cog.link_add, cog, interaction, "https://WWW.YouTube.com/watch?v=1")
    await invoke(cog.link_add, cog, interaction, "youtu.be")
    await invoke(cog.link_add, cog, interaction, "youtube.com")  # doppelt
    settings = await load(bot)
    assert settings.links.mode == "whitelist"
    assert settings.links.domains == ["youtu.be", "youtube.com"]

    await invoke(cog.link_list, cog, interaction)
    listing = sent_text(interaction.response.send_message)
    assert "youtube.com" in listing
    assert "erlaubt" in listing

    await invoke(cog.link_remove, cog, interaction, "youtu.be")
    assert (await load(bot)).links.domains == ["youtube.com"]
    with pytest.raises(UserError) as exc:
        await invoke(cog.link_add, cog, interaction, "keine domain")
    assert exc.value.key == "error.automod_domain_invalid"


async def test_wortfilter_anlegen_testen_entfernen(bot: GGBot) -> None:
    cog, interaction = setup(bot)
    await invoke(cog.word_add, cog, interaction, choice("word"), "Dummkopf")
    await invoke(cog.word_add, cog, interaction, choice("wildcard"), "blöd*")
    await invoke(cog.word_add, cog, interaction, choice("regex"), r"\d{4}-\d{3}")
    await invoke(cog.word_add, cog, interaction, choice("word"), "Dummkopf")  # doppelt
    assert len((await load(bot)).words.entries) == 3

    await invoke(cog.word_test, cog, interaction, "du dummkopf")
    assert "Dummkopf" in sent_text(interaction.response.send_message)
    await invoke(cog.word_test, cog, interaction, "alles gut")
    assert "nicht erfasst" in sent_text(interaction.response.send_message)

    await invoke(cog.word_list, cog, interaction)
    assert "blöd*" in sent_text(interaction.response.send_message)

    await invoke(cog.word_remove, cog, interaction, "DUMMKOPF")
    assert [e.pattern for e in (await load(bot)).words.entries] == ["blöd*", r"\d{4}-\d{3}"]
    with pytest.raises(UserError) as exc:
        await invoke(cog.word_remove, cog, interaction, "gibt es nicht")
    assert exc.value.key == "error.automod_word_not_found"


@pytest.mark.parametrize(
    ("kind", "pattern", "key"),
    [
        ("regex", "(kaputt", "error.automod_regex_invalid"),
        ("regex", r"(?<=a)b", "error.automod_regex_invalid"),
        ("regex", "a*", "error.automod_pattern_matches_all"),  # träfe jede Nachricht
        ("regex", ".*", "error.automod_pattern_matches_all"),
        ("wildcard", "*", "error.automod_pattern_matches_all"),
        ("wildcard", "zwei wörter", "error.automod_wildcard_space"),
        ("word", "   ", "error.automod_pattern_empty"),
    ],
)
async def test_gefaehrliche_oder_kaputte_muster_werden_nicht_gespeichert(
    bot: GGBot, kind: str, pattern: str, key: str
) -> None:
    cog, interaction = setup(bot)
    with pytest.raises(UserError) as exc:
        await invoke(cog.word_add, cog, interaction, choice(kind), pattern)
    assert exc.value.key == key
    assert (await load(bot)).words.entries == []


async def test_raid_und_kontoalter_einstellen(bot: GGBot) -> None:
    cog, interaction = setup(bot)
    await invoke(cog.raid_set, cog, interaction, 6, 20, choice("timeout"), 15, "2h")
    raid = (await load(bot)).raid
    assert (raid.joins, raid.seconds, raid.mode_minutes) == (6, 20, 15)
    assert (raid.action.action, raid.action.timeout_seconds) == ("timeout", 2 * HOUR)

    await invoke(cog.accountage, cog, interaction, 14, choice("ban"), None)
    age = (await load(bot)).accountage
    assert (age.min_days, age.action.action) == (14, "ban")

    with pytest.raises(UserError):  # timeout ohne Dauer
        await invoke(cog.raid_set, cog, interaction, 5, 10, choice("timeout"), 10, None)
    with pytest.raises(UserError):  # kick mit Dauer
        await invoke(cog.raid_set, cog, interaction, 5, 10, choice("kick"), 10, "1h")
    with pytest.raises(UserError):
        await invoke(cog.accountage, cog, interaction, 7, choice("timeout"), None)


async def test_raid_modus_von_hand_beenden(bot: GGBot) -> None:
    cog, interaction = setup(bot)
    await invoke(cog.raid_end, cog, interaction)
    assert "nicht aktiv" in sent_text(interaction.response.send_message)
    await bot.services.redis.set("gg:automod:raid:" + str(GUILD_ID), "1", ex=600)
    await invoke(cog.raid_end, cog, interaction)
    assert "beendet" in sent_text(interaction.response.send_message)
    assert await bot.services.automod.raid_active(GUILD_ID) is False


async def test_log_kanal_und_moderatoren(bot: GGBot) -> None:
    cog, interaction = setup(bot)
    channel = MagicMock(id=77, mention="<#77>")
    await invoke(cog.log, cog, interaction, channel)
    assert (await load(bot)).log_channel_id == 77
    await invoke(cog.log, cog, interaction, None)
    assert (await load(bot)).log_channel_id is None
    await invoke(cog.moderators, cog, interaction, False)
    assert (await load(bot)).exempt_moderators is False


async def test_status_und_statistik_werden_angezeigt(bot: GGBot) -> None:
    cog, interaction = setup(bot)
    await invoke(cog.enable, cog, interaction, choice("words"))
    await invoke(cog.status, cog, interaction)
    embed = interaction.response.send_message.await_args.kwargs["embed"]
    assert "✅ **words**" in embed.description
    assert "⬜ **spam**" in embed.description
    assert "Message Content" in next(f.value for f in embed.fields if f.name == "Intents")

    await invoke(cog.stats, cog, interaction, 7)
    assert "Keine Regeltreffer" in sent_text(interaction.response.send_message)
    await bot.services.automod.record_hit(GUILD_ID, 1, 2, "spam", ["delete"])
    await invoke(cog.stats, cog, interaction, 7)
    assert "**spam**: 1" in sent_text(interaction.response.send_message)


async def test_status_warnt_bei_fehlenden_intents(bot_without_intents: GGBot) -> None:
    cog, interaction = setup(bot_without_intents)
    await invoke(cog.status, cog, interaction)
    embed = interaction.response.send_message.await_args.kwargs["embed"]
    warning = next(f.value for f in embed.fields if f.name == "⚠️")
    assert "Message-Content" in warning
    assert "Server-Members" in warning
