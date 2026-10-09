"""Event-Log: übersetzt Discord-Ereignisse in Einträge und schickt sie in den gewählten Kanal."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import discord
from discord.ext import commands

from ggbot.bot.client import GGBot
from ggbot.bot.eventlog_send import LogSender
from ggbot.bot.log_diff import channel_changes, role_changes, snapshot_of, user_ref
from ggbot.core import events
from ggbot.core.logging import get_logger
from ggbot.core.logs import entries
from ggbot.core.logs.entries import Executor, LogEntry

log = get_logger(__name__)

# Der Audit-Log-Eintrag entsteht kurz nach dem Gateway-Ereignis. Ohne Pause fände man ihn nicht.
AUDIT_DELAY_SECONDS = 1.0
AUDIT_WINDOW = timedelta(seconds=20)
AUDIT_CONCURRENCY = 3

Builder = Callable[[str], Awaitable[LogEntry | None]]


def _now() -> datetime:
    return datetime.now(UTC)


class EventLogCog(commands.Cog):
    def __init__(self, bot: GGBot) -> None:
        self.bot = bot
        self.sender = LogSender()
        self._audit_slots = asyncio.Semaphore(AUDIT_CONCURRENCY)

    async def cog_unload(self) -> None:
        await self.sender.aclose()

    # --- Gemeinsames ---

    async def _dispatch(
        self,
        guild: discord.Guild | None,
        event: str,
        build: Builder,
        *,
        channel_id: int | None = None,
        is_bot: bool = False,
    ) -> None:
        """Erst prüfen, ob die Art an ist; teure Schritte (Audit-Log) laufen erst danach."""
        if guild is None:
            return
        services = self.bot.services
        target_id = await services.eventlog.route(
            guild.id, event, channel_id=channel_id, is_bot=is_bot
        )
        if target_id is None:
            return
        channel = guild.get_channel(target_id)
        if not isinstance(channel, discord.TextChannel):
            log.warning("eventlog_channel_missing", guild_id=guild.id, channel_id=target_id)
            return
        locale = await services.guilds.get_locale(guild.id)
        entry = await build(locale)
        if entry is not None:
            self.sender.enqueue(channel, entry, locale)

    async def _executor(
        self, guild: discord.Guild, action: discord.AuditLogAction, target_id: int | None
    ) -> Executor | None:
        """Wer es getan hat, laut Audit-Log. None, wenn das Recht fehlt oder nichts passt."""
        if not guild.me.guild_permissions.view_audit_log:
            return None
        async with self._audit_slots:
            await asyncio.sleep(AUDIT_DELAY_SECONDS)
            try:
                async for entry in guild.audit_logs(limit=6, action=action):
                    target = getattr(entry.target, "id", None)
                    if target_id is not None and target != target_id:
                        continue
                    if _now() - entry.created_at > AUDIT_WINDOW:
                        continue
                    return Executor(entry.user_id, entry.reason)
            except discord.Forbidden:
                log.debug("eventlog_audit_forbidden", guild_id=guild.id)
        return None

    @commands.Cog.listener()
    async def on_ggbot_settings_changed(self, event: events.SettingsChanged) -> None:
        if event.module == "logging":
            self.bot.services.eventlog.invalidate(event.guild_id)

    # --- Nachrichten ---

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        guild = self.bot.get_guild(payload.guild_id) if payload.guild_id else None
        cached = payload.cached_message
        snapshot = snapshot_of(cached) if cached is not None else None

        async def build(locale: str) -> LogEntry:
            return entries.message_deleted(
                locale, payload.channel_id, payload.message_id, snapshot, _now()
            )

        await self._dispatch(
            guild,
            "message_delete",
            build,
            channel_id=payload.channel_id,
            is_bot=bool(cached and cached.author.bot),
        )

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        guild = self.bot.get_guild(payload.guild_id) if payload.guild_id else None
        after = payload.message
        before = payload.cached_message
        if before is not None:
            if before.content == after.content:
                return  # Link-Vorschau, angeheftet o. ä.
        elif not payload.data.get("edited_timestamp"):
            return  # ohne bekannten Vorzustand zählt nur eine echte Bearbeitung

        async def build(locale: str) -> LogEntry:
            return entries.message_edited(
                locale,
                after.jump_url,
                user_ref(after.author),
                payload.channel_id,
                before.content if before is not None else None,
                after.content,
                _now(),
            )

        await self._dispatch(
            guild,
            "message_edit",
            build,
            channel_id=payload.channel_id,
            is_bot=after.author.bot,
        )

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent) -> None:
        guild = self.bot.get_guild(payload.guild_id) if payload.guild_id else None
        ids = sorted(payload.message_ids)
        cached = [snapshot_of(m) for m in payload.cached_messages]

        async def build(locale: str) -> LogEntry:
            return entries.messages_bulk_deleted(locale, payload.channel_id, ids, cached, _now())

        await self._dispatch(guild, "message_bulk", build, channel_id=payload.channel_id)

    # --- Mitglieder ---

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        async def build(locale: str) -> LogEntry:
            return entries.member_joined(
                locale, user_ref(member), member.created_at, member.guild.member_count, _now()
            )

        await self._dispatch(member.guild, "member_join", build)

    @commands.Cog.listener()
    async def on_raw_member_remove(self, payload: discord.RawMemberRemoveEvent) -> None:
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        # In diesem Moment ist das Mitglied noch im Cache: Beitrittsdatum und Rollen sind bekannt.
        member = guild.get_member(payload.user.id)

        async def build(locale: str) -> LogEntry:
            kicked = await self._executor(guild, discord.AuditLogAction.kick, payload.user.id)
            roles = tuple(r.name for r in member.roles[1:]) if member else ()
            return entries.member_left(
                locale,
                user_ref(payload.user),
                member.joined_at if member else None,
                roles,
                kicked,
                _now(),
            )

        await self._dispatch(guild, "member_leave", build)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        old_roles, new_roles = set(before.roles), set(after.roles)
        added = tuple(sorted(r.name for r in new_roles - old_roles))
        removed = tuple(sorted(r.name for r in old_roles - new_roles))
        nick = (before.nick, after.nick)
        timeout = (before.timed_out_until, after.timed_out_until)
        if not (added or removed or nick[0] != nick[1] or timeout[0] != timeout[1]):
            return

        async def build(locale: str) -> LogEntry | None:
            action = (
                discord.AuditLogAction.member_role_update
                if added or removed
                else discord.AuditLogAction.member_update
            )
            executor = await self._executor(after.guild, action, after.id)
            return entries.member_updated(
                locale, user_ref(after), added, removed, nick, timeout, executor, _now()
            )

        await self._dispatch(after.guild, "member_update", build)

    @commands.Cog.listener()
    async def on_member_ban(
        self, guild: discord.Guild, user: discord.User | discord.Member
    ) -> None:
        async def build(locale: str) -> LogEntry:
            executor = await self._executor(guild, discord.AuditLogAction.ban, user.id)
            return entries.member_banned(locale, user_ref(user), executor, _now())

        await self._dispatch(guild, "member_ban", build)

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User) -> None:
        async def build(locale: str) -> LogEntry:
            executor = await self._executor(guild, discord.AuditLogAction.unban, user.id)
            return entries.member_unbanned(locale, user_ref(user), executor, _now())

        await self._dispatch(guild, "member_unban", build)

    # --- Rollen ---

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role) -> None:
        async def build(locale: str) -> LogEntry | None:
            executor = await self._executor(role.guild, discord.AuditLogAction.role_create, role.id)
            return entries.role_event(
                locale, "role_create", role.name, role.id, [], executor, _now()
            )

        await self._dispatch(role.guild, "role_create", build)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        async def build(locale: str) -> LogEntry | None:
            executor = await self._executor(role.guild, discord.AuditLogAction.role_delete, role.id)
            return entries.role_event(
                locale, "role_delete", role.name, role.id, [], executor, _now()
            )

        await self._dispatch(role.guild, "role_delete", build)

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role) -> None:
        changes = role_changes(before, after)
        if not changes:
            return  # nur umsortiert

        async def build(locale: str) -> LogEntry | None:
            executor = await self._executor(
                after.guild, discord.AuditLogAction.role_update, after.id
            )
            return entries.role_event(
                locale, "role_update", after.name, after.id, changes, executor, _now()
            )

        await self._dispatch(after.guild, "role_update", build)

    # --- Kanäle ---

    async def _channel_entry(
        self,
        locale: str,
        event: str,
        channel: discord.abc.GuildChannel,
        action: discord.AuditLogAction,
        changes: list[entries.Change],
    ) -> LogEntry | None:
        executor = await self._executor(channel.guild, action, channel.id)
        return entries.channel_event(
            locale, event, channel.name, channel.id, channel.type.name, changes, executor, _now()
        )

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        async def build(locale: str) -> LogEntry | None:
            return await self._channel_entry(
                locale, "channel_create", channel, discord.AuditLogAction.channel_create, []
            )

        await self._dispatch(channel.guild, "channel_create", build)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        async def build(locale: str) -> LogEntry | None:
            return await self._channel_entry(
                locale, "channel_delete", channel, discord.AuditLogAction.channel_delete, []
            )

        await self._dispatch(channel.guild, "channel_delete", build)

    @commands.Cog.listener()
    async def on_guild_channel_update(
        self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel
    ) -> None:
        changes = channel_changes(before, after)
        if not changes:
            return  # Position oder Berechtigungen: zu geräuschvoll

        async def build(locale: str) -> LogEntry | None:
            return await self._channel_entry(
                locale, "channel_update", after, discord.AuditLogAction.channel_update, changes
            )

        await self._dispatch(after.guild, "channel_update", build)

    # --- Sprache ---

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ) -> None:
        old = (before.channel.id, before.channel.name) if before.channel else None
        new = (after.channel.id, after.channel.name) if after.channel else None
        if old == new:
            return  # Stummschaltung o. ä.

        async def build(locale: str) -> LogEntry | None:
            return entries.voice_changed(locale, user_ref(member), old, new, _now())

        await self._dispatch(member.guild, "voice", build, is_bot=member.bot)

    # --- Einladungen ---

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite) -> None:
        guild = self.bot.get_guild(invite.guild.id) if invite.guild else None

        async def build(locale: str) -> LogEntry:
            return entries.invite_created(
                locale,
                invite.code or "?",
                invite.channel.id if invite.channel else None,
                user_ref(invite.inviter) if invite.inviter else None,
                invite.max_uses or 0,
                invite.max_age or 0,
                bool(invite.temporary),
                _now(),
            )

        await self._dispatch(guild, "invite_create", build)

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite) -> None:
        guild = self.bot.get_guild(invite.guild.id) if invite.guild else None

        async def build(locale: str) -> LogEntry:
            return entries.invite_deleted(
                locale, invite.code or "?", invite.channel.id if invite.channel else None, _now()
            )

        await self._dispatch(guild, "invite_delete", build)


async def setup(bot: GGBot) -> None:
    await bot.add_cog(EventLogCog(bot))
