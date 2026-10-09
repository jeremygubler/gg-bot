"""/logging: Kanal pro Ereignisart wählen, Ausnahmen setzen, Zustand prüfen."""

from collections.abc import Callable
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands
from pydantic import ValidationError

from ggbot.bot.checks import ensure_channel_permissions, ensure_guild
from ggbot.bot.client import GGBot
from ggbot.bot.i18n import d
from ggbot.core.errors import UserError
from ggbot.core.i18n import translator
from ggbot.core.services.eventlog import required_intent
from ggbot.core.settings.eventlog import EVENT_TYPES, LoggingSettings

Mutation = Callable[[dict[str, Any]], None]
EVENT_CHOICES = [app_commands.Choice(name=name, value=name) for name in (*EVENT_TYPES, "all")]
OFF_CHOICES = EVENT_CHOICES
# Einträge mit Anhang brauchen zusätzlich das Recht, Dateien zu senden.
_NEEDS_FILES = frozenset({"message_bulk"})


class EventLogConfigCog(commands.Cog):
    def __init__(self, bot: GGBot) -> None:
        self.bot = bot

    logging = app_commands.Group(
        name="logging",
        description=d("cmd.logging.desc"),
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )
    ignore = app_commands.Group(
        name="ignore", description=d("cmd.logging.ignore.desc"), parent=logging
    )

    # --- Hilfen ---

    def _intent_available(self, event: str) -> bool:
        needed = required_intent(event)
        if needed == "message_content":
            return bool(self.bot.intents.message_content)
        if needed == "members":
            return bool(self.bot.intents.members)
        return True

    async def _edit(
        self, interaction: discord.Interaction, mutate: Mutation, reply_key: str, **params: object
    ) -> LoggingSettings:
        guild = ensure_guild(interaction)
        services = self.bot.services
        current = await services.settings.get(guild.id, LoggingSettings)
        data = current.model_dump()
        mutate(data)
        try:
            updated = LoggingSettings.model_validate(data)
        except ValidationError as exc:
            raise UserError("error.logging_invalid") from exc
        await services.settings.save(guild.id, updated)
        services.eventlog.invalidate(guild.id)
        locale = await services.guilds.get_locale(guild.id)
        await interaction.response.send_message(
            translator.t(locale, reply_key, **params), ephemeral=True
        )
        return updated

    # --- Kanäle ---

    @logging.command(name="set", description=d("cmd.logging.set.desc"))
    @app_commands.describe(event=d("cmd.arg.event.desc"), channel=d("cmd.arg.target_channel.desc"))
    @app_commands.choices(event=EVENT_CHOICES)
    async def set_channel(
        self,
        interaction: discord.Interaction,
        event: app_commands.Choice[str],
        channel: discord.TextChannel,
    ) -> None:
        guild = ensure_guild(interaction)
        wanted = list(EVENT_TYPES) if event.value == "all" else [event.value]
        usable = [name for name in wanted if self._intent_available(name)]
        skipped = [name for name in wanted if name not in usable]
        if not usable:
            # Eine einzelne Art, deren Intent fehlt: ablehnen statt ein stummes Log anzulegen.
            needed = required_intent(wanted[0])
            raise UserError(f"error.logging_needs_{needed}")
        needs_files = any(name in _NEEDS_FILES for name in usable)
        ensure_channel_permissions(
            channel,
            guild.me,
            bot=True,
            send_messages=True,
            embed_links=True,
            attach_files=needs_files,
        )

        def mutate(data: dict[str, Any]) -> None:
            for name in usable:
                data["channels"][name] = channel.id

        locale = await self.bot.services.guilds.get_locale(guild.id)
        await self._edit(
            interaction,
            mutate,
            "logging.set",
            event=event.value if event.value != "all" else translator.t(locale, "logging.all"),
            channel=channel.mention,
            skipped=(
                "\n" + translator.t(locale, "logging.skipped", events=", ".join(skipped))
                if skipped
                else ""
            ),
        )

    @logging.command(name="off", description=d("cmd.logging.off.desc"))
    @app_commands.describe(event=d("cmd.arg.event.desc"))
    @app_commands.choices(event=OFF_CHOICES)
    async def off(self, interaction: discord.Interaction, event: app_commands.Choice[str]) -> None:
        guild = ensure_guild(interaction)
        locale = await self.bot.services.guilds.get_locale(guild.id)

        def mutate(data: dict[str, Any]) -> None:
            if event.value == "all":
                data["channels"] = {}
            else:
                data["channels"].pop(event.value, None)

        shown = event.value if event.value != "all" else translator.t(locale, "logging.all")
        await self._edit(interaction, mutate, "logging.off", event=shown)

    # --- Ausnahmen ---

    @ignore.command(name="channel_add", description=d("cmd.logging.ignore.channel_add.desc"))
    @app_commands.describe(channel=d("cmd.arg.ignored_channel.desc"))
    async def ignore_channel_add(
        self, interaction: discord.Interaction, channel: discord.abc.GuildChannel
    ) -> None:
        def mutate(data: dict[str, Any]) -> None:
            if channel.id not in data["ignored_channels"]:
                data["ignored_channels"].append(channel.id)

        await self._edit(interaction, mutate, "logging.ignored_added", channel=channel.mention)

    @ignore.command(name="channel_remove", description=d("cmd.logging.ignore.channel_remove.desc"))
    @app_commands.describe(channel=d("cmd.arg.ignored_channel.desc"))
    async def ignore_channel_remove(
        self, interaction: discord.Interaction, channel: discord.abc.GuildChannel
    ) -> None:
        def mutate(data: dict[str, Any]) -> None:
            data["ignored_channels"] = [c for c in data["ignored_channels"] if c != channel.id]

        await self._edit(interaction, mutate, "logging.ignored_removed", channel=channel.mention)

    @ignore.command(name="bots", description=d("cmd.logging.ignore.bots.desc"))
    @app_commands.describe(enabled=d("cmd.arg.enabled.desc"))
    async def ignore_bots(self, interaction: discord.Interaction, enabled: bool) -> None:
        def mutate(data: dict[str, Any]) -> None:
            data["ignore_bots"] = enabled

        key = "logging.bots_ignored" if enabled else "logging.bots_logged"
        await self._edit(interaction, mutate, key)

    # --- Zustand ---

    @logging.command(name="status", description=d("cmd.logging.status.desc"))
    async def status(self, interaction: discord.Interaction) -> None:
        guild = ensure_guild(interaction)
        t = translator.t
        locale = await self.bot.services.guilds.get_locale(guild.id)
        settings = await self.bot.services.settings.get(guild.id, LoggingSettings)
        lines = []
        for name in EVENT_TYPES:
            channel_id = settings.channels.get(name)
            if channel_id is None:
                lines.append(f"⬜ **{name}**")
                continue
            problems = self._problems(guild, name, channel_id, locale)
            mark = "⚠️" if problems else "✅"
            suffix = f" – {problems}" if problems else ""
            lines.append(f"{mark} **{name}** → <#{channel_id}>{suffix}")
        embed = discord.Embed(title=t(locale, "logging.status.title"), description="\n".join(lines))
        embed.add_field(
            name=t(locale, "logging.status.ignored"),
            value=", ".join(f"<#{c}>" for c in settings.ignored_channels) or "–",
        )
        embed.add_field(
            name=t(locale, "logging.status.bots"),
            value=t(locale, "automod.yes" if settings.ignore_bots else "automod.no"),
        )
        if not guild.me.guild_permissions.view_audit_log:
            embed.add_field(name="⚠️", value=t(locale, "logging.status.no_audit"), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    def _problems(self, guild: discord.Guild, event: str, channel_id: int, locale: str) -> str:
        t = translator.t
        found: list[str] = []
        needed = required_intent(event)
        if not self._intent_available(event):
            found.append(t(locale, f"logging.status.need_{needed}"))
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            found.append(t(locale, "logging.status.channel_missing"))
        else:
            perms = channel.permissions_for(guild.me)
            if not (perms.send_messages and perms.embed_links):
                found.append(t(locale, "logging.status.cannot_send"))
            elif event in _NEEDS_FILES and not perms.attach_files:
                found.append(t(locale, "logging.status.cannot_attach"))
        return "; ".join(found)


async def setup(bot: GGBot) -> None:
    await bot.add_cog(EventLogConfigCog(bot))
