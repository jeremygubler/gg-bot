"""Geburtstage: eintragen, ankündigen, optional eine Rolle für den Tag. Dazu /privacy."""

import asyncio
import contextlib
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands
from pydantic import ValidationError

from ggbot.bot.checks import ensure_grantable, ensure_guild
from ggbot.bot.client import GGBot
from ggbot.bot.i18n import d
from ggbot.bot.rewards import assignable
from ggbot.core import messages
from ggbot.core.community import birthday as rules
from ggbot.core.errors import UserError
from ggbot.core.i18n import translator
from ggbot.core.logging import get_logger
from ggbot.core.redis import key
from ggbot.core.settings.birthday import FIELDS, BirthdaySettings

log = get_logger(__name__)

CHECK_SECONDS = 1800
GATE_SECONDS = 3 * 86400
RESET_WORD = "DELETE"
MONTHS = [
    app_commands.Choice(name=str(m), value=m) for m in range(1, 13)
]  # Auswahl statt Freitext: so kann kein ungültiger Monat entstehen


class BirthdayCog(commands.Cog):
    def __init__(self, bot: GGBot) -> None:
        self.bot = bot
        self._task: asyncio.Task[None] | None = None

    async def cog_load(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="birthday-check")

    async def cog_unload(self) -> None:
        if self._task is not None:
            self._task.cancel()

    @commands.Cog.listener()
    async def on_ggbot_settings_changed(self, event: Any) -> None:
        if event.module == "birthday":
            self.bot.services.birthdays.invalidate(event.guild_id)

    # --- Hintergrund ---

    async def _loop(self) -> None:
        while True:
            if self.bot.is_ready():
                try:
                    await self.check(datetime.now(UTC))
                except Exception:
                    log.exception("birthday_check_failed")
                await asyncio.sleep(CHECK_SECONDS)
            else:
                await asyncio.sleep(5)

    async def check(self, now: datetime) -> None:
        for guild in self.bot.guilds:
            try:
                await self._check_guild(guild, now)
            except Exception:
                log.exception("birthday_guild_failed", guild_id=guild.id)

    async def _member(self, guild: discord.Guild, user_id: int) -> discord.Member | None:
        member = guild.get_member(user_id)
        if member is not None:
            return member
        try:
            return await guild.fetch_member(user_id)
        except discord.HTTPException:
            return None

    async def _check_guild(self, guild: discord.Guild, now: datetime) -> None:
        services = self.bot.services
        settings = await services.birthdays.settings(guild.id)
        if not settings.enabled:
            return
        today = rules.local_today(now, await services.guilds.get_timezone(guild.id))
        ids = await services.birthdays.today(guild.id, today)
        members = [m for uid in ids if (m := await self._member(guild, uid)) and not m.bot]
        await self._sync_role(guild, settings, members)
        if not members:
            return
        # Genau eine Ankündigung pro Tag und Server, auch bei Neustart oder mehreren Prozessen.
        gate = key("birthday", guild.id, today.isoformat())
        if not await services.redis.set(gate, "1", ex=GATE_SECONDS, nx=True):
            return
        channel = guild.get_channel(settings.channel_id or 0)
        if not isinstance(channel, discord.TextChannel):
            return
        locale = await services.guilds.get_locale(guild.id)
        template = settings.message or translator.t(locale, "birthday.default")
        for member in members:
            text = messages.render_template(
                template, FIELDS, user=member.mention, name=member.display_name, server=guild.name
            )
            with contextlib.suppress(discord.HTTPException):
                await channel.send(
                    text,
                    # {name} ist frei wählbar: gepingt wird nur das Geburtstagskind.
                    allowed_mentions=discord.AllowedMentions(
                        everyone=False, roles=False, users=[member], replied_user=False
                    ),
                )

    async def _sync_role(
        self, guild: discord.Guild, settings: BirthdaySettings, today: list[discord.Member]
    ) -> None:
        """Die Rolle haben genau die Geburtstagskinder von heute."""
        role = guild.get_role(settings.role_id) if settings.role_id else None
        if role is None or not assignable(guild, role):
            return
        keep = {m.id for m in today}
        for member in role.members:
            if member.id not in keep:
                with contextlib.suppress(discord.HTTPException):
                    await member.remove_roles(role, reason="Geburtstag vorbei")
        for member in today:
            if role not in member.roles:
                with contextlib.suppress(discord.HTTPException):
                    await member.add_roles(role, reason="Geburtstag")

    # --- Befehle für alle ---

    birthday = app_commands.Group(
        name="birthday", description=d("cmd.birthday.desc"), guild_only=True
    )

    async def _reply(self, interaction: discord.Interaction, key_: str, **params: object) -> None:
        locale = await self.bot.services.guilds.get_locale(interaction.guild_id)
        await interaction.response.send_message(
            translator.t(locale, key_, **params),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @birthday.command(name="set", description=d("cmd.birthday.set.desc"))
    @app_commands.describe(day=d("cmd.arg.bd_day.desc"), month=d("cmd.arg.bd_month.desc"))
    @app_commands.choices(month=MONTHS)
    async def set_(
        self,
        interaction: discord.Interaction,
        day: app_commands.Range[int, 1, 31],
        month: app_commands.Choice[int],
    ) -> None:
        guild = ensure_guild(interaction)
        await self.bot.services.birthdays.set(guild.id, interaction.user.id, day, month.value)
        await self._reply(interaction, "birthday.saved", day=day, month=month.value)

    @birthday.command(name="remove", description=d("cmd.birthday.remove.desc"))
    async def remove(self, interaction: discord.Interaction) -> None:
        guild = ensure_guild(interaction)
        await self.bot.services.birthdays.remove(guild.id, interaction.user.id)
        await self._reply(interaction, "birthday.removed")

    @birthday.command(name="show", description=d("cmd.birthday.show.desc"))
    @app_commands.describe(member=d("cmd.arg.member.desc"))
    async def show(
        self, interaction: discord.Interaction, member: discord.Member | None = None
    ) -> None:
        guild = ensure_guild(interaction)
        target = member or interaction.user
        found = await self.bot.services.birthdays.get(guild.id, target.id)
        if found is None:
            raise UserError("error.bd_unknown_user")
        await self._reply(
            interaction, "birthday.show", user=target.mention, day=found[0], month=found[1]
        )

    @birthday.command(name="upcoming", description=d("cmd.birthday.upcoming.desc"))
    async def upcoming(self, interaction: discord.Interaction) -> None:
        guild = ensure_guild(interaction)
        services = self.bot.services
        locale = await services.guilds.get_locale(guild.id)
        today = rules.local_today(datetime.now(UTC), await services.guilds.get_timezone(guild.id))
        rows = [
            r
            for r in await services.birthdays.upcoming(guild.id, today, limit=40)
            if guild.get_member(r[0]) is not None
        ][:10]
        if not rows:
            text = translator.t(locale, "birthday.upcoming_empty")
        else:
            text = "\n".join(
                translator.t(
                    locale,
                    "birthday.upcoming_line",
                    user=f"<@{user}>",
                    day=day,
                    month=month,
                    days=days,
                )
                for user, day, month, days in rows
            )
        await interaction.response.send_message(
            text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )

    # --- Einstellungen ---

    birthdayconfig = app_commands.Group(
        name="birthdayconfig",
        description=d("cmd.birthdayconfig.desc"),
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    async def _edit(
        self,
        interaction: discord.Interaction,
        mutate: Callable[[Any], None],
        reply_key: str,
        **params: object,
    ) -> None:
        guild = ensure_guild(interaction)
        services = self.bot.services
        data = (await services.birthdays.settings(guild.id)).model_dump()
        mutate(data)
        try:
            updated = BirthdaySettings.model_validate(data)
        except ValidationError as exc:
            error = exc.errors()[0]
            detail = f"{'.'.join(str(p) for p in error['loc'])}: {error['msg']}"[:150]
            raise UserError("error.birthday_invalid", detail=detail) from exc
        await services.settings.save(guild.id, updated)
        services.birthdays.invalidate(guild.id)
        await self._reply(interaction, reply_key, **params)

    @birthdayconfig.command(name="set", description=d("cmd.birthdayconfig.set.desc"))
    @app_commands.describe(
        channel=d("cmd.arg.channel.desc"),
        role=d("cmd.arg.bd_role.desc"),
        message=d("cmd.arg.bd_message.desc"),
    )
    async def config_set(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        role: discord.Role | None = None,
        message: str | None = None,
    ) -> None:
        guild = ensure_guild(interaction)
        have = channel.permissions_for(guild.me)
        if not (have.view_channel and have.send_messages):
            raise UserError(
                "error.missing_bot_permissions", permissions="View Channel, Send Messages"
            )
        if role is not None:
            ensure_grantable(interaction, role)

        def mutate(data: dict[str, Any]) -> None:
            data.update(enabled=True, channel_id=channel.id)
            if role is not None:
                data["role_id"] = role.id
            if message is not None:
                data["message"] = message

        await self._edit(interaction, mutate, "birthday.config_set", channel=channel.mention)

    @birthdayconfig.command(name="off", description=d("cmd.birthdayconfig.off.desc"))
    async def config_off(self, interaction: discord.Interaction) -> None:
        await self._edit(
            interaction, lambda data: data.update(enabled=False), "birthday.config_off"
        )

    @birthdayconfig.command(name="clear_role", description=d("cmd.birthdayconfig.clear_role.desc"))
    async def clear_role(self, interaction: discord.Interaction) -> None:
        await self._edit(
            interaction, lambda data: data.update(role_id=None), "birthday.role_cleared"
        )


class PrivacyCog(commands.Cog):
    """`/privacy`: zeigt, was der Bot über dich auf diesem Server speichert, und löscht es."""

    def __init__(self, bot: GGBot) -> None:
        self.bot = bot

    privacy = app_commands.Group(name="privacy", description=d("cmd.privacy.desc"), guild_only=True)

    @privacy.command(name="show", description=d("cmd.privacy.show.desc"))
    async def show(self, interaction: discord.Interaction) -> None:
        guild = ensure_guild(interaction)
        services = self.bot.services
        user_id = interaction.user.id
        locale = await services.guilds.get_locale(guild.id)
        t = translator.t
        xp = await services.leveling.rank(guild.id, user_id)
        birthday = await services.birthdays.get(guild.id, user_id)
        reminders = await services.engagement.pending_reminders(guild.id, user_id)
        text = t(
            locale,
            "privacy.show",
            xp=xp.progress.xp,
            birthday=f"{birthday[0]}.{birthday[1]}." if birthday else t(locale, "welcome.none"),
            reminders=len(reminders),
            word=RESET_WORD,
        )
        await interaction.response.send_message(text, ephemeral=True)

    @privacy.command(name="delete", description=d("cmd.privacy.delete.desc"))
    @app_commands.describe(confirm=d("cmd.arg.privacy_confirm.desc"))
    async def delete(self, interaction: discord.Interaction, confirm: str) -> None:
        guild = ensure_guild(interaction)
        if confirm.strip() != RESET_WORD:
            raise UserError("error.privacy_confirm", word=RESET_WORD)
        services = self.bot.services
        user_id = interaction.user.id
        await services.birthdays.delete_user(guild.id, user_id)
        await services.leveling.reset_user(guild.id, user_id)
        await services.leveling.reset_card_style(guild.id, user_id)
        await services.engagement.cancel_all_reminders(guild.id, user_id)
        locale = await services.guilds.get_locale(guild.id)
        await interaction.response.send_message(
            translator.t(locale, "privacy.deleted"), ephemeral=True
        )


async def setup(bot: GGBot) -> None:
    await bot.add_cog(BirthdayCog(bot))
    await bot.add_cog(PrivacyCog(bot))
