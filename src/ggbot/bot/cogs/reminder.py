"""/remind: Erinnerungen an sich selbst."""

import datetime as dt

import discord
from discord import app_commands
from discord.ext import commands

from ggbot.bot.checks import ensure_guild
from ggbot.bot.client import GGBot
from ggbot.bot.i18n import d
from ggbot.core.db.models import ScheduledJob
from ggbot.core.duration import parse_duration
from ggbot.core.i18n import translator
from ggbot.core.logging import get_logger
from ggbot.core.services.engagement import REMINDER_JOB, ReminderView

log = get_logger(__name__)


class ReminderCog(commands.Cog):
    def __init__(self, bot: GGBot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.services.scheduler.register(REMINDER_JOB, self._job)

    async def _job(self, job: ScheduledJob) -> None:
        guild = self.bot.get_guild(job.guild_id)
        if guild is None:
            raise LookupError(f"Server {job.guild_id} nicht verfügbar")  # Scheduler versucht erneut
        reminder = await self.bot.services.engagement.fire_reminder(
            job.guild_id, int(job.payload["reminder_id"])
        )
        if reminder is None:
            return  # abgebrochen oder schon ausgeliefert
        await self.deliver(guild, reminder)

    async def deliver(self, guild: discord.Guild, reminder: ReminderView) -> None:
        """Im Kanal der Erinnerung, sonst per DM. Gepingt wird nur der Ersteller."""
        locale = await self.bot.services.guilds.get_locale(guild.id)
        text = translator.t(locale, "remind.due", user=f"<@{reminder.user_id}>", text=reminder.text)
        mentions = discord.AllowedMentions(
            everyone=False, roles=False, users=[discord.Object(reminder.user_id)]
        )
        channel = guild.get_channel(reminder.channel_id) if reminder.channel_id else None
        if isinstance(channel, discord.TextChannel):
            try:
                await channel.send(text, allowed_mentions=mentions)
                return
            except discord.HTTPException:
                log.info("reminder_channel_failed", reminder_id=reminder.id)
        try:
            user = self.bot.get_user(reminder.user_id) or await self.bot.fetch_user(
                reminder.user_id
            )
            await user.send(
                translator.t(locale, "remind.due_dm", server=guild.name, text=reminder.text),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            # Weder Kanal noch DM: die Erinnerung gilt als ausgeliefert, mehr geht nicht.
            log.warning("reminder_undeliverable", reminder_id=reminder.id, guild_id=guild.id)

    remind = app_commands.Group(name="remind", description=d("cmd.remind.desc"), guild_only=True)

    @remind.command(name="me", description=d("cmd.remind.me.desc"))
    @app_commands.describe(when=d("cmd.arg.remind_when.desc"), text=d("cmd.arg.remind_text.desc"))
    async def me(
        self, interaction: discord.Interaction, when: str, text: app_commands.Range[str, 1, 500]
    ) -> None:
        guild = ensure_guild(interaction)
        delay = dt.timedelta(seconds=parse_duration(when))
        reminder = await self.bot.services.engagement.create_reminder(
            guild.id, interaction.user.id, interaction.channel_id, text, delay
        )
        locale = await self.bot.services.guilds.get_locale(guild.id)
        await interaction.response.send_message(
            translator.t(
                locale,
                "remind.set",
                id=reminder.id,
                when=f"<t:{int(reminder.due_at.timestamp())}:R>",
            ),
            ephemeral=True,
        )

    @remind.command(name="list", description=d("cmd.remind.list.desc"))
    async def list_(self, interaction: discord.Interaction) -> None:
        guild = ensure_guild(interaction)
        locale = await self.bot.services.guilds.get_locale(guild.id)
        found = await self.bot.services.engagement.pending_reminders(guild.id, interaction.user.id)
        if not found:
            text = translator.t(locale, "remind.list_empty")
        else:
            text = "\n".join(
                translator.t(
                    locale,
                    "remind.list_line",
                    id=r.id,
                    when=f"<t:{int(r.due_at.timestamp())}:R>",
                    text=r.text[:80],
                )
                for r in found
            )[:1900]
        await interaction.response.send_message(
            text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )

    @remind.command(name="cancel", description=d("cmd.remind.cancel.desc"))
    @app_commands.describe(reminder=d("cmd.arg.remind_id.desc"))
    async def cancel(self, interaction: discord.Interaction, reminder: int) -> None:
        guild = ensure_guild(interaction)
        await self.bot.services.engagement.cancel_reminder(guild.id, interaction.user.id, reminder)
        locale = await self.bot.services.guilds.get_locale(guild.id)
        await interaction.response.send_message(
            translator.t(locale, "remind.cancelled", id=reminder), ephemeral=True
        )

    @cancel.autocomplete("reminder")
    async def _ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        if interaction.guild_id is None:
            return []
        found = await self.bot.services.engagement.pending_reminders(
            interaction.guild_id, interaction.user.id
        )
        return [
            app_commands.Choice(name=f"#{r.id} {r.text}"[:100], value=r.id)
            for r in found
            if current.lower() in r.text.lower()
        ][:25]


async def setup(bot: GGBot) -> None:
    await bot.add_cog(ReminderCog(bot))
