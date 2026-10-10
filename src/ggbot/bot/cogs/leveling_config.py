"""/leveling: Einstellungen, Belohnungen, Verwaltung und Import."""

import asyncio
from collections.abc import Callable
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands
from pydantic import ValidationError

from ggbot.bot.checks import ensure_guild
from ggbot.bot.client import GGBot
from ggbot.bot.i18n import d
from ggbot.bot.rewards import RoleChange, apply_rewards, assignable
from ggbot.core.bulk import chunked
from ggbot.core.errors import UserError
from ggbot.core.i18n import translator
from ggbot.core.leveling.announce import render_template
from ggbot.core.leveling.importer import MAX_BYTES, parse_import
from ggbot.core.leveling.xp import level_from_xp
from ggbot.core.services.leveling import Award
from ggbot.core.settings.leveling import LevelingSettings

Mutation = Callable[[dict[str, Any]], None]
ANNOUNCE_CHOICES = [
    app_commands.Choice(name=m, value=m) for m in ("off", "current", "channel", "dm")
]
REWARD_MODES = [app_commands.Choice(name=m, value=m) for m in ("stack", "replace")]
IMPORT_MODES = [app_commands.Choice(name=m, value=m) for m in ("max", "replace", "add")]
SYNC_BATCH = 5
SYNC_PAUSE_SECONDS = 1.0
SYNC_LIMIT = 5000
RESET_WORD = "RESET"


