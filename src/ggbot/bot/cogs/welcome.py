"""Willkommen, Abschied, Beitritts-DM und Autorollen."""

import asyncio
import io
from collections.abc import Callable
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands
from pydantic import ValidationError

from ggbot.bot.checks import ensure_grantable, ensure_guild
from ggbot.bot.client import GGBot
from ggbot.bot.cogs.leveling import TEMPLATE_CHOICES, parse_color
from ggbot.bot.i18n import d
from ggbot.bot.panels import apply_change
from ggbot.core import events, messages
from ggbot.core.errors import UserError
from ggbot.core.i18n import translator
from ggbot.core.logging import get_logger
from ggbot.core.roles.plan import Change
from ggbot.core.settings.welcome import DM_FIELDS, JOIN_FIELDS, LEAVE_FIELDS, WelcomeSettings
from ggbot.core.welcome.rules import plan_join, plan_leave
from ggbot.render.welcome_card import WelcomeCardData, render_welcome_card

log = get_logger(__name__)

# Wer Sekunden nach dem Beitritt wieder weg ist (Raider, Fehlklick), wird nicht begrüsst.
SETTLE_SECONDS = 2.0


class WelcomeCog(commands.Cog):
    def __init__(self, bot: GGBot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_ggbot_settings_changed(self, event: events.SettingsChanged) -> None:
        if event.module == "welcome":
            self.bot.services.welcome.invalidate(event.guild_id)

    # --- Ereignisse ---

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        await self._joined(member)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        # Das Mitglieder-Screening ist bestanden: jetzt gilt der Beitritt.
        if before.pending and not after.pending:
            await self._joined(after)

    @commands.Cog.listener()
    async def on_raw_member_remove(self, payload: discord.RawMemberRemoveEvent) -> None:
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        user = payload.user
        pending = isinstance(user, discord.Member) and user.pending
        services = self.bot.services
        settings = await services.welcome.settings(guild.id)
        raid = await services.automod.raid_active(guild.id)
        if not plan_leave(settings, is_bot=user.bot, pending=pending, raid=raid):
            return
        if not await services.welcome.claim(guild.id, user.id, "leave"):
            return
        channel = guild.get_channel(settings.leave_channel_id or 0)
        if not isinstance(channel, discord.TextChannel) or not await services.welcome.throttle(
            guild.id
        ):
            return
        locale = await services.guilds.get_locale(guild.id)
        template = settings.leave_message or translator.t(locale, "welcome.default_leave")
        text = messages.render_template(
            template,
            LEAVE_FIELDS,
            name=user.display_name,
            server=guild.name,
            count=guild.member_count or 0,
        )
        await self._send(channel, text, discord.AllowedMentions.none())

    async def _joined(self, member: discord.Member) -> None:
        guild = member.guild
        services = self.bot.services
        settings = await services.welcome.settings(guild.id)
        raid = await services.automod.raid_active(guild.id)
        plan = plan_join(settings, is_bot=member.bot, pending=member.pending, raid=raid)
        if plan.nothing:
            return
        if not await services.welcome.claim(guild.id, member.id, "join"):
            return
        if not member.bot:
            await asyncio.sleep(SETTLE_SECONDS)
            if guild.get_member(member.id) is None:
                return  # schon wieder weg
        locale = await services.guilds.get_locale(guild.id)
        if plan.roles:
            await self._autorole(guild, member, plan.roles)
        if plan.message and await services.welcome.throttle(guild.id):
            await self._welcome(guild, member, settings, locale)
        if plan.dm:
            await self._dm(guild, member, settings, locale)

    async def _autorole(
        self, guild: discord.Guild, member: discord.Member, role_ids: tuple[int, ...]
    ) -> None:
        try:
            applied = await apply_change(
                guild, member, Change(add=frozenset(role_ids)), "Autorolle"
            )
        except UserError:
            return  # fehlende Rechte: in `apply_change` protokolliert
        if applied.skipped:
            log.warning("autorole_skipped", guild_id=guild.id, roles=list(applied.skipped))

    async def _welcome(
        self,
        guild: discord.Guild,
        member: discord.Member,
        settings: WelcomeSettings,
        locale: str,
    ) -> None:
        channel = guild.get_channel(settings.join_channel_id or 0)
        if not isinstance(channel, discord.TextChannel):
            log.warning("welcome_channel_missing", guild_id=guild.id)
            return
        text = messages.render_template(
            settings.join_message or translator.t(locale, "welcome.default_join"),
            JOIN_FIELDS,
            user=member.mention,
            name=member.display_name,
            server=guild.name,
            count=guild.member_count or 0,
        )
        file = await self._card(guild, member, settings, locale) if settings.join_card else None
        # `{name}` und `{server}` sind frei wählbar: gepingt wird nur das neue Mitglied.
        mentions = discord.AllowedMentions(
            everyone=False, roles=False, users=[member], replied_user=False
        )
        await self._send(channel, text, mentions, file)

    async def _card(
        self, guild: discord.Guild, member: discord.Member, settings: WelcomeSettings, locale: str
    ) -> discord.File:
        try:
            avatar = await member.display_avatar.replace(size=128, static_format="png").read()
        except (discord.HTTPException, discord.NotFound):
            avatar = None  # die Karte kommt dann mit Platzhalter
        data = WelcomeCardData(
            title=translator.t(locale, "welcome.card.title"),
            name=member.display_name,
            subtitle=translator.t(locale, "welcome.card.member", count=guild.member_count or 0),
            avatar=avatar,
            accent=settings.card.accent,
            background=settings.card.background,
            template=settings.card.template,
        )
        png = await asyncio.to_thread(render_welcome_card, data)
        return discord.File(io.BytesIO(png), filename="welcome.png")

    async def _dm(
        self, guild: discord.Guild, member: discord.Member, settings: WelcomeSettings, locale: str
    ) -> None:
        text = messages.render_template(
            settings.dm_message or translator.t(locale, "welcome.default_dm"),
            DM_FIELDS,
            name=member.display_name,
            server=guild.name,
        )
        try:
            await member.send(text, allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.HTTPException):
            # Geschlossene DMs sind der Normalfall und kein Fehler.
            log.debug("welcome_dm_closed", guild_id=guild.id, user_id=member.id)

    @staticmethod
    async def _send(
        channel: discord.TextChannel,
        text: str,
        mentions: discord.AllowedMentions,
        file: discord.File | None = None,
    ) -> None:
        try:
            await channel.send(text, file=file or discord.utils.MISSING, allowed_mentions=mentions)
        except (discord.Forbidden, discord.HTTPException):
            log.warning("welcome_send_failed", guild_id=channel.guild.id, channel_id=channel.id)

    # --- Befehle ---

    welcome = app_commands.Group(
        name="welcome",
        description=d("cmd.welcome.desc"),
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )
    join = app_commands.Group(name="join", description=d("cmd.welcome.join.desc"), parent=welcome)
    leave = app_commands.Group(
        name="leave", description=d("cmd.welcome.leave.desc"), parent=welcome
    )
    dm = app_commands.Group(name="dm", description=d("cmd.welcome.dm.desc"), parent=welcome)
    autorole = app_commands.Group(
        name="autorole", description=d("cmd.welcome.autorole.desc"), parent=welcome
    )

    async def _edit(
        self,
        interaction: discord.Interaction,
        mutate: Callable[[Any], None],
        reply_key: str,
        **params: object,
    ) -> WelcomeSettings:
        guild = ensure_guild(interaction)
        services = self.bot.services
        data = (await services.welcome.settings(guild.id)).model_dump()
        mutate(data)
        try:
            updated = WelcomeSettings.model_validate(data)
        except ValidationError as exc:
            error = exc.errors()[0]
            detail = f"{'.'.join(str(p) for p in error['loc'])}: {error['msg']}"[:150]
            raise UserError("error.welcome_invalid", detail=detail) from exc
        await services.settings.save(guild.id, updated)
        services.welcome.invalidate(guild.id)
        locale = await services.guilds.get_locale(guild.id)
        await interaction.response.send_message(
            translator.t(locale, reply_key, **params),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return updated

    @join.command(name="set", description=d("cmd.welcome.join.set.desc"))
    @app_commands.describe(
        channel=d("cmd.arg.channel.desc"),
        message=d("cmd.arg.template_text.desc"),
        card=d("cmd.arg.welcome_card.desc"),
    )
    async def join_set(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        message: str | None = None,
        card: bool | None = None,
    ) -> None:
        def mutate(data: dict[str, object]) -> None:
            data.update(join_enabled=True, join_channel_id=channel.id)
            if message is not None:
                data["join_message"] = message
            if card is not None:
                data["join_card"] = card

        await self._edit(interaction, mutate, "welcome.join_set", channel=channel.mention)

    @join.command(name="off", description=d("cmd.welcome.join.off.desc"))
    async def join_off(self, interaction: discord.Interaction) -> None:
        await self._edit(
            interaction, lambda data: data.update(join_enabled=False), "welcome.join_off"
        )

    @leave.command(name="set", description=d("cmd.welcome.leave.set.desc"))
    @app_commands.describe(
        channel=d("cmd.arg.channel.desc"), message=d("cmd.arg.template_text.desc")
    )
    async def leave_set(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        message: str | None = None,
    ) -> None:
        def mutate(data: dict[str, object]) -> None:
            data.update(leave_enabled=True, leave_channel_id=channel.id)
            if message is not None:
                data["leave_message"] = message

        await self._edit(interaction, mutate, "welcome.leave_set", channel=channel.mention)

    @leave.command(name="off", description=d("cmd.welcome.leave.off.desc"))
    async def leave_off(self, interaction: discord.Interaction) -> None:
        await self._edit(
            interaction, lambda data: data.update(leave_enabled=False), "welcome.leave_off"
        )

    @dm.command(name="set", description=d("cmd.welcome.dm.set.desc"))
    @app_commands.describe(message=d("cmd.arg.template_text.desc"))
    async def dm_set(self, interaction: discord.Interaction, message: str | None = None) -> None:
        def mutate(data: dict[str, object]) -> None:
            data["dm_enabled"] = True
            if message is not None:
                data["dm_message"] = message

        await self._edit(interaction, mutate, "welcome.dm_set")

    @dm.command(name="off", description=d("cmd.welcome.dm.off.desc"))
    async def dm_off(self, interaction: discord.Interaction) -> None:
        await self._edit(interaction, lambda data: data.update(dm_enabled=False), "welcome.dm_off")

    @autorole.command(name="add", description=d("cmd.welcome.autorole.add.desc"))
    @app_commands.describe(role=d("cmd.arg.role.desc"), bots=d("cmd.arg.autorole_bots.desc"))
    async def autorole_add(
        self, interaction: discord.Interaction, role: discord.Role, bots: bool = False
    ) -> None:
        ensure_grantable(interaction, role)
        field = "bot_autoroles" if bots else "autoroles"

        def mutate(data: dict[str, list[int]]) -> None:
            data[field] = [*data[field], role.id]

        await self._edit(interaction, mutate, "welcome.autorole_added", role=role.mention)

    @autorole.command(name="remove", description=d("cmd.welcome.autorole.remove.desc"))
    @app_commands.describe(role=d("cmd.arg.role.desc"))
    async def autorole_remove(self, interaction: discord.Interaction, role: discord.Role) -> None:
        settings = await self.bot.services.welcome.settings(ensure_guild(interaction).id)
        if role.id not in settings.autoroles and role.id not in settings.bot_autoroles:
            raise UserError("error.autorole_unknown", role=role.mention)

        def mutate(data: dict[str, list[int]]) -> None:
            data["autoroles"] = [r for r in data["autoroles"] if r != role.id]
            data["bot_autoroles"] = [r for r in data["bot_autoroles"] if r != role.id]

        await self._edit(interaction, mutate, "welcome.autorole_removed", role=role.mention)

    @welcome.command(name="card", description=d("cmd.welcome.card.desc"))
    @app_commands.describe(
        accent=d("cmd.arg.accent.desc"),
        background=d("cmd.arg.background.desc"),
        template=d("cmd.arg.template.desc"),
    )
    @app_commands.choices(template=TEMPLATE_CHOICES)
    async def card(
        self,
        interaction: discord.Interaction,
        accent: str | None = None,
        background: str | None = None,
        template: app_commands.Choice[str] | None = None,
    ) -> None:
        if accent is None and background is None and template is None:
            raise UserError("error.card_nothing_to_change")
        colors = {
            k: parse_color(v) for k, v in (("accent", accent), ("background", background)) if v
        }

        def mutate(data: dict[str, dict[str, object]]) -> None:
            data["card"].update(colors)
            if template is not None:
                data["card"]["template"] = template.value

        await self._edit(interaction, mutate, "welcome.card_saved")

    @welcome.command(name="preview", description=d("cmd.welcome.preview.desc"))
    async def preview(self, interaction: discord.Interaction) -> None:
        """Zeigt die Begrüssung für den Aufrufer, nur für ihn sichtbar. Nichts wird gesendet."""
        guild = ensure_guild(interaction)
        member = interaction.user
        if not isinstance(member, discord.Member):
            raise UserError("error.guild_only")
        services = self.bot.services
        settings = await services.welcome.settings(guild.id)
        locale = await services.guilds.get_locale(guild.id)
        text = messages.render_template(
            settings.join_message or translator.t(locale, "welcome.default_join"),
            JOIN_FIELDS,
            user=member.mention,
            name=member.display_name,
            server=guild.name,
            count=guild.member_count or 0,
        )
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(
            text,
            file=await self._card(guild, member, settings, locale),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @welcome.command(name="status", description=d("cmd.welcome.status.desc"))
    async def status(self, interaction: discord.Interaction) -> None:
        guild = ensure_guild(interaction)
        services = self.bot.services
        settings = await services.welcome.settings(guild.id)
        locale = await services.guilds.get_locale(guild.id)
        t = translator.t

        def channel(enabled: bool, channel_id: int | None) -> str:
            return f"<#{channel_id}>" if enabled and channel_id else t(locale, "welcome.off")

        def roles(ids: list[int]) -> str:
            return ", ".join(f"<@&{i}>" for i in ids) or t(locale, "welcome.none")

        text = t(
            locale,
            "welcome.status",
            join=channel(settings.join_enabled, settings.join_channel_id),
            card=t(locale, "welcome.on" if settings.join_card else "welcome.off"),
            leave=channel(settings.leave_enabled, settings.leave_channel_id),
            dm=t(locale, "welcome.on" if settings.dm_enabled else "welcome.off"),
            autoroles=roles(settings.autoroles),
            bot_autoroles=roles(settings.bot_autoroles),
        )
        await interaction.response.send_message(
            text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )


async def setup(bot: GGBot) -> None:
    await bot.add_cog(WelcomeCog(bot))
