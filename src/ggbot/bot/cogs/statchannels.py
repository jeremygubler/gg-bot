"""/statchannel: Sprachkanäle, deren Name eine Zahl des Servers zeigt (z. B. Mitgliederzahl)."""

import asyncio
import contextlib

import discord
from discord import app_commands
from discord.ext import commands

from ggbot.bot.checks import ensure_guild
from ggbot.bot.client import GGBot
from ggbot.bot.i18n import d
from ggbot.core.community import stats as rules
from ggbot.core.errors import UserError
from ggbot.core.i18n import translator
from ggbot.core.logging import get_logger

log = get_logger(__name__)

# Discord erlaubt etwa zwei Umbenennungen je Kanal und zehn Minuten: öfter lohnt nicht.
REFRESH_SECONDS = 600
KIND_CHOICES = [app_commands.Choice(name=k, value=k) for k in rules.KINDS]


class StatChannelCog(commands.Cog):
    def __init__(self, bot: GGBot) -> None:
        self.bot = bot
        self._task: asyncio.Task[None] | None = None

    async def cog_load(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="statchannels")

    async def cog_unload(self) -> None:
        if self._task is not None:
            self._task.cancel()

    async def _loop(self) -> None:
        while True:
            if self.bot.is_ready():
                try:
                    await self.refresh_all()
                except Exception:
                    log.exception("statchannels_failed")
                await asyncio.sleep(REFRESH_SECONDS)
            else:
                await asyncio.sleep(5)

    def snapshot(self, guild: discord.Guild) -> rules.Snapshot:
        # Ohne Members-Intent kennt der Cache nicht alle Mitglieder: die Bot-Zahl wäre falsch.
        bots = (
            sum(1 for m in guild.members if m.bot)
            if self.bot.services.config.enable_members_intent
            else None
        )
        return rules.Snapshot(
            member_count=guild.member_count or 0,
            bots=bots,
            channels=len(guild.channels),
            roles=len(guild.roles) - 1,  # ohne @everyone
            boosts=guild.premium_subscription_count,
        )

    async def refresh_all(self) -> int:
        """Setzt alle Namen auf den aktuellen Stand; benennt nur um, was sich geändert hat."""
        changed = 0
        for guild in self.bot.guilds:
            stats = await self.bot.services.statchannels.list(guild.id)
            if not stats:
                continue
            snap = self.snapshot(guild)
            for stat in stats:
                channel = guild.get_channel(stat.channel_id)
                if not isinstance(channel, discord.VoiceChannel):
                    # Der Kanal wurde gelöscht: den Eintrag aufräumen, sonst bliebe er ewig.
                    with contextlib.suppress(UserError):
                        await self.bot.services.statchannels.remove(guild.id, stat.channel_id)
                    continue
                count = rules.value(stat.kind, snap)
                if count is None:
                    continue
                name = rules.render_name(stat.template, count)
                if channel.name == name:
                    continue
                try:
                    await channel.edit(name=name, reason="Stat-Kanal")
                    changed += 1
                except discord.HTTPException:
                    log.warning(
                        "statchannel_rename_failed", guild_id=guild.id, channel_id=channel.id
                    )
        return changed

    statchannel = app_commands.Group(
        name="statchannel",
        description=d("cmd.statchannel.desc"),
        guild_only=True,
        default_permissions=discord.Permissions(manage_channels=True),
    )

    @statchannel.command(name="add", description=d("cmd.statchannel.add.desc"))
    @app_commands.describe(kind=d("cmd.arg.st_kind.desc"), template=d("cmd.arg.st_template.desc"))
    @app_commands.choices(kind=KIND_CHOICES)
    async def add(
        self,
        interaction: discord.Interaction,
        kind: app_commands.Choice[str],
        template: str | None = None,
    ) -> None:
        guild = ensure_guild(interaction)
        locale = await self.bot.services.guilds.get_locale(guild.id)
        if kind.value in rules.NEEDS_MEMBERS and not self.bot.services.config.enable_members_intent:
            raise UserError("error.st_needs_members")
        pattern = template or translator.t(locale, f"statchannel.default_{kind.value}")
        # Erst prüfen, dann den Kanal anlegen: sonst entstünde und verschwände ein Kanal umsonst.
        try:
            rules.validate_template(pattern)
        except ValueError as exc:
            raise UserError("error.st_template", detail=str(exc)) from exc
        await interaction.response.defer(ephemeral=True)
        count = rules.value(kind.value, self.snapshot(guild)) or 0
        try:
            channel = await guild.create_voice_channel(
                rules.render_name(pattern, count),
                overwrites={guild.default_role: discord.PermissionOverwrite(connect=False)},
                reason="Stat-Kanal",
            )
        except discord.Forbidden as exc:
            raise UserError("error.ticket_create_forbidden") from exc
        except discord.HTTPException as exc:
            raise UserError("error.ticket_create_failed") from exc
        try:
            await self.bot.services.statchannels.add(guild.id, channel.id, kind.value, pattern)
        except Exception:
            with contextlib.suppress(discord.HTTPException):
                await channel.delete(reason="Stat-Kanal konnte nicht angelegt werden")
            raise
        await interaction.followup.send(
            translator.t(locale, "statchannel.added", channel=channel.mention), ephemeral=True
        )

    @statchannel.command(name="remove", description=d("cmd.statchannel.remove.desc"))
    @app_commands.describe(channel=d("cmd.arg.channel.desc"))
    async def remove(self, interaction: discord.Interaction, channel: discord.VoiceChannel) -> None:
        guild = ensure_guild(interaction)
        await self.bot.services.statchannels.remove(guild.id, channel.id)
        locale = await self.bot.services.guilds.get_locale(guild.id)
        await interaction.response.send_message(
            translator.t(locale, "statchannel.removed", channel=channel.mention), ephemeral=True
        )

    @statchannel.command(name="list", description=d("cmd.statchannel.list.desc"))
    async def list_(self, interaction: discord.Interaction) -> None:
        guild = ensure_guild(interaction)
        locale = await self.bot.services.guilds.get_locale(guild.id)
        found = await self.bot.services.statchannels.list(guild.id)
        if not found:
            text = translator.t(locale, "statchannel.list_empty")
        else:
            text = "\n".join(
                translator.t(
                    locale,
                    "statchannel.list_line",
                    channel=f"<#{s.channel_id}>",
                    kind=s.kind,
                    template=s.template,
                )
                for s in found
            )
        await interaction.response.send_message(
            text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )


async def setup(bot: GGBot) -> None:
    await bot.add_cog(StatChannelCog(bot))
