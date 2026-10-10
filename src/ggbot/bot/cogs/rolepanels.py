"""/rolepanel: Rollen-Panels mit Buttons, Auswahlmenü oder Reaktionen."""

import asyncio
import contextlib
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from ggbot.bot import panels
from ggbot.bot.checks import ensure_grantable, ensure_guild
from ggbot.bot.client import GGBot
from ggbot.bot.i18n import d
from ggbot.core.errors import UserError
from ggbot.core.i18n import translator
from ggbot.core.logging import get_logger
from ggbot.core.roles.plan import (
    PanelMode,
    emoji_key,
    is_emoji,
    plan_reaction_added,
    plan_reaction_removed,
)
from ggbot.core.services.rolepanels import PanelView

log = get_logger(__name__)

KIND_CHOICES = [app_commands.Choice(name=k, value=k) for k in ("button", "select", "reaction")]
MODE_CHOICES = [app_commands.Choice(name=m, value=m) for m in ("normal", "unique", "verify")]
Messageable = discord.TextChannel | discord.VoiceChannel | discord.StageChannel | discord.Thread
CHANNEL_NEEDS = ("view_channel", "send_messages", "embed_links")
REACTION_PAUSE = 0.3  # Discord erlaubt nur wenige Reaktionen pro Sekunde


