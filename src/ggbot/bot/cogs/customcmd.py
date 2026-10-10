"""/c und /customcmd: eigene Textbefehle pro Server.

Slash-Befehle lassen sich nicht pro Server beliebig anlegen, ohne bei jeder Änderung neu zu
synchronisieren. Darum gibt es einen einzigen Befehl `/c <name> [args]` mit Autovervollständigung
der Befehle des Servers: keine Synchronisation, kein Message-Content-Intent.
"""

import random

import discord
from discord import app_commands
from discord.ext import commands

from ggbot.bot.checks import ensure_guild
from ggbot.bot.client import GGBot
from ggbot.bot.i18n import d
from ggbot.core.customcmd import template
from ggbot.core.errors import UserError
from ggbot.core.i18n import translator
from ggbot.core.services.engagement import CommandView

MAX_ARGS = 300
_RNG = random.SystemRandom()  # Auswahl in Antworten ist kein Sicherheitsmerkmal


def newlines(text: str) -> str:
    """Slash-Eingabefelder haben keine Zeilenumbrüche: `\\n` wird einer."""
    return text.replace("\\n", "\n")


class CustomCommandCog(commands.Cog):
    def __init__(self, bot: GGBot) -> None:
        self.bot = bot

    async def _locale(self, guild_id: int) -> str:
        return await self.bot.services.guilds.get_locale(guild_id)

    async def _reply(self, interaction: discord.Interaction, key: str, **params: object) -> None:
        locale = await self._locale(interaction.guild_id or 0)
        await interaction.response.send_message(
            translator.t(locale, key, **params),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _names(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild_id is None:
            return []
        needle = current.lower()
        return [
            app_commands.Choice(name=c.name, value=c.name)
            for c in await self.bot.services.engagement.list_commands(interaction.guild_id)
            if needle in c.name
        ][:25]

    # --- Aufrufen ---

    @app_commands.command(name="c", description=d("cmd.c.desc"))
    @app_commands.describe(name=d("cmd.arg.cc_name.desc"), args=d("cmd.arg.cc_args.desc"))
    @app_commands.guild_only()
    async def run(
        self,
        interaction: discord.Interaction,
        name: str,
        args: app_commands.Range[str, 1, MAX_ARGS] | None = None,
    ) -> None:
        guild = ensure_guild(interaction)
        member = interaction.user
        if not isinstance(member, discord.Member):
            raise UserError("error.guild_only")
        service = self.bot.services.engagement
        command = await service.get_command(guild.id, name)
        if command.required_role_id and command.required_role_id not in {
            r.id for r in member.roles
        }:
            raise UserError("error.cc_role", role=f"<@&{command.required_role_id}>")
        if command.cooldown_seconds > 0:
            await self.bot.services.cooldown.ensure(
                "cc", f"{guild.id}:{command.name}:{member.id}", seconds=command.cooldown_seconds
            )
        command = await service.use_command(guild.id, command.name)
        text = template.render(
            command.response, self.values(guild, member, interaction, args, command), _RNG.randrange
        )
        await interaction.response.send_message(
            text,
            ephemeral=command.ephemeral,
            # Wer das Argument tippt, kann beliebige Erwähnungen einsetzen: es wird niemand gepingt.
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @staticmethod
    def values(
        guild: discord.Guild,
        member: discord.Member,
        interaction: discord.Interaction,
        args: str | None,
        command: CommandView,
    ) -> dict[str, object]:
        channel = interaction.channel
        return {
            "user": member.mention,
            "name": member.display_name,
            "server": guild.name,
            "channel": channel.mention if isinstance(channel, discord.abc.GuildChannel) else "",
            "args": args or "",
            "members": guild.member_count or 0,
            "uses": command.uses,
        }

    @run.autocomplete("name")
    async def _ac_run(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self._names(interaction, current)

    # --- Verwalten ---

    customcmd = app_commands.Group(
        name="customcmd",
        description=d("cmd.customcmd.desc"),
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    @customcmd.command(name="add", description=d("cmd.customcmd.add.desc"))
    @app_commands.describe(
        name=d("cmd.arg.cc_name_new.desc"),
        response=d("cmd.arg.cc_response.desc"),
        ephemeral=d("cmd.arg.cc_ephemeral.desc"),
        role=d("cmd.arg.cc_role.desc"),
        cooldown=d("cmd.arg.cc_cooldown.desc"),
    )
    async def add(
        self,
        interaction: discord.Interaction,
        name: str,
        response: str,
        ephemeral: bool = False,
        role: discord.Role | None = None,
        cooldown: app_commands.Range[int, 0, 3600] = 5,
    ) -> None:
        guild = ensure_guild(interaction)
        view = await self.bot.services.engagement.add_command(
            guild.id,
            name,
            newlines(response),
            interaction.user.id,
            ephemeral=ephemeral,
            required_role_id=role.id if role else None,
            cooldown_seconds=cooldown,
        )
        await self._reply(interaction, "customcmd.added", name=view.name)

    @customcmd.command(name="edit", description=d("cmd.customcmd.edit.desc"))
    @app_commands.describe(
        name=d("cmd.arg.cc_name.desc"),
        response=d("cmd.arg.cc_response.desc"),
        ephemeral=d("cmd.arg.cc_ephemeral.desc"),
        role=d("cmd.arg.cc_role.desc"),
        clear_role=d("cmd.arg.cc_clear_role.desc"),
        cooldown=d("cmd.arg.cc_cooldown.desc"),
    )
    async def edit(
        self,
        interaction: discord.Interaction,
        name: str,
        response: str | None = None,
        ephemeral: bool | None = None,
        role: discord.Role | None = None,
        clear_role: bool = False,
        cooldown: app_commands.Range[int, 0, 3600] | None = None,
    ) -> None:
        guild = ensure_guild(interaction)
        if (
            response is None
            and ephemeral is None
            and role is None
            and not clear_role
            and cooldown is None
        ):
            raise UserError("error.card_nothing_to_change")
        view = await self.bot.services.engagement.edit_command(
            guild.id,
            name,
            response=newlines(response) if response is not None else None,
            ephemeral=ephemeral,
            required_role_id=role.id if role else None,
            clear_role=clear_role,
            cooldown_seconds=cooldown,
        )
        await self._reply(interaction, "customcmd.edited", name=view.name)

    @customcmd.command(name="remove", description=d("cmd.customcmd.remove.desc"))
    @app_commands.describe(name=d("cmd.arg.cc_name.desc"))
    async def remove(self, interaction: discord.Interaction, name: str) -> None:
        guild = ensure_guild(interaction)
        await self.bot.services.engagement.remove_command(guild.id, name)
        await self._reply(interaction, "customcmd.removed", name=name.strip().lower())

    @customcmd.command(name="list", description=d("cmd.customcmd.list.desc"))
    async def list_(self, interaction: discord.Interaction) -> None:
        guild = ensure_guild(interaction)
        locale = await self._locale(guild.id)
        found = await self.bot.services.engagement.list_commands(guild.id)
        if not found:
            text = translator.t(locale, "customcmd.list_empty")
        else:
            text = "\n".join(
                translator.t(locale, "customcmd.list_line", name=c.name, uses=c.uses) for c in found
            )[:1900]
        await interaction.response.send_message(
            text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )

    @customcmd.command(name="info", description=d("cmd.customcmd.info.desc"))
    @app_commands.describe(name=d("cmd.arg.cc_name.desc"))
    async def info(self, interaction: discord.Interaction, name: str) -> None:
        guild = ensure_guild(interaction)
        locale = await self._locale(guild.id)
        c = await self.bot.services.engagement.get_command(guild.id, name)
        t = translator.t
        text = t(
            locale,
            "customcmd.info",
            name=c.name,
            uses=c.uses,
            cooldown=c.cooldown_seconds,
            role=f"<@&{c.required_role_id}>" if c.required_role_id else t(locale, "welcome.none"),
            ephemeral=t(locale, "welcome.on" if c.ephemeral else "welcome.off"),
            response=c.response.replace("`", "'")[:1200],
        )
        await interaction.response.send_message(
            text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )

    @edit.autocomplete("name")
    async def _ac_edit(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self._names(interaction, current)

    @remove.autocomplete("name")
    async def _ac_remove(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self._names(interaction, current)

    @info.autocomplete("name")
    async def _ac_info(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self._names(interaction, current)


async def setup(bot: GGBot) -> None:
    await bot.add_cog(CustomCommandCog(bot))
