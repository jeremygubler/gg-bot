"""Einstellungen der Tickets (Modul `tickets`). Panels und Kategorien liegen in eigenen Tabellen."""

from typing import ClassVar

from pydantic import Field, field_validator

from ggbot.core import messages
from ggbot.core.settings.schema import SettingsModel

NAME_FIELDS = ("number", "category", "user")
MAX_NAME_TEMPLATE = 50


class TicketSettings(SettingsModel):
    module: ClassVar[str] = "tickets"

    # Hierhin gehen Transkript und Schliess-Meldung (None = nur DM, falls eingeschaltet).
    log_channel_id: int | None = None
    max_open_per_user: int = Field(default=3, ge=1, le=10)
    # 0 = nie automatisch schliessen.
    auto_close_hours: int = Field(default=0, ge=0, le=720)
    # Wartezeit zwischen "geschlossen" und dem Löschen des Kanals.
    close_delay_seconds: int = Field(default=10, ge=0, le=300)
    feedback: bool = True
    dm_transcript: bool = True
    creators_may_close: bool = True
    ping_support: bool = True
    # Transkripte nach so vielen Tagen löschen; 0 = behalten.
    retention_days: int = Field(default=0, ge=0, le=3650)
    name_template: str = "ticket-{number}"

    @field_validator("name_template")
    @classmethod
    def _name(cls, value: str) -> str:
        if not 1 <= len(value) <= MAX_NAME_TEMPLATE:
            raise ValueError(f"Namensvorlage: 1 bis {MAX_NAME_TEMPLATE} Zeichen")
        messages.validate_template(value, NAME_FIELDS)
        return value
