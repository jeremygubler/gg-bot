"""/ticketpanel: Panels und Kategorien bauen, veröffentlichen, Einstellungen."""

import contextlib
from collections.abc import Callable
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands
from pydantic import ValidationError

from ggbot.bot import tickets as tk
from ggbot.bot.checks import ensure_guild
from ggbot.bot.client import GGBot
from ggbot.bot.i18n import d
from ggbot.core.errors import UserError
from ggbot.core.i18n import translator
from ggbot.core.roles.plan import is_emoji
from ggbot.core.services.tickets import TicketPanelView
from ggbot.core.settings.tickets import TicketSettings
from ggbot.core.tickets.rules import clean_questions

PostChannel = discord.TextChannel
NEEDS = ("view_channel", "send_messages", "embed_links")


class TicketPanelCog(commands.Cog):
    def __init__(self, bot: GGBot) -> None:
        self.bot = bot

    panel = app_commands.Group(
        name="ticketpanel",
        description=d("cmd.ticketpanel.desc"),
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )
    category = app_commands.Group(
        name="category", description=d("cmd.ticketpanel.category.desc"), parent=panel
    )

    async def _locale(self, guild_id: int) -> str:
        return await self.bot.services.guilds.get_locale(guild_id)

    async def _reply(self, interaction: discord.Interaction, key: str, **params: object) -> None:
        locale = await self._locale(interaction.guild_id or 0)
        await interaction.response.send_message(
            translator.t(locale, key, **params),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _panel_choices(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        if interaction.guild_id is None:
            return []
        needle = current.lower()
        return [
            app_commands.Choice(name=f"#{p.id} {p.title}"[:100], value=p.id)
            for p in await self.bot.services.tickets.list_panels(interaction.guild_id)
            if needle in p.title.lower() or needle in str(p.id)
        ][:25]

    async def _category_choices(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild_id is None:
            return []
        panel_id = getattr(interaction.namespace, "panel", None)
        if not isinstance(panel_id, int):
            return []
        try:
            view = await self.bot.services.tickets.get_panel(interaction.guild_id, panel_id)
        except UserError:
            return []
        needle = current.lower()
        return [
            app_commands.Choice(name=c.name[:100], value=c.name)
            for c in view.categories
            if needle in c.name.lower()
        ][:25]

    @staticmethod
    def _check_support_role(role: discord.Role) -> None:
        # @everyone als Support-Rolle machte jedes Ticket für alle sichtbar.
        if role.is_default():
            raise UserError("error.tcat_role_everyone")

    # --- Panels ---

    @panel.command(name="create", description=d("cmd.ticketpanel.create.desc"))
    @app_commands.describe(
        title=d("cmd.arg.panel_title.desc"), description=d("cmd.arg.panel_description.desc")
    )
    async def create(
        self, interaction: discord.Interaction, title: str, description: str | None = None
    ) -> None:
        guild = ensure_guild(interaction)
        view = await self.bot.services.tickets.create_panel(
            guild.id, title=title, description=description
        )
        await self._reply(interaction, "ticket.panel_created", panel_id=view.id, title=view.title)

    @panel.command(name="list", description=d("cmd.ticketpanel.list.desc"))
    async def list_(self, interaction: discord.Interaction) -> None:
        guild = ensure_guild(interaction)
        locale = await self._locale(guild.id)
        found = await self.bot.services.tickets.list_panels(guild.id)
        t = translator.t
        if not found:
            text = t(locale, "ticket.panel_list_empty")
        else:
            lines: list[str] = []
            for p in found:
                where = f"<#{p.channel_id}>" if p.published else t(locale, "ticket.unpublished")
                lines.append(
                    t(locale, "ticket.panel_list_line", id=p.id, title=p.title, where=where)
                )
                lines.extend(
                    f"　{c.emoji + ' ' if c.emoji else ''}{c.name} → "
                    + ", ".join(f"<@&{r}>" for r in c.support_role_ids)
                    for c in p.categories
                )
            text = "\n".join(lines)[:1900]
        await interaction.response.send_message(
            text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )

    @panel.command(name="publish", description=d("cmd.ticketpanel.publish.desc"))
    @app_commands.describe(panel=d("cmd.arg.panel.desc"), channel=d("cmd.arg.channel.desc"))
    async def publish(
        self,
        interaction: discord.Interaction,
        panel: int,
        channel: discord.TextChannel | None = None,
    ) -> None:
        guild = ensure_guild(interaction)
        view = await self.bot.services.tickets.get_panel(guild.id, panel)
        if not view.categories:
            raise UserError("error.tpanel_empty")
        target = channel or (
            interaction.channel if isinstance(interaction.channel, discord.TextChannel) else None
        )
        if target is None:
            raise UserError("error.panel_channel_needed")
        have = target.permissions_for(guild.me)
        missing = [name for name in NEEDS if not getattr(have, name)]
        if missing:
            raise UserError(
                "error.missing_bot_permissions",
                permissions=", ".join(n.replace("_", " ").title() for n in missing),
            )
        # Die Rechte, ohne die ein Ticket später scheitern würde, lieber jetzt melden.
        if not (
            guild.me.guild_permissions.manage_channels and guild.me.guild_permissions.manage_roles
        ):
            raise UserError(
                "error.missing_bot_permissions", permissions="Manage Channels, Manage Roles"
            )
        await interaction.response.defer(ephemeral=True)
        await self._delete_message(guild, view)
        color = await tk.embed_color(self.bot, guild.id)
        message = await target.send(
            embed=tk.panel_embed(view, color),
            view=tk.panel_view(view),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await self.bot.services.tickets.set_panel_message(guild.id, panel, target.id, message.id)
        locale = await self._locale(guild.id)
        await interaction.followup.send(
            translator.t(locale, "ticket.panel_published", panel_id=panel, channel=target.mention),
            ephemeral=True,
        )

    @panel.command(name="delete", description=d("cmd.ticketpanel.delete.desc"))
    @app_commands.describe(panel=d("cmd.arg.panel.desc"))
    async def delete(self, interaction: discord.Interaction, panel: int) -> None:
        guild = ensure_guild(interaction)
        view = await self.bot.services.tickets.delete_panel(guild.id, panel)
        await self._delete_message(guild, view)
        await self._reply(interaction, "ticket.panel_deleted", panel_id=panel, title=view.title)

    async def _message(self, guild: discord.Guild, view: TicketPanelView) -> discord.Message | None:
        if view.channel_id is None or view.message_id is None:
            return None
        channel = guild.get_channel(view.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return None
        try:
            return await channel.fetch_message(view.message_id)
        except (discord.NotFound, discord.Forbidden):
            return None

    async def _delete_message(self, guild: discord.Guild, view: TicketPanelView) -> None:
        message = await self._message(guild, view)
        if message is not None:
            with contextlib.suppress(discord.HTTPException):
                await message.delete()

    async def _refresh(self, guild: discord.Guild, view: TicketPanelView) -> None:
        """Hält die veröffentlichte Nachricht auf dem Stand des Panels."""
        message = await self._message(guild, view)
        if message is None:
            return
        color = await tk.embed_color(self.bot, guild.id)
        with contextlib.suppress(discord.HTTPException):
            await message.edit(embed=tk.panel_embed(view, color), view=tk.panel_view(view))

    # --- Kategorien ---

    @category.command(name="add", description=d("cmd.ticketpanel.category.add.desc"))
    @app_commands.describe(
        panel=d("cmd.arg.panel.desc"),
        name=d("cmd.arg.ticket_category.desc"),
        role=d("cmd.arg.support_role.desc"),
        emoji=d("cmd.arg.panel_emoji.desc"),
        description=d("cmd.arg.panel_item_description.desc"),
        parent=d("cmd.arg.ticket_parent.desc"),
        welcome=d("cmd.arg.ticket_welcome.desc"),
        questions=d("cmd.arg.ticket_questions.desc"),
    )
    async def category_add(
        self,
        interaction: discord.Interaction,
        panel: int,
        name: str,
        role: discord.Role,
        emoji: str | None = None,
        description: str | None = None,
        parent: discord.CategoryChannel | None = None,
        welcome: str | None = None,
        questions: str | None = None,
    ) -> None:
        guild = ensure_guild(interaction)
        self._check_support_role(role)
        if emoji is not None and not is_emoji(emoji):
            raise UserError("error.panel_emoji_invalid", value=emoji[:20])
        try:
            parsed = clean_questions(questions) if questions else []
        except ValueError as exc:
            raise UserError("error.tcat_questions", detail=str(exc)) from exc
        view = await self.bot.services.tickets.add_category(
            guild.id,
            panel,
            name=name,
            support_role_id=role.id,
            emoji=emoji.strip() if emoji else None,
            description=description,
            parent_id=parent.id if parent else None,
            welcome_message=welcome,
            questions=parsed,
        )
        await self._refresh(guild, view)
        await self._reply(interaction, "ticket.category_added", name=name.strip(), panel_id=panel)

    @category.command(name="remove", description=d("cmd.ticketpanel.category.remove.desc"))
    @app_commands.describe(panel=d("cmd.arg.panel.desc"), name=d("cmd.arg.ticket_category.desc"))
    async def category_remove(
        self, interaction: discord.Interaction, panel: int, name: str
    ) -> None:
        guild = ensure_guild(interaction)
        view = await self.bot.services.tickets.remove_category(guild.id, panel, name)
        await self._refresh(guild, view)
        await self._reply(interaction, "ticket.category_removed", name=name, panel_id=panel)

    @category.command(name="role_add", description=d("cmd.ticketpanel.category.role_add.desc"))
    @app_commands.describe(
        panel=d("cmd.arg.panel.desc"),
        name=d("cmd.arg.ticket_category.desc"),
        role=d("cmd.arg.support_role.desc"),
    )
    async def role_add(
        self, interaction: discord.Interaction, panel: int, name: str, role: discord.Role
    ) -> None:
        guild = ensure_guild(interaction)
        self._check_support_role(role)
        await self.bot.services.tickets.change_support_role(
            guild.id, panel, name, role.id, add=True
        )
        await self._reply(interaction, "ticket.role_added", role=role.mention, name=name)

    @category.command(
        name="role_remove", description=d("cmd.ticketpanel.category.role_remove.desc")
    )
    @app_commands.describe(
        panel=d("cmd.arg.panel.desc"),
        name=d("cmd.arg.ticket_category.desc"),
        role=d("cmd.arg.support_role.desc"),
    )
    async def role_remove(
        self, interaction: discord.Interaction, panel: int, name: str, role: discord.Role
    ) -> None:
        guild = ensure_guild(interaction)
        await self.bot.services.tickets.change_support_role(
            guild.id, panel, name, role.id, add=False
        )
        await self._reply(interaction, "ticket.role_removed", role=role.mention, name=name)

    # --- Einstellungen ---

    @panel.command(name="settings", description=d("cmd.ticketpanel.settings.desc"))
    @app_commands.describe(
        log_channel=d("cmd.arg.ticket_log.desc"),
        max_open=d("cmd.arg.ticket_max_open.desc"),
        auto_close_hours=d("cmd.arg.ticket_auto_close.desc"),
        close_delay=d("cmd.arg.ticket_close_delay.desc"),
        feedback=d("cmd.arg.ticket_feedback.desc"),
        dm_transcript=d("cmd.arg.ticket_dm.desc"),
        creators_may_close=d("cmd.arg.ticket_creators_close.desc"),
        ping_support=d("cmd.arg.ticket_ping.desc"),
        retention_days=d("cmd.arg.ticket_retention.desc"),
        name_template=d("cmd.arg.ticket_name.desc"),
    )
    async def settings(
        self,
        interaction: discord.Interaction,
        log_channel: discord.TextChannel | None = None,
        max_open: app_commands.Range[int, 1, 10] | None = None,
        auto_close_hours: app_commands.Range[int, 0, 720] | None = None,
        close_delay: app_commands.Range[int, 0, 300] | None = None,
        feedback: bool | None = None,
        dm_transcript: bool | None = None,
        creators_may_close: bool | None = None,
        ping_support: bool | None = None,
        retention_days: app_commands.Range[int, 0, 3650] | None = None,
        name_template: str | None = None,
    ) -> None:
        changes: dict[str, Any] = {
            "log_channel_id": log_channel.id if log_channel else None,
            "max_open_per_user": max_open,
            "auto_close_hours": auto_close_hours,
            "close_delay_seconds": close_delay,
            "feedback": feedback,
            "dm_transcript": dm_transcript,
            "creators_may_close": creators_may_close,
            "ping_support": ping_support,
            "retention_days": retention_days,
            "name_template": name_template,
        }
        changes = {k: v for k, v in changes.items() if v is not None}
        if not changes:
            raise UserError("error.card_nothing_to_change")
        await self._edit(interaction, lambda data: data.update(changes), "ticket.settings_saved")

    async def _edit(
        self, interaction: discord.Interaction, mutate: Callable[[Any], None], reply_key: str
    ) -> None:
        guild = ensure_guild(interaction)
        services = self.bot.services
        data = (await services.tickets.settings(guild.id)).model_dump()
        mutate(data)
        try:
            updated = TicketSettings.model_validate(data)
        except ValidationError as exc:
            error = exc.errors()[0]
            detail = f"{'.'.join(str(p) for p in error['loc'])}: {error['msg']}"[:150]
            raise UserError("error.ticket_settings_invalid", detail=detail) from exc
        await services.settings.save(guild.id, updated)
        services.tickets.invalidate(guild.id)
        await self._reply(interaction, reply_key)

    @panel.command(name="status", description=d("cmd.ticketpanel.status.desc"))
    async def status(self, interaction: discord.Interaction) -> None:
        guild = ensure_guild(interaction)
        services = self.bot.services
        s = await services.tickets.settings(guild.id)
        locale = await self._locale(guild.id)
        t = translator.t
        on = t(locale, "welcome.on")
        off = t(locale, "welcome.off")
        count, average = await services.tickets.feedback_summary(guild.id)
        text = t(
            locale,
            "ticket.status",
            log=f"<#{s.log_channel_id}>" if s.log_channel_id else t(locale, "welcome.none"),
            max_open=s.max_open_per_user,
            auto=t(locale, "ticket.status_hours", hours=s.auto_close_hours)
            if s.auto_close_hours
            else off,
            delay=s.close_delay_seconds,
            feedback=on if s.feedback else off,
            dm=on if s.dm_transcript else off,
            creators=on if s.creators_may_close else off,
            ping=on if s.ping_support else off,
            retention=t(locale, "ticket.status_days", days=s.retention_days)
            if s.retention_days
            else t(locale, "ticket.status_forever"),
            name=s.name_template,
            open=len(await services.tickets.open_tickets(guild.id)),
            ratings=count,
            average=f"{average:.1f}",
        )
        await interaction.response.send_message(
            text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )

    # Autocomplete für Panel und Kategorie.
    @category_add.autocomplete("panel")
    async def _ac_add(self, i: discord.Interaction, c: str) -> list[app_commands.Choice[int]]:
        return await self._panel_choices(i, c)

    @category_remove.autocomplete("panel")
    async def _ac_remove(self, i: discord.Interaction, c: str) -> list[app_commands.Choice[int]]:
        return await self._panel_choices(i, c)

    @role_add.autocomplete("panel")
    async def _ac_role_add(self, i: discord.Interaction, c: str) -> list[app_commands.Choice[int]]:
        return await self._panel_choices(i, c)

    @role_remove.autocomplete("panel")
    async def _ac_role_remove(
        self, i: discord.Interaction, c: str
    ) -> list[app_commands.Choice[int]]:
        return await self._panel_choices(i, c)

    @publish.autocomplete("panel")
    async def _ac_publish(self, i: discord.Interaction, c: str) -> list[app_commands.Choice[int]]:
        return await self._panel_choices(i, c)

    @delete.autocomplete("panel")
    async def _ac_delete(self, i: discord.Interaction, c: str) -> list[app_commands.Choice[int]]:
        return await self._panel_choices(i, c)

    @category_remove.autocomplete("name")
    async def _ac_cat_remove(
        self, i: discord.Interaction, c: str
    ) -> list[app_commands.Choice[str]]:
        return await self._category_choices(i, c)

    @role_add.autocomplete("name")
    async def _ac_cat_role_add(
        self, i: discord.Interaction, c: str
    ) -> list[app_commands.Choice[str]]:
        return await self._category_choices(i, c)

    @role_remove.autocomplete("name")
    async def _ac_cat_role_remove(
        self, i: discord.Interaction, c: str
    ) -> list[app_commands.Choice[str]]:
        return await self._category_choices(i, c)


async def setup(bot: GGBot) -> None:
    await bot.add_cog(TicketPanelCog(bot))
