"""/ticket: schliessen, übernehmen, Mitglieder hinzufügen; dazu Aufräumen und Auto-Close."""

import asyncio
import contextlib
from datetime import UTC, datetime
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from ggbot.bot import tickets as tk
from ggbot.bot.checks import ensure_guild
from ggbot.bot.client import GGBot
from ggbot.bot.i18n import d
from ggbot.core import events
from ggbot.core.db.models import ScheduledJob
from ggbot.core.errors import UserError
from ggbot.core.i18n import translator
from ggbot.core.logging import get_logger
from ggbot.core.tickets import rules
from ggbot.core.tickets.transcript import render_transcript

log = get_logger(__name__)

SWEEP_SECONDS = 300
PURGE_EVERY = 288  # täglich bei 5 Minuten Takt
Reason = app_commands.Range[str, 1, 300]


class TicketsCog(commands.Cog):
    def __init__(self, bot: GGBot) -> None:
        self.bot = bot
        self._sweeper: asyncio.Task[None] | None = None

    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(tk.OpenButton, tk.TicketControl, tk.FeedbackButton)
        self.bot.services.scheduler.register(tk.DELETE_JOB, self._delete_job)
        self._sweeper = asyncio.create_task(self._sweep_loop(), name="ticket-sweep")

    async def cog_unload(self) -> None:
        self.bot.remove_dynamic_items(tk.OpenButton, tk.TicketControl, tk.FeedbackButton)
        if self._sweeper is not None:
            self._sweeper.cancel()

    @commands.Cog.listener()
    async def on_ggbot_settings_changed(self, event: events.SettingsChanged) -> None:
        if event.module == "tickets":
            self.bot.services.tickets.invalidate(event.guild_id)

    # --- Hintergrund ---

    async def _delete_job(self, job: ScheduledJob) -> None:
        guild = self.bot.get_guild(job.guild_id)
        if guild is None:
            raise LookupError(f"Server {job.guild_id} nicht verfügbar")
        channel_id = int(job.payload["channel_id"])
        ticket = await self.bot.services.tickets.by_channel(job.guild_id, channel_id)
        if ticket is None or ticket.is_open:
            return  # nie einen Kanal löschen, der kein geschlossenes Ticket ist
        channel = guild.get_channel(channel_id)
        if channel is not None:
            with contextlib.suppress(discord.NotFound):
                await channel.delete(reason=f"Ticket #{ticket.number} geschlossen")

    async def _sweep_loop(self) -> None:
        tick = 0
        while True:
            await asyncio.sleep(SWEEP_SECONDS)
            try:
                await self.sweep()
                tick += 1
                if tick % PURGE_EVERY == 0:
                    for guild in self.bot.guilds:
                        await self.bot.services.tickets.purge_transcripts(guild.id)
            except Exception:
                # Ein Fehler in einem Durchgang darf den Takt nicht beenden.
                log.exception("ticket_sweep_failed")

    async def sweep(self, now: datetime | None = None) -> int:
        """Schliesst Tickets ohne Aktivität. Gibt an, wie viele geschlossen wurden."""
        now = now or datetime.now(UTC)
        closed = 0
        for guild in self.bot.guilds:
            settings = await self.bot.services.tickets.settings(guild.id)
            if settings.auto_close_hours <= 0:
                continue
            locale = await self.bot.services.guilds.get_locale(guild.id)
            for ticket in await self.bot.services.tickets.open_tickets(guild.id):
                channel = guild.get_channel(ticket.channel_id)
                if not isinstance(channel, discord.TextChannel):
                    continue  # fehlt der Kanal, schliesst on_guild_channel_delete das Ticket
                last = (
                    rules.snowflake_time(channel.last_message_id)
                    if channel.last_message_id
                    else None
                )
                if not rules.is_inactive(now, ticket.created_at, last, settings.auto_close_hours):
                    continue
                try:
                    await tk.close_ticket(
                        self.bot,
                        guild,
                        ticket,
                        self.bot.user.id if self.bot.user else 0,
                        translator.t(
                            locale, "ticket.auto_close_reason", hours=settings.auto_close_hours
                        ),
                    )
                    closed += 1
                except UserError:
                    log.info("ticket_auto_close_skipped", ticket_id=ticket.id)
        return closed

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        """Ein von Hand gelöschter Ticket-Kanal: das Ticket wird geschlossen, ein Transkript
        gibt es dann nicht mehr (der Verlauf liegt nur auf Discord)."""
        services = self.bot.services
        ticket = await services.tickets.by_channel(channel.guild.id, channel.id)
        if ticket is None or not ticket.is_open:
            return
        locale = await services.guilds.get_locale(channel.guild.id)
        html = render_transcript(
            [],
            title=translator.t(
                locale,
                "ticket.transcript_title",
                number=ticket.number,
                category=ticket.category_name,
            ),
            meta=translator.t(locale, "ticket.channel_deleted"),
            truncated_label="",
            attachment_label="",
            embed_label="",
        )
        with contextlib.suppress(UserError):
            await services.tickets.close(
                channel.guild.id,
                channel.id,
                closed_by=self.bot.user.id if self.bot.user else 0,
                reason=translator.t(locale, "ticket.channel_deleted"),
                html=html,
                message_count=0,
            )

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        if payload.guild_id is not None:
            await self.bot.services.tickets.panel_message_deleted(
                payload.guild_id, payload.message_id
            )

    # --- Befehle ---

    ticket = app_commands.Group(name="ticket", description=d("cmd.ticket.desc"), guild_only=True)

    async def _here(
        self, interaction: discord.Interaction
    ) -> tuple[discord.Guild, discord.Member, Any]:
        guild = ensure_guild(interaction)
        member = interaction.user
        if not isinstance(member, discord.Member):
            raise UserError("error.guild_only")
        ticket = await self.bot.services.tickets.require_channel(
            guild.id, interaction.channel_id or 0
        )
        return guild, member, ticket

    async def _say(self, interaction: discord.Interaction, key: str, **params: object) -> None:
        locale = await self.bot.services.guilds.get_locale(interaction.guild_id)
        await interaction.response.send_message(
            translator.t(locale, key, **params),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @ticket.command(name="close", description=d("cmd.ticket.close.desc"))
    @app_commands.describe(reason=d("cmd.arg.reason.desc"))
    async def close(self, interaction: discord.Interaction, reason: Reason | None = None) -> None:
        await tk.close_from_interaction(interaction, reason)

    @ticket.command(name="claim", description=d("cmd.ticket.claim.desc"))
    async def claim(self, interaction: discord.Interaction) -> None:
        await tk.handle_claim(interaction)

    @ticket.command(name="unclaim", description=d("cmd.ticket.unclaim.desc"))
    async def unclaim(self, interaction: discord.Interaction) -> None:
        guild, member, ticket = await self._here(interaction)
        tk.ensure_support(member, ticket)
        await self.bot.services.tickets.unclaim(
            guild.id, ticket.channel_id, member.id, is_manager=tk.is_manager(member)
        )
        locale = await self.bot.services.guilds.get_locale(guild.id)
        await interaction.response.send_message(
            translator.t(locale, "ticket.unclaimed", user=member.mention),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @ticket.command(name="add", description=d("cmd.ticket.add.desc"))
    @app_commands.describe(member=d("cmd.arg.member.desc"))
    async def add(self, interaction: discord.Interaction, member: discord.Member) -> None:
        _, actor, ticket = await self._here(interaction)
        tk.ensure_support(actor, ticket)
        if not ticket.is_open:
            raise UserError("error.ticket_closed")
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            raise UserError("error.ticket_not_here")
        await channel.set_permissions(
            member,
            overwrite=discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            ),
            reason=f"Ticket #{ticket.number}",
        )
        await self._say(interaction, "ticket.member_added", member=member.mention)

    @ticket.command(name="remove", description=d("cmd.ticket.remove.desc"))
    @app_commands.describe(member=d("cmd.arg.member.desc"))
    async def remove(self, interaction: discord.Interaction, member: discord.Member) -> None:
        _, actor, ticket = await self._here(interaction)
        tk.ensure_support(actor, ticket)
        if member.id == ticket.creator_id:
            raise UserError("error.ticket_remove_creator")
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            raise UserError("error.ticket_not_here")
        await channel.set_permissions(member, overwrite=None, reason=f"Ticket #{ticket.number}")
        await self._say(interaction, "ticket.member_removed", member=member.mention)

    @ticket.command(name="transcript", description=d("cmd.ticket.transcript.desc"))
    @app_commands.describe(number=d("cmd.arg.ticket_number.desc"))
    async def transcript(
        self, interaction: discord.Interaction, number: app_commands.Range[int, 1]
    ) -> None:
        guild = ensure_guild(interaction)
        member = interaction.user
        if not isinstance(member, discord.Member):
            raise UserError("error.guild_only")
        ticket, html = await self.bot.services.tickets.transcript(guild.id, number)
        allowed = member.id == ticket.creator_id or rules.can_manage(
            {r.id for r in member.roles}, ticket.support_role_ids, is_manager=tk.is_manager(member)
        )
        if not allowed:
            # Fremde Tickets sehen nach "gibt es nicht" aus.
            raise UserError("error.ticket_unknown", number=number)
        await interaction.response.send_message(
            file=tk.html_file(html, f"ticket-{ticket.number:04d}.html"), ephemeral=True
        )


async def setup(bot: GGBot) -> None:
    await bot.add_cog(TicketsCog(bot))
