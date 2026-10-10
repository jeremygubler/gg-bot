"""Einstellungen der Geburtstage (Modul `birthday`)."""

from typing import ClassVar, Self

from pydantic import Field, model_validator

from ggbot.core import messages
from ggbot.core.settings.schema import SettingsModel
from ggbot.core.settings.types import ChannelId, RoleId

FIELDS = ("user", "name", "server")


class BirthdaySettings(SettingsModel):
    module: ClassVar[str] = "birthday"

    enabled: bool = False
    channel_id: ChannelId | None = None
    role_id: RoleId | None = None  # Rolle nur für den Tag
    message: str | None = Field(default=None, json_schema_extra={"x-widget": "textarea"})

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.enabled and self.channel_id is None:
            raise ValueError("Geburtstage brauchen einen Kanal")
        if self.message is not None:
            messages.validate_template(self.message, FIELDS)
        return self
