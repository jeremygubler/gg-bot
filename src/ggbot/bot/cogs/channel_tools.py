"""Kanal-Werkzeuge: purge, slowmode, lock, unlock."""

import asyncio
from datetime import UTC, datetime

import discord
from discord import app_commands
from discord.ext import commands

from ggbot.bot.checks import ensure_bot_permissions, ensure_channel_permissions, ensure_guild
from ggbot.bot.client import GGBot
from ggbot.bot.i18n import d
from ggbot.core.bulk import chunked
from ggbot.core.errors import UserError
from ggbot.core.i18n import translator
from ggbot.core.purge import (
    BULK_DELETE_MAX_AGE,
    MessageInfo,
    PurgeFilter,
    select_for_purge,
)

MAX_PURGE = 500
# Wie viele Nachrichten höchstens durchsucht werden, um `amount` Treffer zu finden.
MAX_SCAN = 1000
BULK_CHUNK = 100  # Limit von Discord pro Bulk-Delete
CHUNK_PAUSE_SECONDS = 1.0
MAX_SLOWMODE = 21600

Purgeable = discord.TextChannel | discord.VoiceChannel | discord.StageChannel | discord.Thread
Slowable = discord.TextChannel | discord.Thread


def to_info(message: discord.Message) -> MessageInfo:
    return MessageInfo(
        id=message.id,
        author_id=message.author.id,
        author_is_bot=message.author.bot,
        content=message.content,
        has_attachments=bool(message.attachments),
        has_embeds=bool(message.embeds),
        created_at=message.created_at,
        pinned=message.pinned,
    )


