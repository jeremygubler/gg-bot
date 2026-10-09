"""Schickt Log-Einträge in Kanäle: in Reihenfolge, mit Obergrenze pro Kanal."""

import asyncio
import io

import discord

from ggbot.core.i18n import translator
from ggbot.core.logging import get_logger
from ggbot.core.logs.entries import LogEntry
from ggbot.core.logs.queue import ChannelBacklog

log = get_logger(__name__)

BACKLOG_LIMIT = 100


def to_embed(entry: LogEntry) -> discord.Embed:
    embed = discord.Embed(
        title=entry.title,
        description=entry.description or None,
        color=entry.color,
        timestamp=entry.at,
    )
    for field in entry.fields:
        embed.add_field(name=field.name, value=field.value, inline=field.inline)
    return embed


class LogSender:
    """Ein Worker pro Kanal. Bei einem Ansturm (Raid, Massenlöschung) werden Einträge über der
    Obergrenze verworfen und am Ende in einer Zeile gemeldet, statt Hunderte Nachrichten hinter
    dem Ratenlimit festzuhalten."""

    def __init__(self, *, limit: int = BACKLOG_LIMIT) -> None:
        self._limit = limit
        self._backlogs: dict[int, ChannelBacklog[tuple[LogEntry, str]]] = {}
        self._workers: dict[int, asyncio.Task[None]] = {}

    def enqueue(self, channel: discord.TextChannel, entry: LogEntry, locale: str) -> None:
        backlog = self._backlogs.setdefault(channel.id, ChannelBacklog(self._limit))
        backlog.push((entry, locale))
        worker = self._workers.get(channel.id)
        if worker is None or worker.done():
            self._workers[channel.id] = asyncio.create_task(
                self._drain(channel, backlog), name=f"eventlog-{channel.id}"
            )

    async def _drain(
        self, channel: discord.TextChannel, backlog: ChannelBacklog[tuple[LogEntry, str]]
    ) -> None:
        locale = "en"
        while (item := backlog.pop()) is not None:
            entry, locale = item
            try:
                await self._send(channel, entry)
            except discord.Forbidden:
                # Ohne Schreibrecht wäre auch der Rest vergeblich.
                log.warning("eventlog_not_allowed", channel_id=channel.id, log_event=entry.event)
                while backlog.pop() is not None:
                    pass
                backlog.take_dropped()
                return
            except discord.HTTPException:
                log.exception("eventlog_send_failed", channel_id=channel.id, log_event=entry.event)
        dropped = backlog.take_dropped()
        if dropped:
            text = translator.t(locale, "logging.dropped", count=dropped)
            try:
                await channel.send(text)
            except discord.HTTPException:
                log.exception("eventlog_summary_failed", channel_id=channel.id)

    @staticmethod
    async def _send(channel: discord.TextChannel, entry: LogEntry) -> None:
        embed = to_embed(entry)
        if entry.file is None:
            await channel.send(embed=embed)
            return
        attachment = discord.File(io.BytesIO(entry.file.content), filename=entry.file.filename)
        await channel.send(embed=embed, file=attachment)

    async def aclose(self) -> None:
        """Beim Beenden den Rest noch senden lassen, aber nicht ewig warten."""
        workers = [task for task in self._workers.values() if not task.done()]
        if workers:
            await asyncio.wait(workers, timeout=5)
        for task in workers:
            task.cancel()
