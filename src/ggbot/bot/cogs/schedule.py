"""/schedule: Nachrichten für später oder in festem Rhythmus."""

import datetime as dt

import discord
from discord import app_commands
from discord.ext import commands

from ggbot.bot.checks import ensure_guild
from ggbot.bot.client import GGBot
from ggbot.bot.cogs.customcmd import newlines
from ggbot.bot.i18n import d
from ggbot.core.db.models import ScheduledJob
from ggbot.core.duration import format_duration, parse_duration
from ggbot.core.errors import UserError
from ggbot.core.i18n import translator
from ggbot.core.logging import get_logger
from ggbot.core.services.community import SCHEDULE_JOB, ScheduledView

log = get_logger(__name__)
Text = app_commands.Range[str, 1, 1500]


class ScheduleCog(commands.Cog):
    def __init__(self, bot: GGBot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.services.scheduler.register(SCHEDULE_JOB, self._job)

    async def _job(self, job: ScheduledJob) -> None:
        guild = self.bot.get_guild(job.guild_id)
        if guild is None:
            raise LookupError(f"Server {job.guild_id} nicht verfügbar")
        due = dt.datetime.fromisoformat(str(job.payload["at"]))
        view = await self.bot.services.schedules.claim_run(
            job.guild_id, int(job.payload["id"]), due
        )
        if view is not None:
            await self.send(guild, view)

    async def send(self, guild: discord.Guild, view: ScheduledView) -> None:
        channel = guild.get_channel(view.channel_id)
        if not isinstance(channel, discord.TextChannel):
            log.warning("scheduled_channel_missing", guild_id=guild.id, id=view.id)
            return
        try:
            await channel.send(
                view.text,
                # Eine geplante Nachricht pingt nie, auch nicht mit @everyone im Text.
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            log.warning("scheduled_send_failed", guild_id=guild.id, id=view.id)

    schedule = app_commands.Group(
        name="schedule",
        description=d("cmd.schedule.desc"),
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    def _check_channel(self, guild: discord.Guild, channel: discord.TextChannel) -> None:
        have = channel.permissions_for(guild.me)
        if not (have.view_channel and have.send_messages):
            raise UserError(
                "error.missing_bot_permissions", permissions="View Channel, Send Messages"
            )

    async def _add(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        text: str,
        delay: dt.timedelta,
        interval: dt.timedelta | None,
    ) -> None:
        guild = ensure_guild(interaction)
        self._check_channel(guild, channel)
        view = await self.bot.services.schedules.add(
            guild.id,
            channel_id=channel.id,
            text=newlines(text),
            created_by=interaction.user.id,
            delay=delay,
            interval=interval,
        )
        locale = await self.bot.services.guilds.get_locale(guild.id)
        key = "schedule.added_repeat" if interval else "schedule.added_once"
        await interaction.response.send_message(
            translator.t(
                locale,
                key,
                id=view.id,
                when=f"<t:{int(view.next_run.timestamp())}:R>",
                channel=channel.mention,
            ),
            ephemeral=True,
        )

    @schedule.command(name="once", description=d("cmd.schedule.once.desc"))
    @app_commands.describe(
        when=d("cmd.arg.sm_when.desc"),
        channel=d("cmd.arg.channel.desc"),
        text=d("cmd.arg.sm_text.desc"),
    )
    async def once(
        self, interaction: discord.Interaction, when: str, channel: discord.TextChannel, text: Text
    ) -> None:
        await self._add(
            interaction, channel, text, dt.timedelta(seconds=parse_duration(when)), None
        )

    @schedule.command(name="repeat", description=d("cmd.schedule.repeat.desc"))
    @app_commands.describe(
        every=d("cmd.arg.sm_every.desc"),
        channel=d("cmd.arg.channel.desc"),
        text=d("cmd.arg.sm_text.desc"),
        first=d("cmd.arg.sm_first.desc"),
    )
    async def repeat(
        self,
        interaction: discord.Interaction,
        every: str,
        channel: discord.TextChannel,
        text: Text,
        first: str | None = None,
    ) -> None:
        interval = dt.timedelta(seconds=parse_duration(every))
        delay = dt.timedelta(seconds=parse_duration(first)) if first else interval
        await self._add(interaction, channel, text, delay, interval)

    @schedule.command(name="list", description=d("cmd.schedule.list.desc"))
    async def list_(self, interaction: discord.Interaction) -> None:
        guild = ensure_guild(interaction)
        locale = await self.bot.services.guilds.get_locale(guild.id)
        found = await self.bot.services.schedules.list(guild.id)
        if not found:
            text = translator.t(locale, "schedule.list_empty")
        else:
            text = "\n".join(
                translator.t(
                    locale,
                    "schedule.list_line",
                    id=m.id,
                    when=f"<t:{int(m.next_run.timestamp())}:R>",
                    channel=f"<#{m.channel_id}>",
                    every=translator.t(locale, "schedule.once_label")
                    if m.interval_seconds is None
                    else format_duration(m.interval_seconds),
                    text=m.text[:60].replace("\n", " "),
                )
                for m in found
            )[:1900]
        await interaction.response.send_message(
            text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )

    @schedule.command(name="remove", description=d("cmd.schedule.remove.desc"))
    @app_commands.describe(message=d("cmd.arg.sm_id.desc"))
    async def remove(self, interaction: discord.Interaction, message: int) -> None:
        guild = ensure_guild(interaction)
        await self.bot.services.schedules.cancel(guild.id, message)
        locale = await self.bot.services.guilds.get_locale(guild.id)
        await interaction.response.send_message(
            translator.t(locale, "schedule.removed", id=message), ephemeral=True
        )

    @remove.autocomplete("message")
    async def _ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        if interaction.guild_id is None:
            return []
        found = await self.bot.services.schedules.list(interaction.guild_id)
        return [
            app_commands.Choice(name=f"#{m.id} {m.text}"[:100], value=m.id)
            for m in found
            if current.lower() in m.text.lower()
        ][:25]


async def setup(bot: GGBot) -> None:
    await bot.add_cog(ScheduleCog(bot))
