"""Temporäre Sprachkanäle: Lobby betreten, eigenen Kanal bekommen, leer = weg."""

import contextlib
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands
from pydantic import ValidationError

from ggbot.bot.checks import ensure_guild
from ggbot.bot.client import GGBot
from ggbot.bot.i18n import d
from ggbot.core.errors import UserError
from ggbot.core.i18n import translator
from ggbot.core.logging import get_logger
from ggbot.core.settings.tempvoice import TempVoiceSettings
from ggbot.core.tempvoice import rules

log = get_logger(__name__)

CREATE_COOLDOWN_SECONDS = 15
RENAME_COOLDOWN_SECONDS = 120  # Discord erlaubt nur zwei Umbenennungen je zehn Minuten
NEEDS = ("manage_channels", "move_members")


class TempVoiceCog(commands.Cog):
    def __init__(self, bot: GGBot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_ggbot_settings_changed(self, event: Any) -> None:
        if event.module == "tempvoice":
            self.bot.services.tempvoice.invalidate(event.guild_id)

    # --- Anlegen und Aufräumen ---

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ) -> None:
        if before.channel == after.channel or member.bot:
            return
        try:
            if after.channel is not None:
                await self._maybe_create(member, after.channel)
            if before.channel is not None:
                await self._maybe_delete(before.channel)
        except discord.HTTPException:
            log.warning("tempvoice_failed", guild_id=member.guild.id, exc_info=True)

    async def _maybe_create(self, member: discord.Member, joined: discord.abc.GuildChannel) -> None:
        guild = member.guild
        service = self.bot.services.tempvoice
        settings = await service.settings(guild.id)
        if not settings.enabled or joined.id != settings.lobby_channel_id:
            return
        if not isinstance(joined, discord.VoiceChannel):
            return
        # Wer die Lobby hin- und herschaltet, soll keine Kanalflut auslösen.
        if not await self.bot.services.cooldown.hit(
            "tempvoice", f"{guild.id}:{member.id}", seconds=CREATE_COOLDOWN_SECONDS
        ):
            return
        count = await service.count(guild.id)
        if count >= settings.max_channels:
            log.info("tempvoice_limit", guild_id=guild.id, count=count)
            return
        have = guild.me.guild_permissions
        if not all(getattr(have, n) for n in NEEDS):
            log.warning("tempvoice_no_permission", guild_id=guild.id)
            return
        category = guild.get_channel(settings.category_id or 0)
        if not isinstance(category, discord.CategoryChannel):
            category = joined.category
        channel = await guild.create_voice_channel(
            rules.channel_name(settings.name_template, user=member.display_name, number=count + 1),
            category=category,
            user_limit=settings.user_limit,
            overwrites={member: discord.PermissionOverwrite(connect=True, view_channel=True)},
            reason="Temporärer Sprachkanal",
        )
        await service.register(guild.id, channel.id, member.id)
        try:
            await member.move_to(channel, reason="Temporärer Sprachkanal")
        except discord.HTTPException:
            # Das Mitglied ist schon wieder weg: der leere Kanal bliebe sonst liegen.
            await self._maybe_delete(channel)

    async def _maybe_delete(self, channel: discord.abc.GuildChannel) -> None:
        if not isinstance(channel, discord.VoiceChannel) or channel.members:
            return
        service = self.bot.services.tempvoice
        # Wer den Eintrag entfernt, löscht den Kanal: zwei Austritte kurz nacheinander löschen
        # nicht doppelt.
        if not await service.remove(channel.id):
            return
        with contextlib.suppress(discord.NotFound):
            await channel.delete(reason="Temporärer Sprachkanal leer")

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        for guild in list(self.bot.guilds):
            try:
                await self.reconcile(guild)
            except Exception:
                log.exception("tempvoice_reconcile_failed", guild_id=guild.id)

    async def reconcile(self, guild: discord.Guild) -> None:
        """Nach einem Neustart: Einträge ohne Kanal löschen, verwaiste leere Kanäle entfernen."""
        service = self.bot.services.tempvoice
        for channel_id in await service.channel_ids(guild.id):
            channel = guild.get_channel(channel_id)
            if channel is None:
                await service.remove(channel_id)
            else:
                await self._maybe_delete(channel)

    # --- Befehle der Mitglieder ---

    voice = app_commands.Group(name="voice", description=d("cmd.voice.desc"), guild_only=True)

    async def _say(self, interaction: discord.Interaction, key: str, **params: object) -> None:
        locale = await self.bot.services.guilds.get_locale(interaction.guild_id or 0)
        await interaction.response.send_message(
            translator.t(locale, key, **params),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _owned(self, interaction: discord.Interaction) -> discord.VoiceChannel:
        guild = ensure_guild(interaction)
        member = guild.get_member(interaction.user.id)
        channel = member.voice.channel if member and member.voice else None
        if not isinstance(channel, discord.VoiceChannel):
            raise UserError("error.voice_not_in_channel")
        owner = await self.bot.services.tempvoice.owner(channel.id)
        if owner != interaction.user.id:
            raise UserError("error.voice_not_owner")
        return channel

    async def _edit(self, channel: discord.VoiceChannel, **kwargs: Any) -> None:
        try:
            await channel.edit(reason="Temporärer Sprachkanal", **kwargs)
        except discord.Forbidden as exc:
            raise UserError("error.voice_forbidden") from exc

    @voice.command(name="name", description=d("cmd.voice.name.desc"))
    @app_commands.describe(name=d("cmd.arg.voice_name.desc"))
    async def name(self, interaction: discord.Interaction, name: str) -> None:
        channel = await self._owned(interaction)
        cleaned = rules.clean_name(name)
        if not cleaned:
            raise UserError("error.voice_name_empty")
        await self.bot.services.cooldown.ensure(
            "voicename", channel.id, seconds=RENAME_COOLDOWN_SECONDS
        )
        await self._edit(channel, name=cleaned)
        await self._say(interaction, "voice.name_done", name=cleaned)

    @voice.command(name="limit", description=d("cmd.voice.limit.desc"))
    @app_commands.describe(limit=d("cmd.arg.voice_limit.desc"))
    async def limit(
        self, interaction: discord.Interaction, limit: app_commands.Range[int, 0, 99]
    ) -> None:
        channel = await self._owned(interaction)
        await self._edit(channel, user_limit=limit)
        await self._say(interaction, "voice.limit_done", limit=limit)

    @voice.command(name="lock", description=d("cmd.voice.lock.desc"))
    async def lock(self, interaction: discord.Interaction) -> None:
        channel = await self._owned(interaction)
        await self._set_connect(channel, False)
        await self._say(interaction, "voice.lock_done")

    @voice.command(name="unlock", description=d("cmd.voice.unlock.desc"))
    async def unlock(self, interaction: discord.Interaction) -> None:
        channel = await self._owned(interaction)
        await self._set_connect(channel, None)
        await self._say(interaction, "voice.unlock_done")

    async def _set_connect(self, channel: discord.VoiceChannel, value: bool | None) -> None:
        try:
            await channel.set_permissions(
                channel.guild.default_role, connect=value, reason="Temporärer Sprachkanal"
            )
        except discord.Forbidden as exc:
            raise UserError("error.voice_forbidden") from exc

    # --- Befehle des Teams ---

    tempvoice = app_commands.Group(
        name="tempvoice",
        description=d("cmd.tempvoice.desc"),
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    @tempvoice.command(name="setup", description=d("cmd.tempvoice.setup.desc"))
    @app_commands.describe(
        lobby=d("cmd.arg.voice_lobby.desc"),
        category=d("cmd.arg.voice_category.desc"),
        template=d("cmd.arg.voice_template.desc"),
        limit=d("cmd.arg.voice_limit.desc"),
    )
    async def setup(
        self,
        interaction: discord.Interaction,
        lobby: discord.VoiceChannel,
        category: discord.CategoryChannel | None = None,
        template: str | None = None,
        limit: app_commands.Range[int, 0, 99] | None = None,
    ) -> None:
        guild = ensure_guild(interaction)
        if not all(getattr(guild.me.guild_permissions, n) for n in NEEDS):
            raise UserError("error.tempvoice_perms")
        services = self.bot.services
        data = (await services.tempvoice.settings(guild.id)).model_dump()
        data.update(enabled=True, lobby_channel_id=str(lobby.id))
        if category is not None:
            data["category_id"] = str(category.id)
        if template is not None:
            data["name_template"] = template
        if limit is not None:
            data["user_limit"] = limit
        try:
            updated = TempVoiceSettings.model_validate(data)
        except ValidationError as exc:
            error = exc.errors()[0]
            detail = f"{'.'.join(str(p) for p in error['loc'])}: {error['msg']}"[:150]
            raise UserError("error.tempvoice_invalid", detail=detail) from exc
        await services.settings.save(guild.id, updated)
        services.tempvoice.invalidate(guild.id)
        await self._say(interaction, "voice.setup_done", lobby=lobby.mention)

    @tempvoice.command(name="off", description=d("cmd.tempvoice.off.desc"))
    async def off(self, interaction: discord.Interaction) -> None:
        guild = ensure_guild(interaction)
        services = self.bot.services
        current = await services.tempvoice.settings(guild.id)
        await services.settings.save(guild.id, current.model_copy(update={"enabled": False}))
        services.tempvoice.invalidate(guild.id)
        await self._say(interaction, "voice.off_done")

    @tempvoice.command(name="status", description=d("cmd.tempvoice.status.desc"))
    async def status(self, interaction: discord.Interaction) -> None:
        guild = ensure_guild(interaction)
        service = self.bot.services.tempvoice
        settings = await service.settings(guild.id)
        if not settings.enabled:
            await self._say(interaction, "voice.status_off")
            return
        await self._say(
            interaction,
            "voice.status",
            lobby=f"<#{settings.lobby_channel_id}>",
            count=await service.count(guild.id),
            max=settings.max_channels,
        )


async def setup(bot: GGBot) -> None:
    await bot.add_cog(TempVoiceCog(bot))
