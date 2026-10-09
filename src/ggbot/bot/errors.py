"""Globale Fehlerbehandlung der Slash-Commands: freundlich und ephemer, nie ein Stacktrace."""

import secrets

import discord
from discord import app_commands

from ggbot.core.errors import UserError
from ggbot.core.i18n import translator
from ggbot.core.logging import get_logger

log = get_logger(__name__)


def _permission_names(names: list[str]) -> str:
    return ", ".join(name.replace("_", " ").title() for name in names)


def to_message(error: app_commands.AppCommandError, locale: str) -> tuple[str, str | None]:
    """Übersetzt einen Fehler in (Text, Fehler-ID). Die ID gibt es nur bei echten Bugs."""
    cause = error.original if isinstance(error, app_commands.CommandInvokeError) else error
    t = translator.t
    if isinstance(cause, UserError):
        return t(locale, cause.key, **cause.params), None
    if isinstance(cause, app_commands.MissingPermissions):
        return t(
            locale,
            "error.missing_permissions",
            permissions=_permission_names(cause.missing_permissions),
        ), None
    if isinstance(cause, app_commands.BotMissingPermissions):
        return t(
            locale,
            "error.missing_bot_permissions",
            permissions=_permission_names(cause.missing_permissions),
        ), None
    if isinstance(cause, app_commands.NoPrivateMessage):
        return t(locale, "error.guild_only"), None
    if isinstance(cause, app_commands.CommandOnCooldown):
        return t(locale, "error.cooldown", seconds=int(cause.retry_after) + 1), None
    error_id = secrets.token_hex(4)
    log.error("command_error", error_id=error_id, exc_info=cause)
    return t(locale, "error.generic", error_id=error_id), error_id


async def respond_ephemeral(interaction: discord.Interaction, text: str) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=True)
    else:
        await interaction.response.send_message(text, ephemeral=True)