class ChannelToolsCog(commands.Cog):
    def __init__(self, bot: GGBot) -> None:
        self.bot = bot

    async def _locale(self, guild: discord.Guild) -> str:
        return await self.bot.services.guilds.get_locale(guild.id)

    # --- Purge ---

    @app_commands.command(name="purge", description=d("cmd.purge.desc"))
    @app_commands.describe(
        amount=d("cmd.arg.amount.desc"),
        author=d("cmd.arg.author.desc"),
        contains=d("cmd.arg.contains.desc"),
        bots=d("cmd.arg.bots.desc"),
        attachments=d("cmd.arg.attachments.desc"),
        links=d("cmd.arg.links.desc"),
        embeds=d("cmd.arg.embeds.desc"),
    )
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.guild_only()
    async def purge(
        self,
        interaction: discord.Interaction,
        amount: app_commands.Range[int, 1, MAX_PURGE],
        author: discord.User | None = None,
        contains: app_commands.Range[str, 1, 100] | None = None,
        bots: bool = False,
        attachments: bool = False,
        links: bool = False,
        embeds: bool = False,
    ) -> None:
        guild = ensure_guild(interaction)
        channel = interaction.channel
        if not isinstance(channel, Purgeable):
            raise UserError("error.not_text_channel")
        ensure_bot_permissions(interaction, manage_messages=True, read_message_history=True)
        if (contains is not None or links) and not interaction.client.intents.message_content:
            # Ohne den Intent sind fremde Nachrichten leer: der Filter würde nie treffen.
            raise UserError("error.purge_needs_content")

        flt = PurgeFilter(
            user_id=author.id if author else None,
            contains=contains,
            bots_only=bots,
            attachments_only=attachments,
            links_only=links,
            embeds_only=embeds,
        )
        await interaction.response.defer(ephemeral=True)
        with self.bot.services.bulk_gate.acquire(guild.id):
            now = datetime.now(UTC)
            history = [
                m
                async for m in channel.history(
                    limit=MAX_SCAN, after=now - BULK_DELETE_MAX_AGE, oldest_first=False
                )
            ]
            selected = select_for_purge([to_info(m) for m in history], flt, amount=amount, now=now)
            deleted = await self._delete(channel, [m.id for m in selected])

        locale = await self._locale(guild)
        key = "mod.purge.done" if deleted else "mod.purge.none"
        await interaction.followup.send(translator.t(locale, key, count=deleted))

    @staticmethod
    async def _delete(channel: Purgeable, ids: list[int]) -> int:
        for index, block in enumerate(chunked(ids, BULK_CHUNK)):
            if index:
                await asyncio.sleep(CHUNK_PAUSE_SECONDS)
            await channel.delete_messages([discord.Object(i) for i in block])
        return len(ids)

    # --- Slowmode ---

    @app_commands.command(name="slowmode", description=d("cmd.slowmode.desc"))
    @app_commands.describe(seconds=d("cmd.arg.seconds.desc"), channel=d("cmd.arg.channel.desc"))
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.guild_only()
    async def slowmode(
        self,
        interaction: discord.Interaction,
        seconds: app_commands.Range[int, 0, MAX_SLOWMODE],
        channel: Slowable | None = None,
    ) -> None:
        guild = ensure_guild(interaction)
        target = channel or interaction.channel
        if not isinstance(target, Slowable):
            raise UserError("error.not_text_channel")
        needed = "manage_threads" if isinstance(target, discord.Thread) else "manage_channels"
        ensure_channel_permissions(target, guild.me, bot=True, **{needed: True})
        if isinstance(interaction.user, discord.Member):
            ensure_channel_permissions(target, interaction.user, bot=False, **{needed: True})

        await target.edit(slowmode_delay=seconds, reason=str(interaction.user.id))
        locale = await self._locale(guild)
        key = "mod.slowmode.set" if seconds else "mod.slowmode.off"
        await interaction.response.send_message(
            translator.t(locale, key, channel=target.mention, seconds=seconds), ephemeral=True
        )

    # --- Lock / Unlock ---

    @app_commands.command(name="lock", description=d("cmd.lock.desc"))
    @app_commands.describe(channel=d("cmd.arg.channel.desc"))
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.guild_only()
    async def lock(
        self, interaction: discord.Interaction, channel: discord.TextChannel | None = None
    ) -> None:
        guild = ensure_guild(interaction)
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            raise UserError("error.not_text_channel")
        ensure_channel_permissions(target, guild.me, bot=True, manage_roles=True)
        if isinstance(interaction.user, discord.Member):
            ensure_channel_permissions(target, interaction.user, bot=False, manage_channels=True)

        overwrite = target.overwrites_for(guild.default_role)
        locks = self.bot.services.channel_locks
        await locks.register_lock(
            guild.id, target.id, previous=overwrite.send_messages, locked_by=interaction.user.id
        )
        overwrite.update(send_messages=False)
        try:
            await target.set_permissions(
                guild.default_role, overwrite=overwrite, reason=str(interaction.user.id)
            )
        except discord.Forbidden as exc:
            await locks.forget(guild.id, target.id)  # sonst hielte `/unlock` eine Sperre für echt
            raise UserError("error.mod_forbidden") from exc
        locale = await self._locale(guild)
        # Öffentlich: Die Sperre soll der Kanal sehen. Über die Interaction, weil der Bot
        # nach dem Sperren selbst nicht mehr in den Kanal schreiben dürfte.
        await interaction.response.send_message(
            translator.t(locale, "mod.lock.locked", channel=target.mention)
        )

    @app_commands.command(name="unlock", description=d("cmd.unlock.desc"))
    @app_commands.describe(channel=d("cmd.arg.channel.desc"))
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.guild_only()
    async def unlock(
        self, interaction: discord.Interaction, channel: discord.TextChannel | None = None
    ) -> None:
        guild = ensure_guild(interaction)
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            raise UserError("error.not_text_channel")
        ensure_channel_permissions(target, guild.me, bot=True, manage_roles=True)
        if isinstance(interaction.user, discord.Member):
            ensure_channel_permissions(target, interaction.user, bot=False, manage_channels=True)

        locks = self.bot.services.channel_locks
        previous = await locks.previous_state(guild.id, target.id)
        overwrite = target.overwrites_for(guild.default_role)
        overwrite.update(send_messages=previous)
        try:
            await target.set_permissions(
                guild.default_role,
                overwrite=None if overwrite.is_empty() else overwrite,
                reason=str(interaction.user.id),
            )
        except discord.Forbidden as exc:
            raise UserError("error.mod_forbidden") from exc
        await locks.forget(guild.id, target.id)
        locale = await self._locale(guild)
        await interaction.response.send_message(
            translator.t(locale, "mod.lock.unlocked", channel=target.mention)
        )


async def setup(bot: GGBot) -> None:
    await bot.add_cog(ChannelToolsCog(bot))
