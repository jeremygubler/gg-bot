from types import SimpleNamespace
from typing import Any, cast

import discord
import pytest
from discord import app_commands
from redis.asyncio import Redis

from ggbot.bot.checks import ensure_bot_permissions, ensure_user_permissions
from ggbot.bot.client import GGBot
from ggbot.bot.cogs.automod_config import AutomodConfigCog
from ggbot.bot.cogs.channel_tools import ChannelToolsCog
from ggbot.bot.cogs.core import CoreCog, is_guild_only, required_permissions, visible_commands
from ggbot.bot.cogs.customcmd import CustomCommandCog
from ggbot.bot.cogs.eventlog_config import EventLogConfigCog
from ggbot.bot.cogs.giveaway import GiveawayCog
from ggbot.bot.cogs.leveling import LevelingCog
from ggbot.bot.cogs.leveling_config import LevelingConfigCog
from ggbot.bot.cogs.modconfig import ModConfigCog
from ggbot.bot.cogs.moderation import ModerationCog
from ggbot.bot.cogs.notify import NotifyCog
from ggbot.bot.cogs.poll import PollCog
from ggbot.bot.cogs.reminder import ReminderCog
from ggbot.bot.cogs.rolepanels import RolePanelsCog
from ggbot.bot.cogs.ticketpanel import TicketPanelCog
from ggbot.bot.cogs.tickets import TicketsCog
from ggbot.bot.cogs.welcome import WelcomeCog
from ggbot.bot.errors import to_message
from ggbot.bot.i18n import CommandTranslator, command_key
from ggbot.bot.services import Services
from ggbot.core.config import AppConfig
from ggbot.core.db.engine import Database
from ggbot.core.errors import UserError
from ggbot.core.i18n import LOCALES, translator


@pytest.fixture
async def bot(db: Database, redis: Redis) -> GGBot:
    instance = GGBot(Services.build(AppConfig(), db, redis))
    await instance.tree.set_translator(CommandTranslator())
    await instance.add_cog(CoreCog(instance))
    await instance.add_cog(ModerationCog(instance))
    await instance.add_cog(ChannelToolsCog(instance))
    await instance.add_cog(ModConfigCog(instance))
    await instance.add_cog(AutomodConfigCog(instance))
    await instance.add_cog(EventLogConfigCog(instance))
    await instance.add_cog(LevelingCog(instance))
    await instance.add_cog(LevelingConfigCog(instance))
    await instance.add_cog(RolePanelsCog(instance))
    await instance.add_cog(WelcomeCog(instance))
    await instance.add_cog(TicketsCog(instance))
    await instance.add_cog(TicketPanelCog(instance))
    await instance.add_cog(NotifyCog(instance))
    await instance.add_cog(CustomCommandCog(instance))
    await instance.add_cog(GiveawayCog(instance))
    await instance.add_cog(ReminderCog(instance))
    await instance.add_cog(PollCog(instance))
    # Nie gestartet, also gibt es nichts zu schliessen (close() erwartet eine Verbindung).
    return instance


def leaf_commands(bot: GGBot) -> list[app_commands.Command[Any, ..., Any]]:
    return [c for c in bot.tree.walk_commands() if isinstance(c, app_commands.Command)]


def test_jeder_befehl_hat_beschreibung_in_allen_sprachen(bot: GGBot) -> None:
    nodes = list(bot.tree.walk_commands())
    assert nodes
    for node in nodes:
        key = command_key(node.qualified_name)
        for locale in LOCALES:
            assert translator.has(locale, key), f"{key} fehlt in {locale}"


def test_befehlsnamen_sind_englisch_und_klein(bot: GGBot) -> None:
    for node in bot.tree.walk_commands():
        assert node.name == node.name.lower()
        assert node.name.isascii()


async def test_deutsche_lokalisierung_im_payload(bot: GGBot) -> None:
    tr = bot.tree.translator
    assert tr is not None
    payload = await bot.tree.get_command("help").get_translated_payload(bot.tree, tr)  # type: ignore[union-attr]
    assert payload["description"] == translator.t("en", "cmd.help.desc")
    assert payload["description_localizations"]["de"] == translator.t("de", "cmd.help.desc")


