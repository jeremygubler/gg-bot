"""Server-Beitritt/-Austritt, Guild-Snapshots und Events aus dem Dashboard."""

import asyncio
import contextlib
from datetime import UTC, datetime

import discord
from discord.ext import commands

from ggbot.bot.cache import build_snapshot
from ggbot.bot.client import GGBot
from ggbot.core import events
from ggbot.core.logging import get_logger
from ggbot.core.snapshot import delete_snapshot, save_snapshot

log = get_logger(__name__)

# Mehrere Änderungen in kurzer Folge (z. B. Rollen-Umsortieren) ergeben nur einen Snapshot.
DEBOUNCE_SECONDS = 2.0
RECONNECT_DELAY = 5.0


class LifecycleCog(commands.Cog):
    def __init__(self, bot: GGBot) -> None:
        self.bot = bot
        self._pending: dict[int, asyncio.Task[None]] = {}
        self._listener: asyncio.Task[None] | None = None

    async def cog_load(self) -> None:
        self._listener = asyncio.create_task(self._listen(), name="event-listener")

    async def cog_unload(self) -> None:
        if self._listener is not None:
            self._listener.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._listener
        for task in self._pending.values():
            task.cancel()

    # --- Server kommen und gehen ---

    @commands.Cog.listener()
    async def on_guild_available(self, guild: discord.Guild) -> None:
        await self._register(guild)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self._register(guild)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        await self.bot.services.guilds.mark_left(guild.id, datetime.now(UTC))
        # Ohne Snapshot gilt der Bot im Dashboard als nicht (mehr) auf dem Server.
        await delete_snapshot(self.bot.services.redis, guild.id)

    async def _register(self, guild: discord.Guild) -> None:
        await self.bot.services.guilds.ensure(guild.id)
        await self._snapshot(guild)

    # --- Snapshot aktuell halten ---

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        self._schedule(channel.guild)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        self._schedule(channel.guild)

    @commands.Cog.listener()
    async def on_guild_channel_update(
        self, before: discord.abc.GuildChannel, after: discord.abc.GuildChannel
    ) -> None:
        self._schedule(after.guild)

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role) -> None:
        self._schedule(role.guild)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        self._schedule(role.guild)

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role) -> None:
        self._schedule(after.guild)

    @commands.Cog.listener()
    async def on_guild_emojis_update(
        self, guild: discord.Guild, before: object, after: object
    ) -> None:
        self._schedule(guild)

    def _schedule(self, guild: discord.Guild) -> None:
        if guild.id in self._pending:
            return
        self._pending[guild.id] = asyncio.create_task(self._debounced(guild))

    async def _debounced(self, guild: discord.Guild) -> None:
        try:
            await asyncio.sleep(DEBOUNCE_SECONDS)
        finally:
            self._pending.pop(guild.id, None)
        # Zwischenzeitlich entfernt: sonst legte der späte Snapshot den Server wieder an.
        if self.bot.get_guild(guild.id) is not None:
            await self._snapshot(guild)

    async def _snapshot(self, guild: discord.Guild) -> None:
        await save_snapshot(self.bot.services.redis, build_snapshot(guild))

    # --- Events aus dem Dashboard ---

    async def _listen(self) -> None:
        while True:
            try:
                async for event in events.subscribe(self.bot.services.redis):
                    await self._handle(event)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("event_listener_failed")
            await asyncio.sleep(RECONNECT_DELAY)

    async def _handle(self, event: events.Event) -> None:
        if isinstance(event, events.GuildSync):
            guild = self.bot.get_guild(event.guild_id)
            if guild is not None:
                await self._snapshot(guild)
        elif isinstance(event, events.SettingsChanged):
            # Der Redis-Cache ist schon geleert. Cogs mit eigenem Zustand
            # hängen sich per @Cog.listener() an dieses Event.
            self.bot.dispatch("ggbot_settings_changed", event)
        else:
            self.bot.dispatch("ggbot_panel_publish", event)


async def setup(bot: GGBot) -> None:
    await bot.add_cog(LifecycleCog(bot))
