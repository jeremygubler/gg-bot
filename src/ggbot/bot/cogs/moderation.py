"""Moderationsbefehle: warn, timeout, kick, ban, tempban, softban, case, history, notes."""

import discord
from discord import app_commands
from discord.ext import commands

from ggbot.bot.checks import ensure_bot_permissions, ensure_guild, ensure_hierarchy
from ggbot.bot.client import GGBot
from ggbot.bot.embeds import case_embed, history_embed, notes_embed
from ggbot.bot.i18n import d
from ggbot.bot.ports import DiscordModerationPort
from ggbot.core.db.models import ScheduledJob
from ggbot.core.duration import format_duration, parse_duration
from ggbot.core.errors import UserError
from ggbot.core.i18n import translator
from ggbot.core.services.moderation import MAX_REASON, UNBAN_JOB, ActionResult
from ggbot.core.settings.schema import GeneralSettings

Reason = app_commands.Range[str, 1, MAX_REASON]
DeleteDays = app_commands.Range[int, 0, 7]


class ModerationCog(commands.Cog):
    def __init__(self, bot: GGBot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.services.scheduler.register(UNBAN_JOB, self._unban_job)

    # --- Hilfen ---

    def _port(self, guild: discord.Guild) -> DiscordModerationPort:
        return DiscordModerationPort(self.bot, guild)

    async def _locale(self, guild: discord.Guild) -> str:
        return await self.bot.services.guilds.get_locale(guild.id)

    async def _color(self, guild: discord.Guild) -> int:
        return (await self.bot.services.settings.get(guild.id, GeneralSettings)).embed_color

    async def _unban_job(self, job: ScheduledJob) -> None:
        guild = self.bot.get_guild(job.guild_id)
        if guild is None:
            # Anderer Shard oder Bot entfernt: der Scheduler wiederholt mit Backoff.
            raise LookupError(f"Server {job.guild_id} nicht verfügbar")
        await self.bot.services.moderation.finish_tempban(
            self._port(guild), job.guild_id, int(job.payload["case_no"])
        )

    async def _resolve_member(self, guild: discord.Guild, user_id: int) -> discord.Member | None:
        """Mitglied oder None, wenn der Nutzer nicht (mehr) auf dem Server ist."""
        member = guild.get_member(user_id)
        if member is not None:
            return member
        try:
            return await guild.fetch_member(user_id)
        except discord.NotFound:
            return None

    async def _check_target(
        self, interaction: discord.Interaction, guild: discord.Guild, user: discord.abc.User
    ) -> None:
        """Hierarchie, falls das Ziel auf dem Server ist. Ban per ID bleibt möglich."""
        member = await self._resolve_member(guild, user.id)
        if member is not None:
            ensure_hierarchy(interaction, member)
        elif user.id == interaction.user.id:
            raise UserError("error.hierarchy_self")
        elif user.id == guild.me.id:
            raise UserError("error.mod_target_bot")

    def _done_text(
        self, locale: str, kind: str, target: discord.abc.User, result: ActionResult
    ) -> str:
        t = translator.t
        parts = [
            t(
                locale,
                f"mod.done.{kind}",
                target=target.mention,
                case_no=result.case.case_no,
                duration=format_duration(result.case.duration_seconds or 0),
            )
        ]
        if result.dm_sent is False:
            parts.append(t(locale, "mod.done.dm_failed"))
        if result.escalation is not None:
            action = t(locale, f"mod.type.{result.escalation.type}")
            text = f"{action} (Case #{result.escalation.case_no})"
            parts.append(t(locale, "mod.done.escalated", text=text))
        if result.escalation_error is not None:
            error = t(locale, result.escalation_error)
            parts.append(t(locale, "mod.done.escalation_failed", error=error))
        return "\n".join(parts)

    async def _act(
        self,
        interaction: discord.Interaction,
        target: discord.abc.User,
        needed: dict[str, bool],
        *,
        check_hierarchy: bool = True,
    ) -> tuple[discord.Guild, str, DiscordModerationPort]:
        guild = ensure_guild(interaction)
        ensure_bot_permissions(interaction, **needed)
        if check_hierarchy:
            await self._check_target(interaction, guild, target)
        await interaction.response.defer(ephemeral=True)
        return guild, await self._locale(guild), self._port(guild)

    # --- Aktionen ---

    @app_commands.command(name="warn", description=d("cmd.warn.desc"))
    @app_commands.describe(member=d("cmd.arg.member.desc"), reason=d("cmd.arg.reason.desc"))
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def warn(
        self, interaction: discord.Interaction, member: discord.Member, reason: Reason | None = None
    ) -> None:
        guild, locale, port = await self._act(interaction, member, {})
        result = await self.bot.services.moderation.warn(
            port, guild.id, interaction.user.id, member.id, reason
        )
        await interaction.followup.send(self._done_text(locale, "warn", member, result))

    @app_commands.command(name="unwarn", description=d("cmd.unwarn.desc"))
    @app_commands.describe(case_no=d("cmd.arg.case_no.desc"))
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def unwarn(
        self, interaction: discord.Interaction, case_no: app_commands.Range[int, 1]
    ) -> None:
        guild = ensure_guild(interaction)
        case = await self.bot.services.moderation.unwarn(guild.id, case_no)
        locale = await self._locale(guild)
        await interaction.response.send_message(
            translator.t(locale, "mod.done.unwarn", case_no=case.case_no), ephemeral=True
        )

    @app_commands.command(name="timeout", description=d("cmd.timeout.desc"))
    @app_commands.describe(
        member=d("cmd.arg.member.desc"),
        duration=d("cmd.arg.duration.desc"),
        reason=d("cmd.arg.reason.desc"),
    )
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def timeout(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        duration: str,
        reason: Reason | None = None,
    ) -> None:
        seconds = parse_duration(duration)
        guild, locale, port = await self._act(interaction, member, {"moderate_members": True})
        result = await self.bot.services.moderation.timeout(
            port, guild.id, interaction.user.id, member.id, seconds, reason
        )
        await interaction.followup.send(self._done_text(locale, "timeout", member, result))

    @app_commands.command(name="untimeout", description=d("cmd.untimeout.desc"))
    @app_commands.describe(member=d("cmd.arg.member.desc"), reason=d("cmd.arg.reason.desc"))
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def untimeout(
        self, interaction: discord.Interaction, member: discord.Member, reason: Reason | None = None
    ) -> None:
        guild, locale, port = await self._act(interaction, member, {"moderate_members": True})
        result = await self.bot.services.moderation.untimeout(
            port, guild.id, interaction.user.id, member.id, reason
        )
        await interaction.followup.send(self._done_text(locale, "untimeout", member, result))

    @app_commands.command(name="kick", description=d("cmd.kick.desc"))
    @app_commands.describe(member=d("cmd.arg.member.desc"), reason=d("cmd.arg.reason.desc"))
    @app_commands.default_permissions(kick_members=True)
    @app_commands.guild_only()
    async def kick(
        self, interaction: discord.Interaction, member: discord.Member, reason: Reason | None = None
    ) -> None:
        guild, locale, port = await self._act(interaction, member, {"kick_members": True})
        result = await self.bot.services.moderation.kick(
            port, guild.id, interaction.user.id, member.id, reason
        )
        await interaction.followup.send(self._done_text(locale, "kick", member, result))

    @app_commands.command(name="ban", description=d("cmd.ban.desc"))
    @app_commands.describe(
        user=d("cmd.arg.user.desc"),
        reason=d("cmd.arg.reason.desc"),
        delete_days=d("cmd.arg.delete_days.desc"),
    )
    @app_commands.default_permissions(ban_members=True)
    @app_commands.guild_only()
    async def ban(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        reason: Reason | None = None,
        delete_days: DeleteDays = 0,
    ) -> None:
        guild, locale, port = await self._act(interaction, user, {"ban_members": True})
        result = await self.bot.services.moderation.ban(
            port, guild.id, interaction.user.id, user.id, reason, delete_days * 86400
        )
        await interaction.followup.send(self._done_text(locale, "ban", user, result))

    @app_commands.command(name="tempban", description=d("cmd.tempban.desc"))
    @app_commands.describe(
        user=d("cmd.arg.user.desc"),
        duration=d("cmd.arg.duration.desc"),
        reason=d("cmd.arg.reason.desc"),
        delete_days=d("cmd.arg.delete_days.desc"),
    )
    @app_commands.default_permissions(ban_members=True)
    @app_commands.guild_only()
    async def tempban(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        duration: str,
        reason: Reason | None = None,
        delete_days: DeleteDays = 0,
    ) -> None:
        seconds = parse_duration(duration)
        guild, locale, port = await self._act(interaction, user, {"ban_members": True})
        result = await self.bot.services.moderation.tempban(
            port, guild.id, interaction.user.id, user.id, seconds, reason, delete_days * 86400
        )
        await interaction.followup.send(self._done_text(locale, "tempban", user, result))

    @app_commands.command(name="softban", description=d("cmd.softban.desc"))
    @app_commands.describe(
        member=d("cmd.arg.member.desc"),
        reason=d("cmd.arg.reason.desc"),
        delete_days=d("cmd.arg.delete_days.desc"),
    )
    @app_commands.default_permissions(ban_members=True)
    @app_commands.guild_only()
    async def softban(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: Reason | None = None,
        delete_days: app_commands.Range[int, 1, 7] = 1,
    ) -> None:
        guild, locale, port = await self._act(interaction, member, {"ban_members": True})
        result = await self.bot.services.moderation.softban(
            port, guild.id, interaction.user.id, member.id, reason, delete_days * 86400
        )
        await interaction.followup.send(self._done_text(locale, "softban", member, result))

    @app_commands.command(name="unban", description=d("cmd.unban.desc"))
    @app_commands.describe(user=d("cmd.arg.user.desc"), reason=d("cmd.arg.reason.desc"))
    @app_commands.default_permissions(ban_members=True)
    @app_commands.guild_only()
    async def unban(
        self, interaction: discord.Interaction, user: discord.User, reason: Reason | None = None
    ) -> None:
        guild, locale, port = await self._act(
            interaction, user, {"ban_members": True}, check_hierarchy=False
        )
        result = await self.bot.services.moderation.unban(
            port, guild.id, interaction.user.id, user.id, reason
        )
        await interaction.followup.send(self._done_text(locale, "unban", user, result))

    # --- Nachschlagen ---

    @app_commands.command(name="case", description=d("cmd.case.desc"))
    @app_commands.describe(case_no=d("cmd.arg.case_no.desc"))
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def case(
        self, interaction: discord.Interaction, case_no: app_commands.Range[int, 1]
    ) -> None:
        guild = ensure_guild(interaction)
        case = await self.bot.services.moderation.get_case(guild.id, case_no)
        embed = case_embed(case, await self._locale(guild), await self._color(guild))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="history", description=d("cmd.history.desc"))
    @app_commands.describe(user=d("cmd.arg.user.desc"), page=d("cmd.arg.page.desc"))
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def history(
        self,
        interaction: discord.Interaction,
        user: discord.User,
        page: app_commands.Range[int, 1] = 1,
    ) -> None:
        guild = ensure_guild(interaction)
        result = await self.bot.services.moderation.history(guild.id, user.id, page)
        embed = history_embed(
            result, str(user), await self._locale(guild), await self._color(guild)
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # --- Notizen ---

    note = app_commands.Group(
        name="note",
        description=d("cmd.note.desc"),
        guild_only=True,
        default_permissions=discord.Permissions(moderate_members=True),
    )

    @note.command(name="add", description=d("cmd.note.add.desc"))
    @app_commands.describe(user=d("cmd.arg.user.desc"), text=d("cmd.arg.text.desc"))
    async def note_add(
        self, interaction: discord.Interaction, user: discord.User, text: str
    ) -> None:
        guild = ensure_guild(interaction)
        saved = await self.bot.services.moderation.add_note(
            guild.id, user.id, interaction.user.id, text
        )
        locale = await self._locale(guild)
        await interaction.response.send_message(
            translator.t(locale, "mod.note.added", note_id=saved.id), ephemeral=True
        )

    @note.command(name="list", description=d("cmd.note.list.desc"))
    @app_commands.describe(user=d("cmd.arg.user.desc"))
    async def note_list(self, interaction: discord.Interaction, user: discord.User) -> None:
        guild = ensure_guild(interaction)
        notes = await self.bot.services.moderation.list_notes(guild.id, user.id)
        embed = notes_embed(notes, str(user), await self._locale(guild), await self._color(guild))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @note.command(name="remove", description=d("cmd.note.remove.desc"))
    @app_commands.describe(note_id=d("cmd.arg.note_id.desc"))
    async def note_remove(
        self, interaction: discord.Interaction, note_id: app_commands.Range[int, 1]
    ) -> None:
        guild = ensure_guild(interaction)
        await self.bot.services.moderation.remove_note(guild.id, note_id)
        locale = await self._locale(guild)
        await interaction.response.send_message(
            translator.t(locale, "mod.note.removed", note_id=note_id), ephemeral=True
        )


async def setup(bot: GGBot) -> None:
    await bot.add_cog(ModerationCog(bot))
