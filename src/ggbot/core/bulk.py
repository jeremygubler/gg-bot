"""Hilfen für Massenaktionen (Purge, später Rollenvergabe an viele).

discord.py hält die HTTP-Limits selbst ein. Das Gate sorgt zusätzlich dafür, dass pro Server nur
eine Massenaktion zugleich läuft, damit eine nicht alle anderen im selben Server aushungert.
"""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager

from ggbot.core.errors import UserError


def chunked[T](items: Sequence[T], size: int) -> Iterator[Sequence[T]]:
    if size < 1:
        raise ValueError("size muss >= 1 sein")
    for start in range(0, len(items), size):
        yield items[start : start + size]


class BulkGate:
    """Ein Server gehört genau einem Shard-Prozess, deshalb genügt Zustand im Prozess."""

    def __init__(self) -> None:
        self._busy: set[int] = set()

    @contextmanager
    def acquire(self, guild_id: int) -> Iterator[None]:
        if guild_id in self._busy:
            raise UserError("error.bulk_busy")
        self._busy.add(guild_id)
        try:
            yield
        finally:
            self._busy.discard(guild_id)
