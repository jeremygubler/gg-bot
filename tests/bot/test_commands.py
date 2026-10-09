from types import SimpleNamespace
from typing import Any, cast

import discord
import pytest
from discord import app_commands
from redis.asyncio import Redis

from ggbot.bot.checks import ensure_bot_permissions, ensure_user_permissions
from ggbot.bot.client import GGBot
from ggbot.bot.cogs.channel_tools import ChannelToolsCog
from ggbot.bot.cogs.core import CoreCog, is_guild_only, required_permissions, visible_commands
from ggbot.bot.cogs.modconfig import ModConfigCog
from ggbot.bot.cogs.moderation import ModerationCog
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


def test_help_zeigt_nur_erlaubte_befehle(bot: GGBot) -> None:
    commands_ = leaf_commands(bot)
    plain = discord.Permissions.none()
    manager = discord.Permissions(manage_guild=True)
    moderator = discord.Permissions(moderate_members=True, kick_members=True)

    def names(permissions: discord.Permissions, in_guild: bool = True) -> list[str]:
        return [c.qualified_name for c in visible_commands(commands_, permissions, in_guild)]

    assert names(plain) == ["help"]
    assert "config language" in names(manager)
    assert "modconfig dm" in names(manager)
    assert "warn" not in names(manager)  # Manage Server ist kein Moderator-Recht
    assert {"warn", "timeout", "kick", "case", "history", "note add"} <= set(names(moderator))
    assert "ban" not in names(moderator)
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
    assert open_commands == {"help"}
    known = set(EXPECTED_PERMISSIONS) | {"help", "config language", "config timezone"}
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
