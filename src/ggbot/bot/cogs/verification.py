"""/verification: eine Rolle gegen einen Knopfdruck (oder eine gelöste Aufgabe)."""

import contextlib
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands
from pydantic import ValidationError

from ggbot.bot import verification as ui
from ggbot.bot.checks import ensure_grantable, ensure_guild
from ggbot.bot.client import GGBot
from ggbot.bot.i18n import d
from ggbot.core.errors import UserError
from ggbot.core.i18n import translator
from ggbot.core.settings.verification import VerificationSettings

MODE_CHOICES = [app_commands.Choice(name=m, value=m) for m in ("button", "code")]
CHANNEL_NEEDS = ("view_channel", "send_messages", "embed_links")


class VerificationCog(commands.Cog):
    def __init__(self, bot: GGBot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(ui.StartButton, ui.EnterButton)

    async def cog_unload(self) -> None:
        self.bot.remove_dynamic_items(ui.StartButton, ui.EnterButton)

    @commands.Cog.listener()
    async def on_ggbot_settings_changed(self, event: Any) -> None:
        if event.module == "verification":
            self.bot.services.verification.invalidate(event.guild_id)

    verification = app_commands.Group(
        name="verification",
        description=d("cmd.verification.desc"),
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    async def _locale(self, guild_id: int) -> str:
        return await self.bot.services.guilds.get_locale(guild_id)

    @verification.command(name="setup", description=d("cmd.verification.setup.desc"))
    @app_commands.describe(
        channel=d("cmd.arg.channel.desc"),
        role=d("cmd.arg.verify_role.desc"),
        mode=d("cmd.arg.verify_mode.desc"),
        title=d("cmd.arg.verify_title.desc"),
        message=d("cmd.arg.verify_message.desc"),
    )
    @app_commands.choices(mode=MODE_CHOICES)
    async def setup(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        role: discord.Role,
        mode: app_commands.Choice[str] | None = None,
        title: app_commands.Range[str, 1, 100] | None = None,
        message: app_commands.Range[str, 1, 1000] | None = None,
    ) -> None:
        guild = ensure_guild(interaction)
        # Der Akteur muss die Rolle weitergeben und der Bot sie vergeben dürfen.
        ensure_grantable(interaction, role)
        have = channel.permissions_for(guild.me)
        missing = [name for name in CHANNEL_NEEDS if not getattr(have, name)]
        if missing:
            raise UserError(
                "error.missing_bot_permissions",
                permissions=", ".join(n.replace("_", " ").title() for n in missing),
            )
        services = self.bot.services
        previous = await services.verification.settings(guild.id)
        try:
            settings = VerificationSettings.model_validate(
                {
                    **previous.model_dump(),
                    "enabled": True,
                    "role_id": role.id,
                    "channel_id": channel.id,
                    "mode": mode.value if mode else previous.mode,
                    "title": title or previous.title,
                    "message": message or previous.message,
                }
            )
        except ValidationError as exc:
            error = exc.errors()[0]
            raise UserError("error.verify_invalid", detail=str(error["msg"])[:150]) from exc
        locale = await self._locale(guild.id)
        await interaction.response.defer(ephemeral=True)
        await self._delete_old_panel(guild, previous)
        sent = await channel.send(
            embed=ui.panel_embed(settings, locale),
            view=ui.panel_view(locale),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        settings = settings.model_copy(update={"panel_message_id": sent.id})
        await services.settings.save(guild.id, settings)
        services.verification.invalidate(guild.id)
        await interaction.followup.send(
            translator.t(locale, "verify.setup_done", channel=channel.mention, role=role.mention),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _delete_old_panel(self, guild: discord.Guild, old: VerificationSettings) -> None:
        """Eine zweite Einrichtung ersetzt das Panel, damit nie zwei Knöpfe gelten."""
        if old.channel_id is None or old.panel_message_id is None:
            return
        channel = guild.get_channel(old.channel_id)
        if isinstance(channel, discord.TextChannel):
            with contextlib.suppress(discord.HTTPException):
                await channel.get_partial_message(old.panel_message_id).delete()

    @verification.command(name="off", description=d("cmd.verification.off.desc"))
    async def off(self, interaction: discord.Interaction) -> None:
        guild = ensure_guild(interaction)
        services = self.bot.services
        current = await services.verification.settings(guild.id)
        await services.settings.save(guild.id, current.model_copy(update={"enabled": False}))
        services.verification.invalidate(guild.id)
        locale = await self._locale(guild.id)
        await interaction.response.send_message(translator.t(locale, "verify.off"), ephemeral=True)

    @verification.command(name="status", description=d("cmd.verification.status.desc"))
    async def status(self, interaction: discord.Interaction) -> None:
        guild = ensure_guild(interaction)
        s = await self.bot.services.verification.settings(guild.id)
        locale = await self._locale(guild.id)
        t = translator.t
        text = t(
            locale,
            "verify.status",
            state=t(locale, "welcome.on") if s.enabled else t(locale, "welcome.off"),
            mode=s.mode,
            role=f"<@&{s.role_id}>" if s.role_id else t(locale, "welcome.none"),
            channel=f"<#{s.channel_id}>" if s.channel_id else t(locale, "welcome.none"),
        )
        await interaction.response.send_message(
            text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )


async def setup(bot: GGBot) -> None:
    await bot.add_cog(VerificationCog(bot))
