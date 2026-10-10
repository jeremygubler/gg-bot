"""Einstellungen der Geburtstage (Modul `birthday`)."""

from typing import ClassVar, Self

from pydantic import model_validator

from ggbot.core import messages
from ggbot.core.settings.schema import SettingsModel

FIELDS = ("user", "name", "server")


class BirthdaySettings(SettingsModel):
    module: ClassVar[str] = "birthday"

    enabled: bool = False
    channel_id: int | None = None
    role_id: int | None = None  # Rolle nur für den Tag
    message: str | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.enabled and self.channel_id is None:
            raise ValueError("Geburtstage brauchen einen Kanal")
        if self.message is not None:
            messages.validate_template(self.message, FIELDS)
        return self
