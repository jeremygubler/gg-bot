"""Hält die öffentliche Rangliste im Dashboard aktuell (Namen und Avatare der Top 100)."""

import asyncio

import discord
from discord.ext import commands

from ggbot.bot.client import GGBot
from ggbot.core import events
from ggbot.core.logging import get_logger
from ggbot.core.publicboard import TOP, BoardRow, PublicBoard, delete_board, save_board

log = get_logger(__name__)

REFRESH_SECONDS = 600
# Mitglieder, die nicht im Cache sind, werden einzeln nachgefragt; mehr als das pro Lauf wäre
# eine Lawine von Anfragen. Der Rest bekommt beim nächsten Lauf seine Chance.
MAX_FETCH_PER_RUN = 25


class PublicBoardCog(commands.Cog):
    def __init__(self, bot: GGBot) -> None:
        self.bot = bot
        self._task: asyncio.Task[None] | None = None

    async def cog_load(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="public-board")

    async def cog_unload(self) -> None:
        if self._task is not None:
            self._task.cancel()

    @commands.Cog.listener()
    async def on_ggbot_settings_changed(self, event: events.SettingsChanged) -> None:
        if event.module != "leveling":
            return
        guild = self.bot.get_guild(event.guild_id)
        if guild is not None:
            await self._guarded(guild)

    async def _loop(self) -> None:
        await self.bot.wait_until_ready()
        while True:
            for guild in list(self.bot.guilds):
                await self._guarded(guild)
            await asyncio.sleep(REFRESH_SECONDS)

    async def _guarded(self, guild: discord.Guild) -> None:
        try:
            await self.refresh(guild)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Ein Server darf die Aktualisierung der übrigen nicht aufhalten.
            log.exception("public_board_failed", guild_id=guild.id)

    async def refresh(self, guild: discord.Guild) -> None:
        services = self.bot.services
        settings = await services.leveling.settings(guild.id)
        if not (settings.enabled and settings.public_leaderboard):
            # Ausschalten wirkt sofort: ohne Zwischenspeicher gibt die API nichts mehr her.
            await delete_board(services.redis, guild.id)
            return
        rows: list[BoardRow] = []
        fetched = 0
        for entry in await services.leveling.top(guild.id, TOP):
            member = guild.get_member(entry.user_id)
            if member is None and fetched < MAX_FETCH_PER_RUN:
                fetched += 1
                try:
                    member = await guild.fetch_member(entry.user_id)
                except discord.NotFound:
                    continue  # nicht mehr auf dem Server: gehört nicht in eine öffentliche Liste
                except discord.HTTPException:
                    member = None
            rows.append(
                BoardRow(
                    position=entry.position,
                    name=member.display_name if member is not None else None,
                    avatar_url=(
                        member.display_avatar.replace(size=64, static_format="png").url
                        if member is not None
                        else None
                    ),
                    level=entry.level,
                    xp=entry.xp,
                )
            )
        await save_board(services.redis, guild.id, PublicBoard(guild_name=guild.name, rows=rows))


async def setup(bot: GGBot) -> None:
    await bot.add_cog(PublicBoardCog(bot))