class LevelingConfigCog(commands.Cog):
    def __init__(self, bot: GGBot) -> None:
        self.bot = bot

    leveling = app_commands.Group(
        name="leveling",
        description=d("cmd.leveling.desc"),
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )
    noxp = app_commands.Group(name="noxp", description=d("cmd.leveling.noxp.desc"), parent=leveling)
    boost = app_commands.Group(
        name="boost", description=d("cmd.leveling.boost.desc"), parent=leveling
    )
    reward = app_commands.Group(
        name="reward", description=d("cmd.leveling.reward.desc"), parent=leveling
    )

    # --- Hilfen ---

    async def _locale(self, guild_id: int) -> str:
        return await self.bot.services.guilds.get_locale(guild_id)

    async def _edit(
        self, interaction: discord.Interaction, mutate: Mutation, reply_key: str, **params: object
    ) -> LevelingSettings:
        guild = ensure_guild(interaction)
        services = self.bot.services
        current = await services.settings.get(guild.id, LevelingSettings)
        data = current.model_dump()
        mutate(data)
        try:
            updated = LevelingSettings.model_validate(data)
        except ValidationError as exc:
            error = exc.errors()[0]
            detail = f"{'.'.join(str(p) for p in error['loc'])}: {error['msg']}"[:150]
            raise UserError("error.leveling_invalid", detail=detail) from exc
        await services.settings.save(guild.id, updated)
        services.leveling.invalidate(guild.id)
        locale = await self._locale(guild.id)
        await interaction.response.send_message(
            translator.t(locale, reply_key, **params), ephemeral=True
        )
        return updated

    async def _sync_member(
        self, guild: discord.Guild, member: discord.Member, award: Award
    ) -> RoleChange:
        settings = await self.bot.services.leveling.settings(guild.id)
        return await apply_rewards(guild, member, award.new_level, settings)

    # --- Grundeinstellungen ---

    @leveling.command(name="enable", description=d("cmd.leveling.enable.desc"))
    async def enable(self, interaction: discord.Interaction) -> None:
        await self._edit(interaction, lambda data: data.update(enabled=True), "leveling.enabled")

    @leveling.command(name="disable", description=d("cmd.leveling.disable.desc"))
    async def disable(self, interaction: discord.Interaction) -> None:
        await self._edit(interaction, lambda data: data.update(enabled=False), "leveling.disabled")

    @leveling.command(name="xp", description=d("cmd.leveling.xp.desc"))
    @app_commands.describe(xp_min=d("cmd.arg.xp_min.desc"), xp_max=d("cmd.arg.xp_max.desc"))
    async def xp(
        self,
        interaction: discord.Interaction,
        xp_min: app_commands.Range[int, 1, 1000],
        xp_max: app_commands.Range[int, 1, 1000],
    ) -> None:
        await self._edit(
            interaction,
            lambda data: data.update(xp_min=xp_min, xp_max=xp_max),
            "leveling.xp_set",
            xp_min=xp_min,
            xp_max=xp_max,
        )

    @leveling.command(name="cooldown", description=d("cmd.leveling.cooldown.desc"))
    @app_commands.describe(seconds=d("cmd.arg.cooldown_seconds.desc"))
    async def cooldown(
        self, interaction: discord.Interaction, seconds: app_commands.Range[int, 0, 3600]
    ) -> None:
        await self._edit(
            interaction,
            lambda data: data.update(cooldown_seconds=seconds),
            "leveling.cooldown_set",
            seconds=seconds,
        )

    @leveling.command(name="multiplier", description=d("cmd.leveling.multiplier.desc"))
    @app_commands.describe(factor=d("cmd.arg.factor.desc"))
    async def multiplier(
        self, interaction: discord.Interaction, factor: app_commands.Range[float, 0.1, 10.0]
    ) -> None:
        await self._edit(
            interaction,
            lambda data: data.update(multiplier=factor),
            "leveling.multiplier_set",
            factor=factor,
        )

    @leveling.command(name="voice", description=d("cmd.leveling.voice.desc"))
    @app_commands.describe(
        enabled=d("cmd.arg.enabled.desc"),
        xp_min=d("cmd.arg.xp_min.desc"),
        xp_max=d("cmd.arg.xp_max.desc"),
        requires_company=d("cmd.arg.requires_company.desc"),
    )
    async def voice(
        self,
        interaction: discord.Interaction,
        enabled: bool,
        xp_min: app_commands.Range[int, 1, 1000] | None = None,
        xp_max: app_commands.Range[int, 1, 1000] | None = None,
        requires_company: bool | None = None,
    ) -> None:
        def mutate(data: dict[str, Any]) -> None:
            data["voice"]["enabled"] = enabled
            if xp_min is not None:
                data["voice"]["xp_min"] = xp_min
            if xp_max is not None:
                data["voice"]["xp_max"] = xp_max
            if requires_company is not None:
                data["voice"]["requires_company"] = requires_company

        key = "leveling.voice_on" if enabled else "leveling.voice_off"
        await self._edit(interaction, mutate, key)

    @leveling.command(name="announce", description=d("cmd.leveling.announce.desc"))
    @app_commands.describe(mode=d("cmd.arg.mode.desc"), channel=d("cmd.arg.announce_channel.desc"))
    @app_commands.choices(mode=ANNOUNCE_CHOICES)
    async def announce(
        self,
        interaction: discord.Interaction,
        mode: app_commands.Choice[str],
        channel: discord.TextChannel | None = None,
    ) -> None:
        if mode.value == "channel" and channel is None:
            raise UserError("error.leveling_announce_channel_needed")

        def mutate(data: dict[str, Any]) -> None:
            data["announce"] = mode.value
            data["announce_channel_id"] = channel.id if channel else None

        await self._edit(
            interaction,
            mutate,
            f"leveling.announce_{mode.value}",
            channel=channel.mention if channel else "",
        )

    @leveling.command(name="message", description=d("cmd.leveling.message.desc"))
    @app_commands.describe(template=d("cmd.arg.template_text.desc"))
    async def message(
        self, interaction: discord.Interaction, template: app_commands.Range[str, 0, 300] = ""
    ) -> None:
        text = template.strip() or None
        if text is not None:
            try:
                render_template(text, user="@x", name="x", level=1, xp=1)
            except ValueError as exc:
                raise UserError("error.leveling_template_invalid", detail=str(exc)) from exc
        await self._edit(
            interaction,
            lambda data: data.update(announce_message=text),
            "leveling.message_set" if text else "leveling.message_reset",
        )

    # --- Status ---

    @leveling.command(name="status", description=d("cmd.leveling.status.desc"))
    async def status(self, interaction: discord.Interaction) -> None:
        guild = ensure_guild(interaction)
        locale = await self._locale(guild.id)
        t = translator.t
        s = await self.bot.services.settings.get(guild.id, LevelingSettings)

        def yes_no(value: bool) -> str:
            return t(locale, "automod.yes" if value else "automod.no")

        lines = [
            t(locale, "leveling.status.state", state="✅" if s.enabled else "⬜"),
            t(
                locale,
                "leveling.status.xp",
                xp_min=s.xp_min,
                xp_max=s.xp_max,
                cooldown=s.cooldown_seconds,
                factor=s.multiplier,
            ),
            t(
                locale,
                "leveling.status.voice",
                state="✅" if s.voice.enabled else "⬜",
                xp_min=s.voice.xp_min,
                xp_max=s.voice.xp_max,
                company=yes_no(s.voice.requires_company),
            ),
            t(locale, "leveling.status.announce", mode=s.announce),
            t(locale, "leveling.status.rewards", count=len(s.rewards), mode=s.reward_mode),
            t(
                locale,
                "leveling.status.exceptions",
                channels=len(s.no_xp_channels),
                roles=len(s.no_xp_roles),
                role_boosts=len(s.role_multipliers),
                channel_boosts=len(s.channel_multipliers),
            ),
        ]
        embed = discord.Embed(
            title=t(locale, "leveling.status.title"), description="\n".join(lines)
        )
        if not self.bot.intents.members and s.voice.enabled:
            embed.add_field(
                name="⚠️", value=t(locale, "leveling.status.voice_members_hint"), inline=False
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # --- Ausnahmen und Boosts ---

    async def _list_edit(
        self,
        interaction: discord.Interaction,
        field: str,
        target_id: int,
        mention: str,
        *,
        add: bool,
    ) -> None:
        def mutate(data: dict[str, Any]) -> None:
            ids: list[int] = data[field]
            if add and target_id not in ids:
                ids.append(target_id)
            if not add:
                data[field] = [i for i in ids if i != target_id]

        await self._edit(
            interaction,
            mutate,
            "leveling.noxp_added" if add else "leveling.noxp_removed",
            target=mention,
        )

    @noxp.command(name="channel_add", description=d("cmd.leveling.noxp.channel_add.desc"))
    @app_commands.describe(channel=d("cmd.arg.channel.desc"))
    async def noxp_channel_add(
        self, interaction: discord.Interaction, channel: discord.abc.GuildChannel
    ) -> None:
        await self._list_edit(interaction, "no_xp_channels", channel.id, channel.mention, add=True)

    @noxp.command(name="channel_remove", description=d("cmd.leveling.noxp.channel_remove.desc"))
    @app_commands.describe(channel=d("cmd.arg.channel.desc"))
    async def noxp_channel_remove(
        self, interaction: discord.Interaction, channel: discord.abc.GuildChannel
    ) -> None:
        await self._list_edit(interaction, "no_xp_channels", channel.id, channel.mention, add=False)

    @noxp.command(name="role_add", description=d("cmd.leveling.noxp.role_add.desc"))
    @app_commands.describe(role=d("cmd.arg.role.desc"))
    async def noxp_role_add(self, interaction: discord.Interaction, role: discord.Role) -> None:
        await self._list_edit(interaction, "no_xp_roles", role.id, role.mention, add=True)

    @noxp.command(name="role_remove", description=d("cmd.leveling.noxp.role_remove.desc"))
    @app_commands.describe(role=d("cmd.arg.role.desc"))
    async def noxp_role_remove(self, interaction: discord.Interaction, role: discord.Role) -> None:
        await self._list_edit(interaction, "no_xp_roles", role.id, role.mention, add=False)

    async def _boost(
        self,
        interaction: discord.Interaction,
        field: str,
        target_id: int,
        mention: str,
        factor: float | None,
    ) -> None:
        def mutate(data: dict[str, Any]) -> None:
            if factor is None:
                data[field].pop(target_id, None)
            else:
                data[field][target_id] = factor

        key = "leveling.boost_set" if factor is not None else "leveling.boost_removed"
        await self._edit(interaction, mutate, key, target=mention, factor=factor)

    @boost.command(name="channel_set", description=d("cmd.leveling.boost.channel_set.desc"))
    @app_commands.describe(channel=d("cmd.arg.channel.desc"), factor=d("cmd.arg.factor.desc"))
    async def boost_channel_set(
        self,
        interaction: discord.Interaction,
        channel: discord.abc.GuildChannel,
        factor: app_commands.Range[float, 0.1, 10.0],
    ) -> None:
        await self._boost(interaction, "channel_multipliers", channel.id, channel.mention, factor)

    @boost.command(name="channel_remove", description=d("cmd.leveling.boost.channel_remove.desc"))
    @app_commands.describe(channel=d("cmd.arg.channel.desc"))
    async def boost_channel_remove(
        self, interaction: discord.Interaction, channel: discord.abc.GuildChannel
    ) -> None:
        await self._boost(interaction, "channel_multipliers", channel.id, channel.mention, None)

    @boost.command(name="role_set", description=d("cmd.leveling.boost.role_set.desc"))
    @app_commands.describe(role=d("cmd.arg.role.desc"), factor=d("cmd.arg.factor.desc"))
    async def boost_role_set(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
        factor: app_commands.Range[float, 0.1, 10.0],
    ) -> None:
        await self._boost(interaction, "role_multipliers", role.id, role.mention, factor)

    @boost.command(name="role_remove", description=d("cmd.leveling.boost.role_remove.desc"))
    @app_commands.describe(role=d("cmd.arg.role.desc"))
    async def boost_role_remove(self, interaction: discord.Interaction, role: discord.Role) -> None:
        await self._boost(interaction, "role_multipliers", role.id, role.mention, None)

    # --- Belohnungen ---

    @reward.command(name="add", description=d("cmd.leveling.reward.add.desc"))
    @app_commands.describe(level=d("cmd.arg.level.desc"), role=d("cmd.arg.role.desc"))
    async def reward_add(
        self,
        interaction: discord.Interaction,
        level: app_commands.Range[int, 1, 1000],
        role: discord.Role,
    ) -> None:
        guild = ensure_guild(interaction)
        if not assignable(guild, role):
            # Besser jetzt ablehnen als erst beim ersten Aufstieg still zu scheitern.
            raise UserError("error.leveling_role_not_assignable", role=role.name)

        def mutate(data: dict[str, Any]) -> None:
            data["rewards"][level] = role.id

        await self._edit(
            interaction, mutate, "leveling.reward_added", level=level, role=role.mention
        )

    @reward.command(name="remove", description=d("cmd.leveling.reward.remove.desc"))
    @app_commands.describe(level=d("cmd.arg.level.desc"))
    async def reward_remove(
        self, interaction: discord.Interaction, level: app_commands.Range[int, 1, 1000]
    ) -> None:
        def mutate(data: dict[str, Any]) -> None:
            if level not in data["rewards"]:
                raise UserError("error.leveling_reward_not_found", level=level)
            del data["rewards"][level]

        await self._edit(interaction, mutate, "leveling.reward_removed", level=level)

    @reward.command(name="list", description=d("cmd.leveling.reward.list.desc"))
    async def reward_list(self, interaction: discord.Interaction) -> None:
        guild = ensure_guild(interaction)
        locale = await self._locale(guild.id)
        t = translator.t
        settings = await self.bot.services.settings.get(guild.id, LevelingSettings)
        if not settings.rewards:
            await interaction.response.send_message(
                t(locale, "leveling.reward_empty"), ephemeral=True
            )
            return
        lines = []
        for level, role_id in settings.rewards.items():
            role = guild.get_role(role_id)
            if role is None:
                lines.append(
                    t(locale, "leveling.reward_line_missing", level=level, role_id=role_id)
                )
            elif not assignable(guild, role):
                lines.append(
                    t(locale, "leveling.reward_line_blocked", level=level, role=role.mention)
                )
            else:
                lines.append(t(locale, "leveling.reward_line", level=level, role=role.mention))
        header = t(locale, "leveling.reward_mode_line", mode=settings.reward_mode)
        await interaction.response.send_message(
            header + "\n" + "\n".join(lines),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @reward.command(name="mode", description=d("cmd.leveling.reward.mode.desc"))
    @app_commands.describe(mode=d("cmd.arg.mode.desc"))
    @app_commands.choices(mode=REWARD_MODES)
    async def reward_mode(
        self, interaction: discord.Interaction, mode: app_commands.Choice[str]
    ) -> None:
        await self._edit(
            interaction,
            lambda data: data.update(reward_mode=mode.value),
            f"leveling.reward_mode_{mode.value}",
        )

    @leveling.command(name="sync", description=d("cmd.leveling.sync.desc"))
    async def sync(self, interaction: discord.Interaction) -> None:
        """Wendet die Belohnungen auf alle an, nach einer Änderung der Regeln oder einem Import."""
        guild = ensure_guild(interaction)
        locale = await self._locale(guild.id)
        service = self.bot.services.leveling
        settings = await service.settings(guild.id)
        if not settings.rewards:
            raise UserError("error.leveling_no_rewards")
        await interaction.response.defer(ephemeral=True)
        changed = unknown = failed = 0
        with self.bot.services.bulk_gate.acquire(guild.id):
            members = await service.members_with_xp(guild.id, SYNC_LIMIT)
            for index, block in enumerate(chunked(members, SYNC_BATCH)):
                if index:
                    await asyncio.sleep(SYNC_PAUSE_SECONDS)
                for user_id, xp in block:
                    member = guild.get_member(user_id)
                    if member is None:
                        unknown += 1  # nicht (mehr) auf dem Server oder nicht im Cache
                        continue
                    result = await apply_rewards(guild, member, level_from_xp(xp), settings)
                    changed += result.changed
                    failed += result.forbidden
        await interaction.followup.send(
            translator.t(
                locale,
                "leveling.sync_done",
                total=len(members),
                changed=changed,
                unknown=unknown,
                failed=failed,
            )
        )

    # --- Verwaltung ---

    async def _after_admin_change(
        self, guild: discord.Guild, member: discord.Member | None, award: Award
    ) -> None:
        if member is not None and award.level_changed:
            await self._sync_member(guild, member, award)

    @leveling.command(name="give", description=d("cmd.leveling.give.desc"))
    @app_commands.describe(member=d("cmd.arg.member.desc"), amount=d("cmd.arg.xp_amount.desc"))
    async def give(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        amount: app_commands.Range[int, -100_000_000, 100_000_000],
    ) -> None:
        guild = ensure_guild(interaction)
        if member.bot:
            raise UserError("error.leveling_bot")
        award = await self.bot.services.leveling.give(guild.id, member.id, amount)
        await self._after_admin_change(guild, member, award)
        locale = await self._locale(guild.id)
        await interaction.response.send_message(
            translator.t(
                locale, "leveling.given", member=member.mention, xp=award.xp, level=award.new_level
            ),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @leveling.command(name="set_level", description=d("cmd.leveling.set_level.desc"))
    @app_commands.describe(member=d("cmd.arg.member.desc"), level=d("cmd.arg.level.desc"))
    async def set_level(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        level: app_commands.Range[int, 0, 1000],
    ) -> None:
        guild = ensure_guild(interaction)
        if member.bot:
            raise UserError("error.leveling_bot")
        award = await self.bot.services.leveling.set_level(guild.id, member.id, level)
        await self._after_admin_change(guild, member, award)
        locale = await self._locale(guild.id)
        await interaction.response.send_message(
            translator.t(locale, "leveling.level_set", member=member.mention, level=level),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @leveling.command(name="reset_user", description=d("cmd.leveling.reset_user.desc"))
    @app_commands.describe(user=d("cmd.arg.user.desc"))
    async def reset_user(self, interaction: discord.Interaction, user: discord.User) -> None:
        guild = ensure_guild(interaction)
        existed = await self.bot.services.leveling.reset_user(guild.id, user.id)
        locale = await self._locale(guild.id)
        key = "leveling.user_reset" if existed else "leveling.user_nothing"
        await interaction.response.send_message(
            translator.t(locale, key, user=user.mention),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @leveling.command(name="reset_all", description=d("cmd.leveling.reset_all.desc"))
    @app_commands.describe(confirm=d("cmd.arg.confirm.desc"))
    async def reset_all(self, interaction: discord.Interaction, confirm: str) -> None:
        guild = ensure_guild(interaction)
        if confirm != RESET_WORD:
            # Ohne das Wort passiert nichts: ein Tippfehler darf nicht den ganzen Server löschen.
            raise UserError("error.leveling_confirm", word=RESET_WORD)
        removed = await self.bot.services.leveling.reset_all(guild.id)
        locale = await self._locale(guild.id)
        await interaction.response.send_message(
            translator.t(locale, "leveling.all_reset", count=removed), ephemeral=True
        )

    # --- Import ---

    @leveling.command(name="import", description=d("cmd.leveling.import.desc"))
    @app_commands.describe(file=d("cmd.arg.file.desc"), mode=d("cmd.arg.import_mode.desc"))
    @app_commands.choices(mode=IMPORT_MODES)
    async def import_levels(
        self,
        interaction: discord.Interaction,
        file: discord.Attachment,
        mode: app_commands.Choice[str] | None = None,
    ) -> None:
        guild = ensure_guild(interaction)
        if file.size > MAX_BYTES:
            raise UserError("error.import_too_large", limit=MAX_BYTES // (1024 * 1024))
        chosen = mode.value if mode else "max"
        await interaction.response.defer(ephemeral=True)
        raw = await file.read()
        parsed = await asyncio.to_thread(parse_import, raw)
        with self.bot.services.bulk_gate.acquire(guild.id):
            result = await self.bot.services.leveling.import_rows(
                guild.id,
                parsed.rows,
                chosen,  # type: ignore[arg-type]
            )
        locale = await self._locale(guild.id)
        await interaction.followup.send(
            translator.t(
                locale,
                "leveling.imported",
                created=result.created,
                updated=result.updated,
                skipped=parsed.skipped,
                mode=chosen,
            )
        )


async def setup(bot: GGBot) -> None:
    await bot.add_cog(LevelingConfigCog(bot))