async def test_alle_beschreibungen_innerhalb_discord_limit(bot: GGBot) -> None:
    tr = bot.tree.translator
    assert tr is not None
    for command in bot.tree.get_commands():
        payload = await command.get_translated_payload(bot.tree, tr)
        for text in [
            payload["description"],
            *payload.get("description_localizations", {}).values(),
        ]:
            assert 1 <= len(text) <= 100, text


def test_config_befehle_brauchen_manage_guild(bot: GGBot) -> None:
    for command in leaf_commands(bot):
        if command.qualified_name.startswith("config "):
            needed = required_permissions(command)
            assert needed is not None
            assert needed.manage_guild
            assert is_guild_only(command)


def test_rolepanel_braucht_rollen_verwalten_welcome_servereinstellungen(bot: GGBot) -> None:
    seen = {"rolepanel": 0, "welcome": 0}
    for command in leaf_commands(bot):
        root = command.qualified_name.split()[0]
        if root not in seen:
            continue
        seen[root] += 1
        needed = required_permissions(command)
        assert needed is not None, command.qualified_name
        assert is_guild_only(command), command.qualified_name
        if root == "rolepanel":
            assert needed.manage_roles
            assert not needed.manage_guild
        else:
            assert needed.manage_guild
    assert seen["rolepanel"] == 6
    assert seen["welcome"] == 11


def test_help_zeigt_nur_erlaubte_befehle(bot: GGBot) -> None:
    commands_ = leaf_commands(bot)
    plain = discord.Permissions.none()
    manager = discord.Permissions(manage_guild=True)
    moderator = discord.Permissions(moderate_members=True, kick_members=True)

    def names(permissions: discord.Permissions, in_guild: bool = True) -> list[str]:
        return [c.qualified_name for c in visible_commands(commands_, permissions, in_guild)]

    assert names(plain) == [
        "c",
        "card reset",
        "card set",
        "help",
        "leaderboard",
        "rank",
        "remind cancel",
        "remind list",
        "remind me",
        "ticket add",
        "ticket claim",
        "ticket close",
        "ticket remove",
        "ticket transcript",
        "ticket unclaim",
    ]
    assert "config language" in names(manager)
    assert "modconfig dm" in names(manager)
    assert "warn" not in names(manager)  # Manage Server ist kein Moderator-Recht
    assert {"warn", "timeout", "kick", "case", "history", "note add"} <= set(names(moderator))
    assert "ban" not in names(moderator)
    roles_manager = discord.Permissions(manage_roles=True)
    assert {"rolepanel create", "rolepanel publish"} <= set(names(roles_manager))
    assert not any(n.startswith("welcome") for n in names(roles_manager))
    assert not any(n.startswith("rolepanel") for n in names(manager))
    # In Direktnachrichten gibt es keine Server-Befehle.
    assert names(discord.Permissions.all(), in_guild=False) == ["help"]


def test_gruppen_sind_guild_only(bot: GGBot) -> None:
    group = bot.tree.get_command("config")
    assert isinstance(group, app_commands.Group)
    assert group.guild_only is True


@pytest.mark.parametrize("locale", LOCALES)
def test_user_error_wird_uebersetzt_ohne_fehler_id(locale: str) -> None:
    error = app_commands.CommandInvokeError(
        cast(Any, SimpleNamespace(name="test")), UserError("error.unknown_timezone", name="Mars")
    )
    text, error_id = to_message(error, locale)
    assert "Mars" in text
    assert error_id is None


def test_fehlende_rechte_werden_benannt() -> None:
    error = app_commands.MissingPermissions(["manage_guild"])
    text, error_id = to_message(error, "en")
    assert "Manage Guild" in text
    assert error_id is None


def test_unerwarteter_fehler_zeigt_nur_id_kein_stacktrace() -> None:
    boom = app_commands.CommandInvokeError(
        cast(Any, SimpleNamespace(name="test")), RuntimeError("geheime interna")
    )
    text, error_id = to_message(boom, "de")
    assert error_id is not None
    assert error_id in text
    assert "geheime interna" not in text
    assert "Traceback" not in text


