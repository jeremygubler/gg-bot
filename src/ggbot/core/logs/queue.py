"""Begrenzte Warteschlange pro Kanal: ein Ansturm (Raid, Massenlöschung) flutet den Bot nicht."""

from collections import deque


class ChannelBacklog[T]:
    """Hält höchstens `limit` Einträge. Was darüber hinausgeht, wird gezählt statt gepuffert:
    discord.py müsste sonst Hunderte Nachrichten hinter dem Ratenlimit festhalten."""

    def __init__(self, limit: int = 100) -> None:
        if limit < 1:
            raise ValueError("limit muss >= 1 sein")
        self._limit = limit
        self._items: deque[T] = deque()
        self._dropped = 0

    def push(self, item: T) -> bool:
        """False, wenn der Eintrag verworfen wurde."""
        if len(self._items) >= self._limit:
            self._dropped += 1
            return False
        self._items.append(item)
        return True

    def pop(self) -> T | None:
        return self._items.popleft() if self._items else None

    def take_dropped(self) -> int:
        """Anzahl verworfener Einträge seit dem letzten Aufruf (und zurücksetzen)."""
        dropped, self._dropped = self._dropped, 0
        return dropped

    def __len__(self) -> int:
        return len(self._items)
