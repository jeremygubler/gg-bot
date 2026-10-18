"""/suggest und /suggestion: Vorschläge der Mitglieder, entschieden vom Team."""

import contextlib
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from ggbot.bot.checks import ensure_guild
from ggbot.bot.client import GGBot
from ggbot.bot.i18n import d
from ggbot.bot.suggestions import VoteButton, suggestion_embed, suggestion_view
from ggbot.core.errors import UserError
from ggbot.core.i18n import translator
from ggbot.core.logging import get_logger
from ggbot.core.services.suggestions import SuggestionView
from ggbot.core.settings.suggestions import SuggestionSettings
from ggbot.core.suggestions import rules

log = get_logger(__name__)

SUGGEST_COOLDOWN_SECONDS = 60


class SuggestionsCog(commands.Cog):
    def __init__(self, bot: GGBot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(VoteButton)

    async def cog_unload(self) -> None:
        self.bot.remove_dynamic_items(VoteButton)

    @commands.Cog.listener()
    async def on_ggbot_settings_changed(self, event: Any) -> None:
        if event.module == "suggestions":
            self.bot.services.suggestions.invalidate(event.guild_id)

    async def _locale(self, guild_id: int) -> str:
        return str(await self.bot.services.guilds.get_locale(guild_id))

    async def _say(self, interaction: discord.Interaction, key: str, **params: object) -> None:
        locale = await self._locale(interaction.guild_id or 0)
        await interaction.response.send_message(
            translator.t(locale, key, **params),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    # --- Mitglieder ---

    @app_commands.command(name="suggest", description=d("cmd.suggest.desc"))
    @app_commands.describe(text=d("cmd.arg.suggest_text.desc"))
    @app_commands.guild_only()
    async def suggest(self, interaction: discord.Interaction, text: str) -> None:
        guild = ensure_guild(interaction)
        services = self.bot.services
        settings = await services.suggestions.settings(guild.id)
        channel = guild.get_channel(settings.channel_id or 0)
        if not settings.enabled or not isinstance(channel, discord.TextChannel):
            raise UserError("error.suggest_off")
        rules.clean_text(text)  # früh prüfen, bevor der Cooldown verbraucht wird
        have = channel.permissions_for(guild.me)
        if not (have.view_channel and have.send_messages and have.embed_links):
            raise UserError("error.suggest_channel_perms", channel=channel.mention)
        await services.cooldown.ensure(
            "suggest", f"{guild.id}:{interaction.user.id}", seconds=SUGGEST_COOLDOWN_SECONDS
        )
        view = await services.suggestions.create(guild.id, interaction.user.id, text)
        locale = await self._locale(guild.id)

        message = await channel.send(
            embed=suggestion_embed(view, rules.Tally(0, 0), locale),
            view=suggestion_view(view.id),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await services.suggestions.set_message(view.id, channel.id, message.id)
        await self._say(interaction, "suggest.sent", number=view.number, channel=channel.mention)

    # --- Team ---

    suggestion = app_commands.Group(
        name="suggestion",
        description=d("cmd.suggestion.desc"),
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    @suggestion.command(name="setup", description=d("cmd.suggestion.setup.desc"))
    @app_commands.describe(channel=d("cmd.arg.channel.desc"))
    async def setup(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        guild = ensure_guild(interaction)
        have = channel.permissions_for(guild.me)
        if not (have.view_channel and have.send_messages and have.embed_links):
            raise UserError("error.suggest_channel_perms", channel=channel.mention)
        services = self.bot.services
        current = await services.suggestions.settings(guild.id)
        updated = current.model_copy(update={"enabled": True, "channel_id": channel.id})
        await services.settings.save(
            guild.id, SuggestionSettings.model_validate(updated.model_dump())
        )
        services.suggestions.invalidate(guild.id)
        await self._say(interaction, "suggest.setup_done", channel=channel.mention)

    @suggestion.command(name="off", description=d("cmd.suggestion.off.desc"))
    async def off(self, interaction: discord.Interaction) -> None:
        guild = ensure_guild(interaction)
        services = self.bot.services
        current = await services.suggestions.settings(guild.id)
        updated = current.model_copy(update={"enabled": False})
        await services.settings.save(guild.id, updated)
        services.suggestions.invalidate(guild.id)
        await self._say(interaction, "suggest.off_done")

    @suggestion.command(name="approve", description=d("cmd.suggestion.approve.desc"))
    @app_commands.describe(
        number=d("cmd.arg.suggest_number.desc"), reason=d("cmd.arg.suggest_reason.desc")
    )
    async def approve(
        self, interaction: discord.Interaction, number: int, reason: str | None = None
    ) -> None:
        await self._decide(interaction, number, reason, approve=True)

    @suggestion.command(name="deny", description=d("cmd.suggestion.deny.desc"))
    @app_commands.describe(
        number=d("cmd.arg.suggest_number.desc"), reason=d("cmd.arg.suggest_reason.desc")
    )
    async def deny(
        self, interaction: discord.Interaction, number: int, reason: str | None = None
    ) -> None:
        await self._decide(interaction, number, reason, approve=False)

    async def _decide(
        self, interaction: discord.Interaction, number: int, reason: str | None, *, approve: bool
    ) -> None:
        guild = ensure_guild(interaction)
        services = self.bot.services
        view = await services.suggestions.decide(
            guild.id, number, approve=approve, decided_by=interaction.user.id, reason=reason
        )
        await self._refresh_message(guild, view)
        settings = await services.suggestions.settings(guild.id)
        if settings.dm_author:
            await self._notify_author(guild, view)
        await self._say(
            interaction,
            "suggest.approved_done" if approve else "suggest.denied_done",
            number=number,
        )

    async def _refresh_message(self, guild: discord.Guild, view: SuggestionView) -> None:
        channel = guild.get_channel(view.channel_id or 0)
        if not isinstance(channel, discord.TextChannel) or view.message_id is None:
            return
        locale = await self._locale(guild.id)
        tally = await self.bot.services.suggestions.tally(view.id)
        try:
            # Ohne Ansicht: entschiedene Vorschläge nehmen keine Stimmen mehr an.
            await channel.get_partial_message(view.message_id).edit(
                embed=suggestion_embed(view, tally, locale),
                view=None,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.NotFound:
            log.info("suggestion_message_gone", guild_id=guild.id, number=view.number)
        except discord.HTTPException:
            log.warning("suggestion_edit_failed", guild_id=guild.id, exc_info=True)

    async def _notify_author(self, guild: discord.Guild, view: SuggestionView) -> None:
        member = guild.get_member(view.author_id)
        if member is None:
            return
        locale = await self._locale(guild.id)
        key = f"suggest.dm.{view.status}"
        text = translator.t(
            locale, key, number=view.number, guild=guild.name, reason=view.decision_reason or "—"
        )
        with contextlib.suppress(discord.HTTPException):  # DMs zu: dann eben nicht
            await member.send(text, allowed_mentions=discord.AllowedMentions.none())


async def setup(bot: GGBot) -> None:
    await bot.add_cog(SuggestionsCog(bot))
