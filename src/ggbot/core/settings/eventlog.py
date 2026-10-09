"""Einstellungen des Event-Logs (Modul `logging`): pro Ereignisart ein eigener Kanal."""

from typing import ClassVar

from pydantic import Field, field_validator

from ggbot.core.settings.schema import SettingsModel

# Reihenfolge = Reihenfolge in /logging status.
EVENT_TYPES = (
    "message_delete",
    "message_edit",
    "message_bulk",
    "member_join",
    "member_leave",
    "member_update",
    "member_ban",
    "member_unban",
    "role_create",
    "role_delete",
    "role_update",
    "channel_create",
    "channel_delete",
    "channel_update",
    "voice",
    "invite_create",
    "invite_delete",
)

# Welche Ereignisarten welchen privilegierten Intent brauchen. Ohne ihn bekäme der Bot die Daten
# nicht (Nachrichteninhalte, Mitgliederänderungen), das Log wäre leer oder irreführend.
NEEDS_MESSAGE_CONTENT = frozenset({"message_delete", "message_edit", "message_bulk"})
NEEDS_MEMBERS = frozenset({"member_join", "member_leave", "member_update"})

MAX_IGNORED = 100


class LoggingSettings(SettingsModel):
    module: ClassVar[str] = "logging"

    # Ereignisart -> Kanal. Eine Art ohne Eintrag ist aus.
    channels: dict[str, int] = Field(default_factory=dict)
    # Aus diesen Kanälen werden keine Nachrichten-Ereignisse protokolliert.
    ignored_channels: list[int] = Field(default_factory=list, max_length=MAX_IGNORED)
    # Nachrichten und Sprachwechsel von Bots weglassen (sonst füllen Musik- und Spielbots das Log).
    ignore_bots: bool = True

    @field_validator("channels")
    @classmethod
    def _known_types(cls, channels: dict[str, int]) -> dict[str, int]:
        unknown = set(channels) - set(EVENT_TYPES)
        if unknown:
            raise ValueError(f"unbekannte Ereignisart: {sorted(unknown)[0]}")
        if any(channel_id <= 0 for channel_id in channels.values()):
            raise ValueError("ungültige Kanal-ID")
        return channels

    def enabled(self) -> set[str]:
        return set(self.channels)
