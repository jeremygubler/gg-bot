import asyncio
import contextlib
import hashlib
import json
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from ggbot.bot.errors import respond_ephemeral, to_message
from ggbot.bot.i18n import CommandTranslator
from ggbot.bot.services import Services
from ggbot.core.logging import get_logger
from ggbot.core.redis import get_str, key

log = get_logger(__name__)

COGS = (
    "ggbot.bot.cogs.core",
    "ggbot.bot.cogs.lifecycle",
    "ggbot.bot.cogs.moderation",
    "ggbot.bot.cogs.channel_tools",
    "ggbot.bot.cogs.modconfig",
    "ggbot.bot.cogs.automod",
    "ggbot.bot.cogs.automod_config",
    "ggbot.bot.cogs.eventlog",
    "ggbot.bot.cogs.eventlog_config",
    "ggbot.bot.cogs.leveling",
    "ggbot.bot.cogs.leveling_config",
    "ggbot.bot.cogs.rolepanels",
    "ggbot.bot.cogs.welcome",
    "ggbot.bot.cogs.tickets",
    "ggbot.bot.cogs.ticketpanel",
    "ggbot.bot.cogs.notify",
    "ggbot.bot.cogs.customcmd",
    "ggbot.bot.cogs.giveaway",
    "ggbot.bot.cogs.reminder",
    "ggbot.bot.cogs.poll",
    "ggbot.bot.cogs.starboard",
    "ggbot.bot.cogs.birthday",
    "ggbot.bot.cogs.statchannels",
    "ggbot.bot.cogs.schedule",
)
HEARTBEAT_KEY = key("heartbeat", "bot")
HEARTBEAT_INTERVAL = 10
HEARTBEAT_TTL = 30


class GGTree(app_commands.CommandTree["GGBot"]):
    async def on_error(
        self, interaction: discord.Interaction["GGBot"], error: app_commands.AppCommandError
    ) -> None:
        bot = interaction.client
        try:
            locale = await bot.services.guilds.get_locale(interaction.guild_id)
        except Exception:
            # Die Fehlermeldung darf nicht an der Sprachabfrage scheitern.
            log.exception("locale_lookup_failed")
            locale = bot.services.config.default_locale
        text, _ = to_message(error, locale)
        await respond_ephemeral(interaction, text)


class GGBot(commands.AutoShardedBot):
    def __init__(self, services: Services) -> None:
        config = services.config
        # Nur nicht-privilegierte Intents. Members/Message Content kommen mit den
        # Phasen, die sie brauchen (siehe README).
        intents = discord.Intents.none()
        intents.guilds = True
        intents.emojis_and_stickers = True
        # Purge liest den Verlauf per REST; ohne diesen Intent sind fremde Inhalte leer.
        intents.message_content = config.enable_message_content_intent
        # Nachrichten-Ereignisse braucht das Leveling (XP pro Nachricht); der Inhalt selbst bleibt
        # ohne den privilegierten Intent leer, Automod und Log-Arten dafür sind dann gesperrt.
        intents.guild_messages = True
        intents.members = config.enable_members_intent
        # Nicht privilegiert; für das Event-Log (Sprache, Einladungen, Bans).
        intents.voice_states = True
        intents.invites = True
        intents.moderation = True
        # Reaktions-Rollen. Nicht privilegiert.
        intents.guild_reactions = True
        # discord.py typisiert shard_ids als list[int], erwartet aber "weglassen" für "auto".
        shard_options: dict[str, Any] = {"shard_ids": config.shard_ids} if config.shard_ids else {}
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            tree_cls=GGTree,
            shard_count=config.shard_count,
            max_messages=config.message_cache_size,
            **shard_options,
            help_command=None,
            # Sicherer Standard für jede Nachricht: nie @everyone/@here oder Rollen. Wer mehr
            # braucht, erlaubt es beim Senden ausdrücklich.
            allowed_mentions=discord.AllowedMentions(
                everyone=False, roles=False, users=True, replied_user=False
            ),
        )
        self.services = services
        self._background: list[asyncio.Task[None]] = []

    async def setup_hook(self) -> None:
        await self.tree.set_translator(CommandTranslator())
        for cog in COGS:
            await self.load_extension(cog)
        await self._sync_commands()
        self.services.scheduler.start()
        self._background.append(asyncio.create_task(self._heartbeat(), name="heartbeat"))

    async def close(self) -> None:
        for task in self._background:
            task.cancel()
        for task in self._background:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self.services.scheduler.stop()
        await self.services.http.aclose()
        await super().close()

    async def _sync_commands(self) -> None:
        """Synchronisiert nur, wenn sich die Befehle (inkl. Übersetzungen) geändert haben."""
        guild = (
            discord.Object(self.services.config.dev_guild_id)
            if self.services.config.dev_guild_id
            else None
        )
        if guild is not None:
            self.tree.copy_global_to(guild=guild)
        translator = self.tree.translator
        assert translator is not None
        payload = [
            await command.get_translated_payload(self.tree, translator)
            for command in self.tree.get_commands(guild=guild)
        ]
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        redis_key = key("commands", "hash", guild.id if guild else "global")
        if await get_str(self.services.redis, redis_key) == digest:
            log.info("commands_unchanged")
            return
        await self.tree.sync(guild=guild)
        await self.services.redis.set(redis_key, digest)
        log.info("commands_synced", count=len(payload), dev_guild=guild.id if guild else None)

    async def _heartbeat(self) -> None:
        """Lebenszeichen für den Docker-Healthcheck."""
        while True:
            if self.is_ready():
                await self.services.redis.set(HEARTBEAT_KEY, "1", ex=HEARTBEAT_TTL)
            await asyncio.sleep(HEARTBEAT_INTERVAL)
