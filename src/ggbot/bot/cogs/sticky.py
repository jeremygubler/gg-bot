"""/sticky: Eine Nachricht, die nach neuen Beiträgen wieder ans Ende des Kanals rutscht."""

import contextlib
from datetime import UTC, datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands

from ggbot.bot.checks import ensure_guild
from ggbot.bot.client import GGBot
from ggbot.bot.i18n import d
from ggbot.core.db.models import ScheduledJob
from ggbot.core.errors import UserError
from ggbot.core.i18n import translator
from ggbot.core.logging import get_logger
from ggbot.core.sticky import rules

log = get_logger(__name__)

REPOST_JOB = "sticky.repost"
NEEDS = ("view_channel", "send_messages", "embed_links", "manage_messages")


class StickyCog(commands.Cog):
    def __init__(self, bot: GGBot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.services.scheduler.register(REPOST_JOB, self._repost_job)

    # --- Neuposts ---

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        guild = message.guild
        if guild is None or self.bot.user is None or message.author.id == self.bot.user.id:
            return
        service = self.bot.services.sticky
        if message.channel.id not in await service.channel_ids(guild.id):
            return
        # Viele Nachrichten hintereinander ergeben einen Neupost, nicht einen je Nachricht.
        if not await service.claim_repost(message.channel.id):
            return
        run_at = datetime.now(UTC) + timedelta(seconds=rules.REPOST_DELAY_SECONDS)
        await self.bot.services.scheduler.enqueue(
            guild.id, REPOST_JOB, run_at, {"channel_id": message.channel.id}
        )

    async def _repost_job(self, job: ScheduledJob) -> None:
        channel = self.bot.get_channel(int(job.payload["channel_id"]))
        if not isinstance(channel, discord.TextChannel):
            return  # Kanal gelöscht: nichts mehr zu tun
        await self.repost(channel)

    async def repost(self, channel: discord.TextChannel) -> None:
        service = self.bot.services.sticky
        sticky = await service.get(channel.id)
        if sticky is None:
            return  # inzwischen entfernt
        have = channel.permissions_for(channel.guild.me)
        if not all(getattr(have, name) for name in NEEDS):
            log.warning("sticky_no_permission", guild_id=channel.guild.id, channel_id=channel.id)
            return
        if sticky.last_message_id is not None:
            with contextlib.suppress(discord.NotFound):  # schon weg: auch gut
                await channel.get_partial_message(sticky.last_message_id).delete()
        locale = await self.bot.services.guilds.get_locale(channel.guild.id)
        embed = discord.Embed(description=sticky.text)
        embed.set_footer(text=translator.t(locale, "sticky.footer"))
        sent = await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        await service.set_last(channel.id, sent.id)

    # --- Befehle ---

    sticky = app_commands.Group(
        name="sticky",
        description=d("cmd.sticky.desc"),
        guild_only=True,
        default_permissions=discord.Permissions(manage_messages=True),
    )

    async def _say(self, interaction: discord.Interaction, key: str, **params: object) -> None:
        locale = await self.bot.services.guilds.get_locale(interaction.guild_id or 0)
        await interaction.response.send_message(
            translator.t(locale, key, **params),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @sticky.command(name="set", description=d("cmd.sticky.set.desc"))
    @app_commands.describe(channel=d("cmd.arg.channel.desc"), text=d("cmd.arg.sticky_text.desc"))
    async def set_(
        self, interaction: discord.Interaction, channel: discord.TextChannel, text: str
    ) -> None:
        guild = ensure_guild(interaction)
        have = channel.permissions_for(guild.me)
        missing = [n for n in NEEDS if not getattr(have, n)]
        if missing:
            raise UserError("error.sticky_perms", channel=channel.mention)
        await self.bot.services.sticky.set(guild.id, channel.id, text, interaction.user.id)
        await interaction.response.defer(ephemeral=True)
        await self.repost(channel)
        locale = await self.bot.services.guilds.get_locale(guild.id)
        await interaction.followup.send(
            translator.t(locale, "sticky.set_done", channel=channel.mention),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @sticky.command(name="remove", description=d("cmd.sticky.remove.desc"))
    @app_commands.describe(channel=d("cmd.arg.channel.desc"))
    async def remove(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        guild = ensure_guild(interaction)
        gone = await self.bot.services.sticky.remove(guild.id, channel.id)
        if gone.last_message_id is not None:
            with contextlib.suppress(discord.HTTPException):
                await channel.get_partial_message(gone.last_message_id).delete()
        await self._say(interaction, "sticky.remove_done", channel=channel.mention)

    @sticky.command(name="list", description=d("cmd.sticky.list.desc"))
    async def list_(self, interaction: discord.Interaction) -> None:
        guild = ensure_guild(interaction)
        entries = await self.bot.services.sticky.list(guild.id)
        if not entries:
            await self._say(interaction, "sticky.list_empty")
            return
        lines = [f"<#{e.channel_id}> — {e.text[:80]}" for e in entries]
        await self._say(interaction, "sticky.list", entries="\n".join(lines))