class RolePanelsCog(commands.Cog):
    def __init__(self, bot: GGBot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(panels.RoleButton, panels.RoleSelect)

    async def cog_unload(self) -> None:
        self.bot.remove_dynamic_items(panels.RoleButton, panels.RoleSelect)

    rolepanel = app_commands.Group(
        name="rolepanel",
        description=d("cmd.rolepanel.desc"),
        guild_only=True,
        default_permissions=discord.Permissions(manage_roles=True),
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
        found = await self.bot.services.rolepanels.list(interaction.guild_id)
        needle = current.lower()
        return [
            app_commands.Choice(name=f"#{p.id} {p.title}"[:100], value=p.id)
            for p in found
            if needle in p.title.lower() or needle in str(p.id)
        ][:25]

    # --- Anlegen und Bearbeiten ---

    @rolepanel.command(name="create", description=d("cmd.rolepanel.create.desc"))
    @app_commands.describe(
        title=d("cmd.arg.panel_title.desc"),
        kind=d("cmd.arg.panel_kind.desc"),
        mode=d("cmd.arg.panel_mode.desc"),
        description=d("cmd.arg.panel_description.desc"),
        message_id=d("cmd.arg.panel_message_id.desc"),
        channel=d("cmd.arg.channel.desc"),
    )
    @app_commands.choices(kind=KIND_CHOICES, mode=MODE_CHOICES)
    async def create(
        self,
        interaction: discord.Interaction,
        title: str,
        kind: app_commands.Choice[str],
        mode: app_commands.Choice[str] | None = None,
        description: str | None = None,
        message_id: str | None = None,
        channel: discord.TextChannel | None = None,
    ) -> None:
        guild = ensure_guild(interaction)
        external_id: int | None = None
        if message_id is not None:
            # Vorhandene Nachricht übernehmen: nur bei Reaktions-Panels, und nur wenn sie existiert.
            if not message_id.isdigit():
                raise UserError("error.panel_message_invalid")
            external_id = int(message_id)
            if channel is None:
                raise UserError("error.panel_message_needs_channel")
            try:
                await channel.fetch_message(external_id)
            except discord.NotFound as exc:
                raise UserError("error.panel_message_not_found") from exc
        panel = await self.bot.services.rolepanels.create(
            guild.id,
            kind=kind.value,  # type: ignore[arg-type]
            mode=mode.value if mode else "normal",
            title=title,
            description=description,
            channel_id=channel.id if channel else None,
            message_id=external_id,
        )
        await self._reply(interaction, "roles.created", panel_id=panel.id, title=panel.title)

    @rolepanel.command(name="add", description=d("cmd.rolepanel.add.desc"))
    @app_commands.describe(
        panel=d("cmd.arg.panel.desc"),
        role=d("cmd.arg.role.desc"),
        label=d("cmd.arg.panel_label.desc"),
        emoji=d("cmd.arg.panel_emoji.desc"),
        description=d("cmd.arg.panel_item_description.desc"),
    )
    async def add(
        self,
        interaction: discord.Interaction,
        panel: int,
        role: discord.Role,
        label: str | None = None,
        emoji: str | None = None,
        description: str | None = None,
    ) -> None:
        guild = ensure_guild(interaction)
        ensure_grantable(interaction, role)
        if emoji is not None:
            self._check_emoji(emoji)
        view = await self.bot.services.rolepanels.add_item(
            guild.id, panel, role_id=role.id, label=label, emoji=emoji, description=description
        )
        await self._refresh(guild, view)
        await self._reply(interaction, "roles.item_added", role=role.mention, panel_id=panel)

    @staticmethod
    def _check_emoji(emoji: str) -> None:
        if not is_emoji(emoji):
            raise UserError("error.panel_emoji_invalid", value=emoji[:20])

    @rolepanel.command(name="remove", description=d("cmd.rolepanel.remove.desc"))
    @app_commands.describe(panel=d("cmd.arg.panel.desc"), role=d("cmd.arg.role.desc"))
    async def remove(
        self, interaction: discord.Interaction, panel: int, role: discord.Role
    ) -> None:
        guild = ensure_guild(interaction)
        view, removed = await self.bot.services.rolepanels.remove_item(guild.id, panel, role.id)
        await self._refresh(guild, view)
        if view.kind == "reaction" and removed.emoji and view.published:
            await self._drop_reaction(guild, view, removed.emoji)
        await self._reply(interaction, "roles.item_removed", role=role.mention, panel_id=panel)

    @rolepanel.command(name="list", description=d("cmd.rolepanel.list.desc"))
    async def list_(self, interaction: discord.Interaction) -> None:
        guild = ensure_guild(interaction)
        locale = await self._locale(guild.id)
        found = await self.bot.services.rolepanels.list(guild.id)
        t = translator.t
        if not found:
            text = t(locale, "roles.list_empty")
        else:
            lines = [
                t(
                    locale,
                    "roles.list_line",
                    id=p.id,
                    title=p.title,
                    kind=p.kind,
                    mode=p.mode,
                    items=len(p.items),
                    where=(
                        f"<#{p.channel_id}>" if p.published else t(locale, "roles.list_unpublished")
                    ),
                )
                for p in found
            ]
            text = "\n".join(lines)[:1900]
        await interaction.response.send_message(
            text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )

    @rolepanel.command(name="publish", description=d("cmd.rolepanel.publish.desc"))
    @app_commands.describe(panel=d("cmd.arg.panel.desc"), channel=d("cmd.arg.channel.desc"))
    async def publish(
        self,
        interaction: discord.Interaction,
        panel: int,
        channel: discord.TextChannel | None = None,
    ) -> None:
        guild = ensure_guild(interaction)
        locale = await self._locale(guild.id)
        view = await self.bot.services.rolepanels.get(guild.id, panel)
        if view.external:
            raise UserError("error.panel_external_publish")
        if not view.items:
            raise UserError("error.panel_empty")
        target = channel or (
            interaction.channel if isinstance(interaction.channel, discord.TextChannel) else None
        )
        if target is None:
            raise UserError("error.panel_channel_needed")
        have = target.permissions_for(guild.me)
        needs = [*CHANNEL_NEEDS, "add_reactions"] if view.kind == "reaction" else CHANNEL_NEEDS
        missing = [name for name in needs if not getattr(have, name)]
        if missing:
            raise UserError(
                "error.missing_bot_permissions",
                permissions=", ".join(n.replace("_", " ").title() for n in missing),
            )
        await interaction.response.defer(ephemeral=True)
        # Eine zweite Veröffentlichung ersetzt die erste, damit nie zwei Nachrichten gelten.
        await self._delete_message(guild, view)
        message = await target.send(
            embed=panels.build_embed(view, locale),
            view=panels.build_view(view, locale) or discord.utils.MISSING,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await self.bot.services.rolepanels.set_message(guild.id, panel, target.id, message.id)
        text = translator.t(locale, "roles.published", panel_id=panel, channel=target.mention)
        if view.kind == "reaction":
            failed = await self._seed_reactions(message, view)
            if failed:
                text += "\n" + translator.t(
                    locale, "roles.reaction_failed", emojis=" ".join(failed)
                )
        await interaction.followup.send(text, ephemeral=True)

    @rolepanel.command(name="delete", description=d("cmd.rolepanel.delete.desc"))
    @app_commands.describe(panel=d("cmd.arg.panel.desc"))
    async def delete(self, interaction: discord.Interaction, panel: int) -> None:
        guild = ensure_guild(interaction)
        view = await self.bot.services.rolepanels.delete(guild.id, panel)
        if not view.external:
            await self._delete_message(guild, view)
        await self._reply(interaction, "roles.deleted", panel_id=panel, title=view.title)

    @add.autocomplete("panel")
    async def _ac_add(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return await self._panel_choices(interaction, current)

    @remove.autocomplete("panel")
    async def _ac_remove(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return await self._panel_choices(interaction, current)

    @publish.autocomplete("panel")
    async def _ac_publish(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return await self._panel_choices(interaction, current)

    @delete.autocomplete("panel")
    async def _ac_delete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return await self._panel_choices(interaction, current)

    # --- Nachrichten pflegen ---

    async def _fetch_message(self, guild: discord.Guild, view: PanelView) -> discord.Message | None:
        if view.channel_id is None or view.message_id is None:
            return None
        channel = guild.get_channel_or_thread(view.channel_id)
        if not isinstance(channel, Messageable):
            return None
        try:
            return await channel.fetch_message(view.message_id)
        except (discord.NotFound, discord.Forbidden):
            return None

    async def _delete_message(self, guild: discord.Guild, view: PanelView) -> None:
        message = await self._fetch_message(guild, view)
        if message is not None:
            with contextlib.suppress(discord.HTTPException):
                await message.delete()

    async def _refresh(self, guild: discord.Guild, view: PanelView) -> None:
        """Hält die veröffentlichte Nachricht auf dem Stand des Panels."""
        if not view.published or view.external:
            return
        message = await self._fetch_message(guild, view)
        if message is None:
            return
        locale = await self._locale(guild.id)
        try:
            await message.edit(
                embed=panels.build_embed(view, locale),
                view=panels.build_view(view, locale),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            if view.kind == "reaction":
                await self._seed_reactions(message, view)
        except discord.HTTPException:
            log.warning("rolepanel_refresh_failed", guild_id=guild.id, panel_id=view.id)

    async def _seed_reactions(self, message: discord.Message, view: PanelView) -> list[str]:
        """Setzt die Reaktionen; gibt die Emojis zurück, die Discord abgelehnt hat (z. B. eigene
        Emojis eines Servers, die der Bot nicht nutzen darf)."""
        failed: list[str] = []
        for item in view.items:
            if item.emoji:
                try:
                    await message.add_reaction(discord.PartialEmoji.from_str(item.emoji))
                except discord.HTTPException:
                    log.warning("rolepanel_reaction_failed", panel_id=view.id, emoji=item.emoji)
                    failed.append(item.emoji)
                await asyncio.sleep(REACTION_PAUSE)
        return failed

    async def _drop_reaction(self, guild: discord.Guild, view: PanelView, emoji: str) -> None:
        message = await self._fetch_message(guild, view)
        if message is not None:
            with contextlib.suppress(discord.HTTPException):
                await message.clear_reaction(discord.PartialEmoji.from_str(emoji))

    # --- Reaktionen ---

    async def _reaction_context(
        self, payload: discord.RawReactionActionEvent
    ) -> tuple[discord.Guild, discord.Member, PanelView, Any] | None:
        own = self.bot.user.id if self.bot.user else None
        if payload.guild_id is None or payload.user_id == own:
            return None
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return None
        panel = await self.bot.services.rolepanels.panel_for_message(guild.id, payload.message_id)
        if panel is None:
            return None
        item = panel.item_by_emoji(emoji_key(str(payload.emoji)))
        if item is None:
            return None
        member = payload.member or guild.get_member(payload.user_id)
        if member is None:
            # Ohne Members-Intent kennt der Cache das Mitglied oft nicht (v. a. beim Entfernen
            # einer Reaktion, dort liefert Discord kein Mitglied mit): dann einmal nachfragen.
            try:
                member = await guild.fetch_member(payload.user_id)
            except discord.HTTPException:
                return None  # nicht mehr auf dem Server
        if member.bot:
            return None
        return guild, member, panel, item

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        context = await self._reaction_context(payload)
        if context is None:
            return
        guild, member, panel, item = context
        mode: PanelMode = panel.mode  # type: ignore[assignment]
        change = plan_reaction_added(
            mode, item.role_id, panel.role_ids, {r.id for r in member.roles}
        )
        try:
            await panels.apply_change(guild, member, change, f"Rollen-Panel {panel.id}")
        except UserError:
            return  # dem Bot fehlen Rechte: in `apply_change` protokolliert
        if mode == "unique":
            await self._clear_other_reactions(guild, panel, payload)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        context = await self._reaction_context(payload)
        if context is None:
            return
        guild, member, panel, item = context
        mode: PanelMode = panel.mode  # type: ignore[assignment]
        change = plan_reaction_removed(mode, item.role_id, {r.id for r in member.roles})
        with contextlib.suppress(UserError):
            await panels.apply_change(guild, member, change, f"Rollen-Panel {panel.id}")

    async def _clear_other_reactions(
        self, guild: discord.Guild, panel: PanelView, payload: discord.RawReactionActionEvent
    ) -> None:
        """Bei "unique" soll die Reaktion zur Rolle passen: die übrigen des Mitglieds fallen weg."""
        message = await self._fetch_message(guild, panel)
        member = payload.member or guild.get_member(payload.user_id)
        if message is None or member is None:
            return
        for reaction in message.reactions:
            if emoji_key(str(reaction.emoji)) != emoji_key(str(payload.emoji)):
                with contextlib.suppress(discord.HTTPException):
                    await message.remove_reaction(reaction.emoji, member)

    # --- Aufräumen ---

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        if payload.guild_id is None:
            return
        await self.bot.services.rolepanels.message_deleted(payload.guild_id, payload.message_id)

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent) -> None:
        if payload.guild_id is None:
            return
        for message_id in payload.message_ids:
            await self.bot.services.rolepanels.message_deleted(payload.guild_id, message_id)


async def setup(bot: GGBot) -> None:
    await bot.add_cog(RolePanelsCog(bot))
