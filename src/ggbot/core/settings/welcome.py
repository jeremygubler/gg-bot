"""Einstellungen von Willkommen, Abschied und Autorollen (Modul `welcome`)."""

from typing import ClassVar, Self

from pydantic import Field, field_validator, model_validator

from ggbot.core import messages
from ggbot.core.settings.card import CardStyle
from ggbot.core.settings.schema import SettingsModel

MAX_AUTOROLES = 10
JOIN_FIELDS = ("user", "name", "server", "count")
# Wer gegangen ist, wird nicht erwähnt: eine Erwähnung eines Ausgetretenen zeigt nur eine ID.
LEAVE_FIELDS = ("name", "server", "count")
DM_FIELDS = ("name", "server")


class WelcomeSettings(SettingsModel):
    module: ClassVar[str] = "welcome"

    join_enabled: bool = False
    join_channel_id: int | None = None
    join_message: str | None = None
    # Bild mit Avatar und Namen unter die Nachricht hängen.
    join_card: bool = True

    leave_enabled: bool = False
    leave_channel_id: int | None = None
    leave_message: str | None = None

    dm_enabled: bool = False
    dm_message: str | None = None

    # Rollen für neue Mitglieder, erst nach dem Mitglieder-Screening vergeben.
    autoroles: list[int] = Field(default_factory=list, max_length=MAX_AUTOROLES)
    bot_autoroles: list[int] = Field(default_factory=list, max_length=MAX_AUTOROLES)

    card: CardStyle = Field(default_factory=CardStyle)

    @field_validator("join_message")
    @classmethod
    def _join(cls, value: str | None) -> str | None:
        return _checked(value, JOIN_FIELDS)

    @field_validator("leave_message")
    @classmethod
    def _leave(cls, value: str | None) -> str | None:
        return _checked(value, LEAVE_FIELDS)

    @field_validator("dm_message")
    @classmethod
    def _dm(cls, value: str | None) -> str | None:
        return _checked(value, DM_FIELDS)

    @field_validator("autoroles", "bot_autoroles")
    @classmethod
    def _roles(cls, roles: list[int]) -> list[int]:
        if any(role_id <= 0 for role_id in roles):
            raise ValueError("ungültige Rollen-ID")
        return list(dict.fromkeys(roles))  # Reihenfolge behalten, Doppelte entfernen

    @model_validator(mode="after")
    def _channels(self) -> Self:
        if self.join_enabled and self.join_channel_id is None:
            raise ValueError("Willkommen braucht einen Kanal")
        if self.leave_enabled and self.leave_channel_id is None:
            raise ValueError("Abschied braucht einen Kanal")
        return self


def _checked(value: str | None, allowed: tuple[str, ...]) -> str | None:
    if value is not None:
        messages.validate_template(value, allowed)
    return value
