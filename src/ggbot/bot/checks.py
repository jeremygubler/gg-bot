"""Berechtigungs- und Hierarchieprüfungen. Hier wohnt das Discord-Wissen,
die Entscheidung selbst liegt in `ggbot.core.permissions`."""

import discord

from ggbot.core.errors import UserError
from ggbot.core.permissions import Principal, ensure_can_target


def _names(missing: list[str]) -> str:
    return ", ".join(name.replace("_", " ").title() for name in missing)


def ensure_guild(interaction: discord.Interaction) -> discord.Guild:
    if interaction.guild is None:
        raise UserError("error.guild_only")
    return interaction.guild


def ensure_bot_permissions(interaction: discord.Interaction, **needed: bool) -> None:
    """Prüft die Rechte des Bots im Kanal des Befehls."""
    have = interaction.app_permissions
    missing = [name for name, wanted in needed.items() if wanted and not getattr(have, name)]
    if missing:
        raise UserError("error.missing_bot_permissions", permissions=_names(missing))


def ensure_user_permissions(interaction: discord.Interaction, **needed: bool) -> None:
    have = interaction.permissions
    missing = [name for name, wanted in needed.items() if wanted and not getattr(have, name)]
    if missing:
        raise UserError("error.missing_permissions", permissions=_names(missing))


def principal_of(member: discord.Member) -> Principal:
    return Principal(
        user_id=member.id,
        top_role_position=member.top_role.position,
        is_owner=member.guild.owner_id == member.id,
    )


def ensure_hierarchy(interaction: discord.Interaction, target: discord.Member) -> None:
    """Akteur über Ziel, Bot über Ziel, Ziel nicht Besitzer und nicht man selbst."""
    guild = ensure_guild(interaction)
    actor = interaction.user
    if not isinstance(actor, discord.Member):
        raise UserError("error.guild_only")
    ensure_can_target(principal_of(actor), principal_of(target), principal_of(guild.me))
