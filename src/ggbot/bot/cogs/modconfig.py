"""/modconfig: Eskalation, Verfall von Verwarnungen, DM-Verhalten."""

import discord
from discord import app_commands
from discord.ext import commands
from pydantic import ValidationError

from ggbot.bot.checks import ensure_guild
from ggbot.bot.client import GGBot
from ggbot.bot.i18n import d
from ggbot.core.duration import format_duration, parse_duration
from ggbot.core.errors import UserError
from ggbot.core.i18n import translator
from ggbot.core.settings.schema import EscalationRule, ModerationSettings

ACTIONS = [
    app_commands.Choice(name="timeout", value="timeout"),
    app_commands.Choice(name="kick", value="kick"),
    app_commands.Choice(name="ban", value="ban"),
    app_commands.Choice(name="tempban", value="tempban"),
]


def describe_rule(rule: EscalationRule) -> str:
    if rule.duration_seconds is None:
        return rule.action
    return f"{rule.action} ({format_duration(rule.duration_seconds)})"


class ModConfigCog(commands.Cog):
    def __init__(self, bot: GGBot) -> None:
        self.bot = bot

    modconfig = app_commands.Group(
        name="modconfig",
        description=d("cmd.modconfig.desc"),
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )
    escalation = app_commands.Group(
        name="escalation", description=d("cmd.modconfig.escalation.desc"), parent=modconfig
    )

    async def _load(self, interaction: discord.Interaction) -> tuple[int, str, ModerationSettings]:
        guild = ensure_guild(interaction)
        locale = await self.bot.services.guilds.get_locale(guild.id)
        return guild.id, locale, await self.bot.services.settings.get(guild.id, ModerationSettings)

    @escalation.command(name="add", description=d("cmd.modconfig.escalation.add.desc"))
    @app_commands.describe(
        warns=d("cmd.arg.warns.desc"),
        action=d("cmd.arg.action.desc"),
        duration=d("cmd.arg.duration.desc"),
    )
    @app_commands.choices(action=ACTIONS)
    async def escalation_add(
        self,
        interaction: discord.Interaction,
        warns: app_commands.Range[int, 1, 100],
        action: app_commands.Choice[str],
        duration: str | None = None,
    ) -> None:
        guild_id, locale, settings = await self._load(interaction)
        needs_duration = action.value in ("timeout", "tempban")
        if needs_duration and duration is None:
            raise UserError("error.escalation_duration_needed", action=action.value)
        if not needs_duration and duration is not None:
            raise UserError("error.escalation_duration_unexpected", action=action.value)
        try:
            rule = EscalationRule(
                warns=warns,
                action=action.value,
                duration_seconds=parse_duration(duration) if duration else None,
            )
            rules = [r for r in settings.escalation if r.warns != warns] + [rule]
            updated = settings.model_copy(update={"escalation": rules})
            ModerationSettings.model_validate(updated.model_dump())
        except ValidationError as exc:
            raise UserError("error.escalation_invalid") from exc
        await self.bot.services.settings.save(guild_id, updated)
        await interaction.response.send_message(
            translator.t(
                locale, "mod.config.escalation_saved", warns=warns, action=describe_rule(rule)
            ),
            ephemeral=True,
        )

    @escalation.command(name="remove", description=d("cmd.modconfig.escalation.remove.desc"))
    @app_commands.describe(warns=d("cmd.arg.warns.desc"))
    async def escalation_remove(
        self, interaction: discord.Interaction, warns: app_commands.Range[int, 1, 100]
    ) -> None:
        guild_id, locale, settings = await self._load(interaction)
        rules = [r for r in settings.escalation if r.warns != warns]
        if len(rules) == len(settings.escalation):
            raise UserError("error.escalation_not_found", warns=warns)
        await self.bot.services.settings.save(
            guild_id, settings.model_copy(update={"escalation": rules})
        )
        await interaction.response.send_message(
            translator.t(locale, "mod.config.escalation_removed", warns=warns), ephemeral=True
        )

    @escalation.command(name="list", description=d("cmd.modconfig.escalation.list.desc"))
    async def escalation_list(self, interaction: discord.Interaction) -> None:
        _, locale, settings = await self._load(interaction)
        if not settings.escalation:
            await interaction.response.send_message(
                translator.t(locale, "mod.config.escalation_empty"), ephemeral=True
            )
            return
        lines = [
            translator.t(
                locale, "mod.config.escalation_line", warns=r.warns, action=describe_rule(r)
            )
            for r in settings.escalation
        ]
        embed = discord.Embed(
            title=translator.t(locale, "mod.config.escalation_title"), description="\n".join(lines)
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @modconfig.command(name="warn_expiry", description=d("cmd.modconfig.warn_expiry.desc"))
    @app_commands.describe(days=d("cmd.arg.days.desc"))
    async def warn_expiry(
        self, interaction: discord.Interaction, days: app_commands.Range[int, 0, 3650]
    ) -> None:
        guild_id, locale, settings = await self._load(interaction)
        value = days or None  # 0 schaltet den Verfall ab
        await self.bot.services.settings.save(
            guild_id, settings.model_copy(update={"warn_expiry_days": value})
        )
        key = "mod.config.expiry_set" if value else "mod.config.expiry_off"
        await interaction.response.send_message(
            translator.t(locale, key, days=days), ephemeral=True
        )

    @modconfig.command(name="dm", description=d("cmd.modconfig.dm.desc"))
    @app_commands.describe(enabled=d("cmd.arg.enabled.desc"))
    async def dm(self, interaction: discord.Interaction, enabled: bool) -> None:
        guild_id, locale, settings = await self._load(interaction)
        await self.bot.services.settings.save(
            guild_id, settings.model_copy(update={"dm_on_action": enabled})
        )
        key = "mod.config.dm_on" if enabled else "mod.config.dm_off"
        await interaction.response.send_message(translator.t(locale, key), ephemeral=True)


async def setup(bot: GGBot) -> None:
    await bot.add_cog(ModConfigCog(bot))
