"""Filterlogik für /purge, ohne Discord-Typen."""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

# Bulk-Delete akzeptiert nur Nachrichten, die jünger als 14 Tage sind. Eine Minute Reserve,
# weil zwischen Auswahl und Löschen Zeit vergeht.
BULK_DELETE_MAX_AGE = timedelta(days=14) - timedelta(minutes=1)

_LINK = re.compile(r"https?://\S+", re.IGNORECASE)


@dataclass(frozen=True)
class MessageInfo:
    id: int
    author_id: int
    author_is_bot: bool
    content: str
    has_attachments: bool
    has_embeds: bool
    created_at: datetime
    pinned: bool = False


@dataclass(frozen=True)
class PurgeFilter:
    """Alle gesetzten Bedingungen müssen zugleich zutreffen."""

    user_id: int | None = None
    contains: str | None = None
    bots_only: bool = False
    attachments_only: bool = False
    links_only: bool = False
    embeds_only: bool = False

    def matches(self, message: MessageInfo) -> bool:
        if message.pinned:  # Angeheftetes ist absichtlich da
            return False
        if self.user_id is not None and message.author_id != self.user_id:
            return False
        if self.contains is not None and self.contains.lower() not in message.content.lower():
            return False
        if self.bots_only and not message.author_is_bot:
            return False
        if self.attachments_only and not message.has_attachments:
            return False
        if self.links_only and not _LINK.search(message.content):
            return False
        return not (self.embeds_only and not message.has_embeds)


def select_for_purge(
    messages: list[MessageInfo], flt: PurgeFilter, *, amount: int, now: datetime
) -> list[MessageInfo]:
    """Die `amount` jüngsten passenden Nachrichten. `messages` ist neu → alt sortiert.

    Zu alte Nachrichten werden übersprungen, nicht gelöscht: der Einzel-Löschweg würde für
    jede einen eigenen API-Aufruf brauchen.
    """
    cutoff = now - BULK_DELETE_MAX_AGE
    selected: list[MessageInfo] = []
    for message in messages:
        if message.created_at < cutoff:
            break
        if flt.matches(message):
            selected.append(message)
            if len(selected) == amount:
                break
    return selected
