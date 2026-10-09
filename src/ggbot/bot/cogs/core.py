"""/help und die Grundeinstellungen des Servers (/config language, /config timezone)."""

from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from ggbot.bot.checks import ensure_guild
from ggbot.bot.client import GGBot
from ggbot.bot.i18n import command_key, d
from ggbot.core.i18n import translator
from ggbot.core.settings.schema import GeneralSettings

AnyCommand = app_commands.Command[Any, ..., Any]

LANGUAGE_CHOICES = [
    app_commands.Choice(name="Deutsch", value="de"),
    app_commands.Choice(name="English", value="en"),
]


def required_permissions(
    command: AnyCommand | app_commands.Group,
) -> discord.Permissions | None:
    """Die nächste gesetzte Default-Berechtigung entlang der Gruppen-Hierarchie."""
    node: AnyCommand | app_commands.Group | None = command
    while node is not None:
        if node.default_permissions is not None:
            return node.default_permissions
        node = node.parent
    return None


def is_guild_only(command: AnyCommand | app_commands.Group) -> bool:
    """`guild_only` steht bei Untergruppen an der Gruppe, nicht am Unterbefehl."""
    node: AnyCommand | app_commands.Group | None = command
    while node is not None:
        if node.guild_only:
            return True
        node = node.parent
    return False


def visible_commands(
    commands_: list[AnyCommand], permissions: discord.Permissions, in_guild: bool
) -> list[AnyCommand]:
    result = []
    for command in commands_:
        if is_guild_only(command) and not in_guild:
            continue
        needed = required_permissions(command)
        if needed is not None and not permissions.is_superset(needed):
            continue
        result.append(command)
    return sorted(result, key=lambda c: c.qualified_name)


class CoreCog(commands.Cog):
    def __init__(self, bot: GGBot) -> None:
        self.bot = bot

    config = app_commands.Group(
        name="config",
        description=d("cmd.config.desc"),
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    @app_commands.command(name="help", description=d("cmd.help.desc"))
    async def help(self, interaction: discord.Interaction) -> None:
        services = self.bot.services
        locale = await services.guilds.get_locale(interaction.guild_id)
        visible = visible_commands(
            [c for c in self.bot.tree.walk_commands() if isinstance(c, app_commands.Command)],
            interaction.permissions,
            in_guild=interaction.guild_id is not None,
        )
        color = discord.Color.blurple()
        if interaction.guild_id is not None:
            general = await services.settings.get(interaction.guild_id, GeneralSettings)
            color = discord.Color(general.embed_color)

        if not visible:
            await interaction.response.send_message(
                translator.t(locale, "help.empty"), ephemeral=True
            )
            return
        embed = discord.Embed(title=translator.t(locale, "help.title"), color=color)
        embed.description = "\n".join(
            f"`/{c.qualified_name}` – {translator.t(locale, command_key(c.qualified_name))}"
            for c in visible
        )
        embed.set_footer(text=translator.t(locale, "help.footer", count=len(visible)))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @config.command(name="language", description=d("cmd.config.language.desc"))
    @app_commands.describe(language=d("cmd.arg.language.desc"))
    @app_commands.choices(language=LANGUAGE_CHOICES)
    async def language(
        self, interaction: discord.Interaction, language: app_commands.Choice[str]
    ) -> None:
        guild = ensure_guild(interaction)
        await self.bot.services.guilds.set_locale(guild.id, language.value)
        # Die Antwort kommt bereits in der neuen Sprache.
        text = translator.t(language.value, "config.language_set", language=language.name)
        await interaction.response.send_message(text, ephemeral=True)

    @config.command(name="timezone", description=d("cmd.config.timezone.desc"))
    @app_commands.describe(name=d("cmd.arg.timezone.desc"))
    async def timezone(self, interaction: discord.Interaction, name: str) -> None:
        guild = ensure_guild(interaction)
        saved = await self.bot.services.guilds.set_timezone(guild.id, name)
        locale = await self.bot.services.guilds.get_locale(guild.id)
        await interaction.response.send_message(
            translator.t(locale, "config.timezone_set", name=saved), ephemeral=True
        )


async def setup(bot: GGBot) -> None:
    await bot.add_cog(CoreCog(bot))
