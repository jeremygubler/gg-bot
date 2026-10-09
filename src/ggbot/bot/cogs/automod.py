"""Automod: prüft Nachrichten und Beitritte und setzt die Entscheidungen des Service um."""

import asyncio
from collections import defaultdict
from collections.abc import Awaitable

import discord
from discord.ext import commands

from ggbot.bot.client import GGBot
from ggbot.bot.ports import DiscordModerationPort
from ggbot.core import events
from ggbot.core.errors import UserError
from ggbot.core.i18n import translator
from ggbot.core.logging import get_logger
from ggbot.core.services.automod import (
    JoinContext,
    JoinDecision,
    MessageContext,
    MessageRef,
    Violation,
)
from ggbot.core.services.moderation import ModerationPort
from ggbot.core.settings.automod import AutomodSettings

log = get_logger(__name__)

NOTICE_SECONDS = 8
JOIN_BATCH = 5
JOIN_PAUSE_SECONDS = 1.0
LOG_CONTENT_LIMIT = 500
Messageable = discord.TextChannel | discord.VoiceChannel | discord.StageChannel | discord.Thread


class AutomodCog(commands.Cog):
    def __init__(self, bot: GGBot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        # Fehlende Intents dürfen nicht still zu einem Bot führen, der nichts sieht.
        if not self.bot.intents.message_content:
            log.warning("automod_message_rules_inactive", reason="Message Content Intent fehlt")
        if not self.bot.intents.members:
            log.warning("automod_join_rules_inactive", reason="Members Intent fehlt")

    # --- Ereignisse ---

    @commands.Cog.listener()
    async def on_ggbot_settings_changed(self, event: events.SettingsChanged) -> None:
        if event.module == "automod":
            self.bot.services.automod.invalidate(event.guild_id)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        await self._check_message(message, is_edit=False)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        # Auch Link-Vorschauen lösen Bearbeitungen aus; nur echte Textänderungen prüfen.
        if before.content != after.content:
            await self._check_message(after, is_edit=True)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        decision = await self.bot.services.automod.evaluate_join(
            JoinContext(member.guild.id, member.id, member.bot, member.created_at)
        )
        if decision is not None:
            await self._enforce_join(member.guild, decision)

    # --- Nachrichten ---

    async def _check_message(self, message: discord.Message, *, is_edit: bool) -> None:
        guild = message.guild
        author = message.author
        if guild is None or message.webhook_id is not None or author.bot:
            return
        if message.type not in (discord.MessageType.default, discord.MessageType.reply):
            return
        if not isinstance(author, discord.Member):
            return
        channel = message.channel
        context = MessageContext(
            guild_id=guild.id,
            channel_id=channel.id,
            parent_channel_id=channel.parent_id if isinstance(channel, discord.Thread) else None,
            message_id=message.id,
            user_id=author.id,
            content=message.content,
            role_ids=frozenset(role.id for role in author.roles),
            mentioned_user_ids=frozenset(user.id for user in message.mentions),
            mentioned_role_ids=frozenset(role.id for role in message.role_mentions),
            can_manage_messages=author.guild_permissions.manage_messages,
            is_edit=is_edit,
        )
        violation = await self.bot.services.automod.evaluate_message(context)
        if violation is not None:
            await self._enforce(message, author, violation)

    async def _enforce(
        self, message: discord.Message, author: discord.Member, violation: Violation
    ) -> None:
        guild = message.guild
        assert guild is not None
        services = self.bot.services
        locale = await services.guilds.get_locale(guild.id)
        label = translator.t(locale, f"automod.rule.{violation.rule}")
        reason = translator.t(locale, "automod.reason", rule=label)
        action = violation.action
        done: list[str] = []
        failures: list[str] = []

        if action.delete:
            if await self._delete(guild, violation.delete):
                done.append("delete")
            else:
                failures.append("error.automod_cannot_delete")

        if violation.punish:
            port = DiscordModerationPort(self.bot, guild)
            if action.warn:
                await self._try(
                    failures,
                    done,
                    "warn",
                    services.moderation.warn(port, guild.id, port.bot_user_id, author.id, reason),
                )
            if action.timeout_seconds:
                await self._try(
                    failures,
                    done,
                    "timeout",
                    services.moderation.timeout(
                        port, guild.id, port.bot_user_id, author.id, action.timeout_seconds, reason
                    ),
                )
            if "delete" in done:
                await self._notify(message, author, locale, label)
            settings = await services.automod.settings(guild.id)
            await self._log(guild, settings, locale, message, author, violation, done, failures)

        await services.automod.record_hit(
            guild.id, author.id, message.channel.id, violation.rule, done
        )

    @staticmethod
    async def _try(
        failures: list[str], done: list[str], name: str, call: Awaitable[object]
    ) -> None:
        """Führt eine Moderationsaktion aus. Scheitert sie erwartbar, wird das gemeldet."""
        try:
            await call
        except UserError as exc:
            log.warning("automod_action_failed", action=name, key=exc.key)
            failures.append(exc.key)
        else:
            done.append(name)

    async def _delete(self, guild: discord.Guild, refs: tuple[MessageRef, ...]) -> bool:
        """False, wenn dem Bot das Recht zum Löschen fehlt. Schon gelöschtes ist kein Fehler."""
        by_channel: dict[int, list[int]] = defaultdict(list)
        for channel_id, message_id in refs:
            by_channel[channel_id].append(message_id)
        allowed = True
        for channel_id, ids in by_channel.items():
            channel = guild.get_channel_or_thread(channel_id)
            if not isinstance(channel, Messageable):
                continue
            try:
                if len(ids) == 1:
                    await channel.get_partial_message(ids[0]).delete()
                else:
                    await channel.delete_messages([discord.Object(i) for i in ids[:100]])
            except discord.NotFound:
                continue  # jemand war schneller
            except discord.Forbidden:
                allowed = False
        return allowed

    async def _notify(
        self, message: discord.Message, author: discord.Member, locale: str, label: str
    ) -> None:
        text = translator.t(locale, "automod.notice", user=author.mention, rule=label)
        try:
            await message.channel.send(text, delete_after=NOTICE_SECONDS)
        except discord.Forbidden:
            log.debug("automod_notice_not_allowed", channel_id=message.channel.id)

    async def _log(
        self,
        guild: discord.Guild,
        settings: AutomodSettings,
        locale: str,
        message: discord.Message,
        author: discord.Member,
        violation: Violation,
        done: list[str],
        failures: list[str],
    ) -> None:
        channel = guild.get_channel(settings.log_channel_id) if settings.log_channel_id else None
        if not isinstance(channel, discord.TextChannel):
            return
        t = translator.t
        embed = discord.Embed(
            title=t(locale, "automod.log.title", rule=t(locale, f"automod.rule.{violation.rule}")),
            color=discord.Color.orange(),
        )
        embed.add_field(
            name=t(locale, "automod.log.user"), value=f"{author.mention}\n`{author.id}`"
        )
        embed.add_field(name=t(locale, "automod.log.channel"), value=f"<#{message.channel.id}>")
        embed.add_field(
            name=t(locale, "automod.log.actions"),
            value=", ".join(t(locale, f"automod.action.{name}") for name in done)
            or t(locale, "automod.log.none"),
        )
        if violation.detail:
            embed.add_field(
                name=t(locale, "automod.log.trigger"), value=f"`{violation.detail[:100]}`"
            )
        if message.content:
            content = message.content[:LOG_CONTENT_LIMIT].replace("```", "'''")
            embed.add_field(
                name=t(locale, "automod.log.content"), value=f"```{content}```", inline=False
            )
        if failures:
            embed.add_field(
                name=t(locale, "automod.log.failed"),
                value="\n".join(t(locale, key) for key in failures),
                inline=False,
            )
        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            log.warning("automod_log_not_allowed", guild_id=guild.id, channel_id=channel.id)

    # --- Beitritte ---

    async def _enforce_join(self, guild: discord.Guild, decision: JoinDecision) -> None:
        services = self.bot.services
        locale = await services.guilds.get_locale(guild.id)
        reason = translator.t(
            locale, f"automod.join_reason.{decision.rule}", **decision.reason_params
        )
        port = DiscordModerationPort(self.bot, guild)
        failed = 0
        for start in range(0, len(decision.targets), JOIN_BATCH):
            if start:
                await asyncio.sleep(JOIN_PAUSE_SECONDS)
            for user_id in decision.targets[start : start + JOIN_BATCH]:
                if not await self._apply_join_action(port, guild, decision, user_id, reason):
                    failed += 1

        if decision.raid_started:
            log.warning("raid_detected", guild_id=guild.id, joins=len(decision.targets))
            await self._raid_alert(guild, locale, decision, failed)

    async def _apply_join_action(
        self,
        port: ModerationPort,
        guild: discord.Guild,
        decision: JoinDecision,
        user_id: int,
        reason: str,
    ) -> bool:
        moderation = self.bot.services.moderation
        action = decision.action
        bot_id = port.bot_user_id
        try:
            if action.action == "kick":
                await moderation.kick(port, guild.id, bot_id, user_id, reason)
            elif action.action == "ban":
                await moderation.ban(port, guild.id, bot_id, user_id, reason)
            else:
                assert action.timeout_seconds is not None
                await moderation.timeout(
                    port, guild.id, bot_id, user_id, action.timeout_seconds, reason
                )
        except UserError as exc:
            log.warning("automod_join_action_failed", user_id=user_id, key=exc.key)
            return False
        await self.bot.services.automod.record_hit(
            guild.id, user_id, None, decision.rule, [action.action]
        )
        return True

    async def _raid_alert(
        self, guild: discord.Guild, locale: str, decision: JoinDecision, failed: int
    ) -> None:
        settings = await self.bot.services.automod.settings(guild.id)
        channel = (
            guild.get_channel(settings.log_channel_id) if settings.log_channel_id else None
        ) or guild.system_channel
        if not isinstance(channel, discord.TextChannel):
            return
        text = translator.t(
            locale,
            "automod.raid_alert",
            joins=len(decision.targets),
            seconds=settings.raid.seconds,
            minutes=settings.raid.mode_minutes,
            action=translator.t(locale, f"automod.action.{decision.action.action}"),
        )
        if failed:
            text += "\n" + translator.t(locale, "automod.raid_failed", count=failed)
        try:
            await channel.send(text)
        except discord.Forbidden:
            log.warning("raid_alert_not_allowed", guild_id=guild.id, channel_id=channel.id)


async def setup(bot: GGBot) -> None:
    await bot.add_cog(AutomodCog(bot))