def fake_interaction(**perms: bool) -> discord.Interaction:
    permissions = discord.Permissions(**perms)
    return cast(
        discord.Interaction,
        SimpleNamespace(app_permissions=permissions, permissions=permissions),
    )


def test_bot_rechte_pruefung_nennt_fehlende() -> None:
    interaction = fake_interaction(send_messages=True)
    ensure_bot_permissions(interaction, send_messages=True)
    with pytest.raises(UserError) as exc:
        ensure_bot_permissions(interaction, send_messages=True, ban_members=True, kick_members=True)
    assert exc.value.key == "error.missing_bot_permissions"
    assert exc.value.params["permissions"] == "Ban Members, Kick Members"


def test_nutzer_rechte_pruefung() -> None:
    ensure_user_permissions(fake_interaction(manage_guild=True), manage_guild=True)
    with pytest.raises(UserError) as exc:
        ensure_user_permissions(fake_interaction(), manage_guild=True)
    assert exc.value.key == "error.missing_permissions"


async def test_sync_nur_bei_aenderung(bot: GGBot, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []

    async def fake_sync(*, guild: object = None) -> list[object]:
        calls.append(guild)
        return []

    monkeypatch.setattr(bot.tree, "sync", fake_sync)
    await bot._sync_commands()
    await bot._sync_commands()
    assert len(calls) == 1  # unverändert: kein zweiter Sync (Discord limitiert Syncs)

    @bot.tree.command(name="neu", description="neu")
    async def neu(interaction: discord.Interaction) -> None: ...

    await bot._sync_commands()
    assert len(calls) == 2


EXPECTED_PERMISSIONS = {
    "warn": "moderate_members",
    "unwarn": "moderate_members",
    "timeout": "moderate_members",
    "untimeout": "moderate_members",
    "kick": "kick_members",
    "ban": "ban_members",
    "unban": "ban_members",
    "tempban": "ban_members",
    "softban": "ban_members",
    "case": "moderate_members",
    "history": "moderate_members",
    "note add": "moderate_members",
    "note list": "moderate_members",
    "note remove": "moderate_members",
    "purge": "manage_messages",
    "slowmode": "manage_channels",
    "lock": "manage_channels",
    "unlock": "manage_channels",
    **{
        name: "manage_guild"
        for name in (
            "automod status",
            "automod stats",
            "automod enable",
            "automod disable",
            "automod action",
            "automod spam",
            "automod duplicates",
            "automod caps",
            "automod mentions",
            "automod log",
            "automod moderators",
            "automod accountage",
            "automod exempt channel_add",
            "automod exempt channel_remove",
            "automod exempt role_add",
            "automod exempt role_remove",
            "automod invite allow",
            "automod invite disallow",
            "automod link mode",
            "automod link add",
            "automod link remove",
            "automod link list",
            "automod word add",
            "automod word remove",
            "automod word list",
            "automod word test",
            "automod raid set",
            "automod raid end",
        )
    },
    **{
        name: "manage_guild"
        for name in (
            "logging set",
            "logging off",
            "logging status",
            "logging ignore channel_add",
            "logging ignore channel_remove",
            "logging ignore bots",
        )
    },
    **{
        name: "manage_guild"
        for name in (
            "leveling status",
            "leveling enable",
            "leveling disable",
            "leveling xp",
            "leveling cooldown",
            "leveling multiplier",
            "leveling voice",
            "leveling announce",
            "leveling message",
            "leveling sync",
            "leveling give",
            "leveling set_level",
            "leveling reset_user",
            "leveling reset_all",
            "leveling import",
            "leveling noxp channel_add",
            "leveling noxp channel_remove",
            "leveling noxp role_add",
            "leveling noxp role_remove",
            "leveling boost channel_set",
            "leveling boost channel_remove",
            "leveling boost role_set",
            "leveling boost role_remove",
            "leveling reward add",
            "leveling reward remove",
            "leveling reward list",
            "leveling reward mode",
        )
    },
    **{
        f"rolepanel {name}": "manage_roles"
        for name in ("create", "add", "remove", "list", "publish", "delete")
    },
    **{
        f"welcome {name}": "manage_guild"
        for name in (
            "join set",
            "join off",
            "leave set",
            "leave off",
            "dm set",
            "dm off",
            "autorole add",
            "autorole remove",
            "card",
            "preview",
            "status",
        )
    },
    **{
        f"ticketpanel {name}": "manage_guild"
        for name in (
            "create",
            "list",
            "publish",
            "delete",
            "settings",
            "status",
            "category add",
            "category remove",
            "category role_add",
            "category role_remove",
        )
    },
    **{
        f"notify {name}": "manage_guild"
        for name in (
            "list",
            "test",
            "twitch add",
            "twitch remove",
            "youtube add",
            "youtube remove",
        )
    },
    **{f"customcmd {name}": "manage_guild" for name in ("add", "edit", "remove", "list", "info")},
    **{f"giveaway {name}": "manage_guild" for name in ("start", "end", "reroll", "cancel", "list")},
    "poll create": "send_polls",
    "poll end": "send_polls",
    "modconfig warn_expiry": "manage_guild",
    "modconfig dm": "manage_guild",
    "modconfig escalation add": "manage_guild",
    "modconfig escalation remove": "manage_guild",
    "modconfig escalation list": "manage_guild",
}


def test_jeder_moderationsbefehl_hat_die_richtige_standardberechtigung(bot: GGBot) -> None:
    by_name = {c.qualified_name: c for c in leaf_commands(bot)}
    for name, permission in EXPECTED_PERMISSIONS.items():
        needed = required_permissions(by_name[name])
        assert needed is not None, f"{name} hat keine Standardberechtigung"
        assert getattr(needed, permission), f"{name} braucht {permission}"
        assert needed.value == discord.Permissions(**{permission: True}).value, name
        assert is_guild_only(by_name[name]), name


def test_kein_neuer_befehl_ohne_erwartete_berechtigung(bot: GGBot) -> None:
    # Ein neuer Befehl muss hier bewusst eingetragen werden, sonst bliebe er unbemerkt offen.
    open_commands = {
        c.qualified_name for c in leaf_commands(bot) if required_permissions(c) is None
    }
    # Für alle gedacht: Hilfe und die Leveling-Anzeige. Alles andere braucht eine Berechtigung.
    # Die /ticket-Befehle prüfen im Ticket selbst, wer Support ist (das hängt von der Kategorie ab).
    in_ticket = {
        "ticket close",
        "ticket claim",
        "ticket unclaim",
        "ticket add",
        "ticket remove",
        "ticket transcript",
    }
    # Für alle: eigene Befehle ausführen und eigene Erinnerungen verwalten.
    for_members = {"c", "remind me", "remind list", "remind cancel"}
    everyone = {"help", "rank", "leaderboard", "card set", "card reset"} | in_ticket | for_members
    assert open_commands == everyone
    known = set(EXPECTED_PERMISSIONS) | everyone | {"config language", "config timezone"}
    assert {c.qualified_name for c in leaf_commands(bot)} == known


def test_jedes_argument_hat_eine_uebersetzte_beschreibung(bot: GGBot) -> None:
    for command in leaf_commands(bot):
        for parameter in command.parameters:
            extras = getattr(parameter.locale_description, "extras", {})
            key = extras.get("i18n")
            assert key, (
                f"{command.qualified_name}: Argument {parameter.name} ohne i18n-Beschreibung"
            )
            for locale in LOCALES:
                assert translator.has(locale, key), f"{key} fehlt in {locale}"


def test_event_log_braucht_die_nicht_privilegierten_intents_immer(bot: GGBot) -> None:
    intents = bot.intents
    assert intents.voice_states
    assert intents.invites
    assert intents.moderation
    assert intents.guilds


def test_der_bot_pingt_standardmaessig_nie_everyone_oder_rollen(bot: GGBot) -> None:
    mentions = bot.allowed_mentions
    assert mentions is not None
    assert mentions.everyone is False
    assert mentions.roles is False
    assert mentions.replied_user is False
    assert mentions.users is True


def test_user_befehle_sind_nur_auf_servern_nutzbar(bot: GGBot) -> None:
    by_name = {c.qualified_name: c for c in leaf_commands(bot)}
    for name in ("rank", "leaderboard", "card set", "card reset"):
        assert is_guild_only(by_name[name]), name
