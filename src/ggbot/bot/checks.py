"""Berechtigungs- und Hierarchieprüfungen. Hier wohnt das Discord-Wissen,
die Entscheidung selbst liegt in `ggbot.core.permissions`."""

import discord

from ggbot.bot.rewards import assignable
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


def ensure_channel_permissions(
    channel: discord.abc.GuildChannel | discord.Thread,
    member: discord.Member,
    *,
    bot: bool,
    **needed: bool,
) -> None:
    """Rechte in einem *anderen* Kanal als dem des Befehls (z. B. /slowmode #news)."""
    have = channel.permissions_for(member)
    missing = [name for name, wanted in needed.items() if wanted and not getattr(have, name)]
    if missing:
        key = "error.missing_bot_permissions" if bot else "error.missing_permissions"
        raise UserError(key, permissions=_names(missing))


def ensure_grantable(interaction: discord.Interaction, role: discord.Role) -> None:
    """Darf der Bot die Rolle vergeben, und darf der Akteur sie weitergeben?

    Wer Rollen verwalten darf, vergibt trotzdem keine über der eigenen höchsten: sonst wäre ein
    Panel oder eine Autorolle der Weg, sich selbst hochzustufen.
    """
    guild = ensure_guild(interaction)
    if not assignable(guild, role):
        raise UserError("error.role_unassignable", role=role.mention)
    actor = interaction.user
    if (
        isinstance(actor, discord.Member)
        and actor.id != guild.owner_id
        and role.position >= actor.top_role.position
    ):
        raise UserError("error.hierarchy_actor")
