"""/giveaway: Verlosungen mit Teilnahme-Button, Ziehung per Scheduler."""

import contextlib
import datetime as dt
import random
import re
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from ggbot.bot.checks import ensure_guild
from ggbot.bot.client import GGBot
from ggbot.bot.i18n import d
from ggbot.bot.panels import component_error
from ggbot.bot.tickets import embed_color
from ggbot.core.db.models import ScheduledJob
from ggbot.core.duration import parse_duration
from ggbot.core.errors import UserError
from ggbot.core.giveaway.rules import MAX_WINNERS, may_enter, pick_winners
from ggbot.core.i18n import translator
from ggbot.core.logging import get_logger
from ggbot.core.services.engagement import GIVEAWAY_JOB, GiveawayView

log = get_logger(__name__)

BUTTON_TEMPLATE = r"gg:gw:(?P<id>\d+)"
MAX_CHECKS = 200  # so viele Kandidaten werden höchstens auf Mitgliedschaft geprüft
_RNG = random.SystemRandom()


def button_id(giveaway_id: int) -> str:
    return f"gg:gw:{giveaway_id}"


class JoinButton(discord.ui.DynamicItem[discord.ui.Button[Any]], template=BUTTON_TEMPLATE):
    def __init__(
        self, giveaway_id: int, *, label: str = "Teilnehmen", disabled: bool = False
    ) -> None:
        super().__init__(
            discord.ui.Button(
                style=discord.ButtonStyle.primary,
                label=label,
                emoji="🎉",
                custom_id=button_id(giveaway_id),
                disabled=disabled,
            )
        )
        self.giveaway_id = giveaway_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str], /
    ) -> "JoinButton":
        return cls(int(match["id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        bot: Any = interaction.client
        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            raise UserError("error.guild_only")
        if member.bot:
            raise UserError("error.gw_bots")
        service = bot.services.engagement
        giveaway = await service.get_giveaway(guild.id, self.giveaway_id)
        if not may_enter(giveaway.required_role_id, {r.id for r in member.roles}):
            raise UserError("error.gw_role", role=f"<@&{giveaway.required_role_id}>")
        joined, count = await service.toggle_entry(guild.id, self.giveaway_id, member.id)
        locale = await bot.services.guilds.get_locale(guild.id)
        key = "giveaway.joined" if joined else "giveaway.left"
        await interaction.response.send_message(
            translator.t(locale, key, count=count), ephemeral=True
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await component_error(interaction, error)


class GiveawayCog(commands.Cog):
    def __init__(self, bot: GGBot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(JoinButton)
        self.bot.services.scheduler.register(GIVEAWAY_JOB, self._job)

    async def cog_unload(self) -> None:
        self.bot.remove_dynamic_items(JoinButton)

    async def _job(self, job: ScheduledJob) -> None:
        guild = self.bot.get_guild(job.guild_id)
        if guild is None:
            raise LookupError(f"Server {job.guild_id} nicht verfügbar")
        try:
            await self.finish(guild, int(job.payload["giveaway_id"]))
        except UserError:
            return  # schon beendet oder abgebrochen: nichts mehr zu tun

    # --- Darstellung ---

    async def _embed(
        self, giveaway: GiveawayView, locale: str, *, winners: list[int] | None = None
    ) -> discord.Embed:
        t = translator.t
        embed = discord.Embed(
            title=f"🎉 {giveaway.prize}",
            color=await embed_color(self.bot, giveaway.guild_id),
        )
        ends = f"<t:{int(giveaway.ends_at.timestamp())}:R>"
        if winners is None:
            embed.description = t(
                locale,
                "giveaway.body",
                ends=ends,
                host=f"<@{giveaway.host_id}>",
                winners=giveaway.winner_count,
            )
            if giveaway.required_role_id:
                embed.add_field(
                    name=t(locale, "giveaway.requires"), value=f"<@&{giveaway.required_role_id}>"
                )
        else:
            embed.description = t(
                locale,
                "giveaway.result",
                winners=", ".join(f"<@{w}>" for w in winners) or t(locale, "giveaway.nobody"),
            )
            embed.color = 0x4F545C
            embed.set_footer(text=t(locale, "giveaway.ended"))
        return embed

    # --- Ziehung ---

    async def _is_valid(self, guild: discord.Guild, user_id: int, giveaway: GiveawayView) -> bool:
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except discord.HTTPException:
                return False  # nicht mehr auf dem Server
        return not member.bot and may_enter(giveaway.required_role_id, {r.id for r in member.roles})

    async def _draw(
        self,
        guild: discord.Guild,
        giveaway: GiveawayView,
        entrants: list[int],
        count: int,
        exclude: list[int],
    ) -> list[int]:
        """Zufällige Reihenfolge der Teilnehmer, dann der Reihe nach prüfen, bis genug da sind:
        wer gegangen ist oder die Rolle verloren hat, gewinnt nicht."""
        order = pick_winners(entrants, len(entrants), exclude=exclude, sample=_RNG.sample)
        winners: list[int] = []
        for user_id in order[:MAX_CHECKS]:
            if len(winners) >= count:
                break
            if await self._is_valid(guild, user_id, giveaway):
                winners.append(user_id)
        return winners

    def _channel(self, guild: discord.Guild, giveaway: GiveawayView) -> discord.TextChannel | None:
        channel = guild.get_channel(giveaway.channel_id)
        return channel if isinstance(channel, discord.TextChannel) else None

    async def finish(self, guild: discord.Guild, giveaway_id: int) -> list[int]:
        service = self.bot.services.engagement
        giveaway, entrants = await service.end_giveaway(guild.id, giveaway_id)
        winners = await self._draw(guild, giveaway, entrants, giveaway.winner_count, [])
        await service.set_winners(guild.id, giveaway_id, winners)
        locale = await self.bot.services.guilds.get_locale(guild.id)
        await self._announce(guild, giveaway, winners, locale, ended=True)
        return winners

    async def _announce(
        self,
        guild: discord.Guild,
        giveaway: GiveawayView,
        winners: list[int],
        locale: str,
        *,
        ended: bool,
    ) -> None:
        channel = self._channel(guild, giveaway)
        if channel is None:
            return
        if ended and giveaway.message_id:
            with contextlib.suppress(discord.HTTPException):
                message = await channel.fetch_message(giveaway.message_id)
                view = discord.ui.View(timeout=None)
                view.add_item(JoinButton(giveaway.id, disabled=True))
                await message.edit(
                    embed=await self._embed(giveaway, locale, winners=winners), view=view
                )
        key = "giveaway.congrats" if winners else "giveaway.no_winner"
        text = translator.t(
            locale,
            key,
            winners=", ".join(f"<@{w}>" for w in winners),
            prize=giveaway.prize,
        )
        with contextlib.suppress(discord.HTTPException):
            await channel.send(
                text,
                allowed_mentions=discord.AllowedMentions(
                    everyone=False, roles=False, users=[discord.Object(w) for w in winners]
                ),
                reference=discord.MessageReference(
                    message_id=giveaway.message_id, channel_id=channel.id, fail_if_not_exists=False
                )
                if giveaway.message_id
                else discord.utils.MISSING,
            )

    # --- Befehle ---

    giveaway = app_commands.Group(
        name="giveaway",
        description=d("cmd.giveaway.desc"),
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    async def _id_choices(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        if interaction.guild_id is None:
            return []
        found = await self.bot.services.engagement.active_giveaways(interaction.guild_id)
        return [
            app_commands.Choice(name=f"#{g.id} {g.prize}"[:100], value=g.id)
            for g in found
            if current.lower() in g.prize.lower()
        ][:25]

    @giveaway.command(name="start", description=d("cmd.giveaway.start.desc"))
    @app_commands.describe(
        prize=d("cmd.arg.gw_prize.desc"),
        duration=d("cmd.arg.gw_duration.desc"),
        winners=d("cmd.arg.gw_winners.desc"),
        channel=d("cmd.arg.channel.desc"),
        role=d("cmd.arg.gw_role.desc"),
    )
    async def start(
        self,
        interaction: discord.Interaction,
        prize: app_commands.Range[str, 1, 200],
        duration: str,
        winners: app_commands.Range[int, 1, MAX_WINNERS] = 1,
        channel: discord.TextChannel | None = None,
        role: discord.Role | None = None,
    ) -> None:
        guild = ensure_guild(interaction)
        target = channel or (
            interaction.channel if isinstance(interaction.channel, discord.TextChannel) else None
        )
        if target is None:
            raise UserError("error.panel_channel_needed")
        have = target.permissions_for(guild.me)
        missing = [
            n for n in ("view_channel", "send_messages", "embed_links") if not getattr(have, n)
        ]
        if missing:
            raise UserError(
                "error.missing_bot_permissions",
                permissions=", ".join(n.replace("_", " ").title() for n in missing),
            )
        if role is not None and role.is_default():
            raise UserError("error.notify_role_everyone")
        service = self.bot.services.engagement
        view = await service.create_giveaway(
            guild.id,
            channel_id=target.id,
            prize=prize,
            winner_count=winners,
            host_id=interaction.user.id,
            duration=dt.timedelta(seconds=parse_duration(duration)),
            required_role_id=role.id if role else None,
        )
        locale = await self.bot.services.guilds.get_locale(guild.id)
        await interaction.response.defer(ephemeral=True)
        buttons = discord.ui.View(timeout=None)
        buttons.add_item(JoinButton(view.id, label=translator.t(locale, "giveaway.btn")))
        message = await target.send(
            embed=await self._embed(view, locale),
            view=buttons,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await service.set_giveaway_message(guild.id, view.id, message.id)
        await interaction.followup.send(
            translator.t(locale, "giveaway.started", id=view.id, channel=target.mention),
            ephemeral=True,
        )

    @giveaway.command(name="end", description=d("cmd.giveaway.end.desc"))
    @app_commands.describe(giveaway_id=d("cmd.arg.gw_id.desc"))
    async def end(self, interaction: discord.Interaction, giveaway_id: int) -> None:
        guild = ensure_guild(interaction)
        await interaction.response.defer(ephemeral=True)
        winners = await self.finish(guild, giveaway_id)
        locale = await self.bot.services.guilds.get_locale(guild.id)
        await interaction.followup.send(
            translator.t(locale, "giveaway.ended_now", count=len(winners)), ephemeral=True
        )

    @giveaway.command(name="reroll", description=d("cmd.giveaway.reroll.desc"))
    @app_commands.describe(
        giveaway_id=d("cmd.arg.gw_id.desc"), count=d("cmd.arg.gw_reroll_count.desc")
    )
    async def reroll(
        self,
        interaction: discord.Interaction,
        giveaway_id: int,
        count: app_commands.Range[int, 1, MAX_WINNERS] = 1,
    ) -> None:
        guild = ensure_guild(interaction)
        service = self.bot.services.engagement
        giveaway, entrants = await service.entrants_for_reroll(guild.id, giveaway_id)
        await interaction.response.defer(ephemeral=True)
        new = await self._draw(guild, giveaway, entrants, count, list(giveaway.winners))
        locale = await self.bot.services.guilds.get_locale(guild.id)
        if not new:
            await interaction.followup.send(
                translator.t(locale, "giveaway.reroll_none"), ephemeral=True
            )
            return
        await service.set_winners(guild.id, giveaway_id, [*giveaway.winners, *new])
        await self._announce(guild, giveaway, new, locale, ended=False)
        await interaction.followup.send(
            translator.t(locale, "giveaway.rerolled", count=len(new)), ephemeral=True
        )

    @giveaway.command(name="cancel", description=d("cmd.giveaway.cancel.desc"))
    @app_commands.describe(giveaway_id=d("cmd.arg.gw_id.desc"))
    async def cancel(self, interaction: discord.Interaction, giveaway_id: int) -> None:
        guild = ensure_guild(interaction)
        view = await self.bot.services.engagement.delete_giveaway(guild.id, giveaway_id)
        channel = self._channel(guild, view)
        if channel is not None and view.message_id:
            with contextlib.suppress(discord.HTTPException):
                await channel.get_partial_message(view.message_id).delete()
        locale = await self.bot.services.guilds.get_locale(guild.id)
        await interaction.response.send_message(
            translator.t(locale, "giveaway.cancelled", id=giveaway_id), ephemeral=True
        )

    @giveaway.command(name="list", description=d("cmd.giveaway.list.desc"))
    async def list_(self, interaction: discord.Interaction) -> None:
        guild = ensure_guild(interaction)
        locale = await self.bot.services.guilds.get_locale(guild.id)
        found = await self.bot.services.engagement.active_giveaways(guild.id)
        if not found:
            text = translator.t(locale, "giveaway.list_empty")
        else:
            text = "\n".join(
                translator.t(
                    locale,
                    "giveaway.list_line",
                    id=g.id,
                    prize=g.prize,
                    ends=f"<t:{int(g.ends_at.timestamp())}:R>",
                    channel=f"<#{g.channel_id}>",
                )
                for g in found
            )[:1900]
        await interaction.response.send_message(
            text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )

    @end.autocomplete("giveaway_id")
    async def _ac_end(self, i: discord.Interaction, c: str) -> list[app_commands.Choice[int]]:
        return await self._id_choices(i, c)

    @cancel.autocomplete("giveaway_id")
    async def _ac_cancel(self, i: discord.Interaction, c: str) -> list[app_commands.Choice[int]]:
        return await self._id_choices(i, c)


async def setup(bot: GGBot) -> None:
    await bot.add_cog(GiveawayCog(bot))
