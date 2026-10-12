"""Einstellungen der Verifizierung (Modul `verification`)."""

from typing import ClassVar, Literal, Self

from pydantic import Field, model_validator

from ggbot.core.settings.schema import SettingsModel
from ggbot.core.settings.types import ChannelId, RoleId, Snowflake

MAX_TITLE = 100
MAX_MESSAGE = 1000

VerifyMode = Literal["button", "code"]


class VerificationSettings(SettingsModel):
    module: ClassVar[str] = "verification"

    enabled: bool = False
    # Wer den Knopf drückt (und bei "code" die Aufgabe löst), bekommt diese Rolle.
    role_id: RoleId | None = None
    # button: ein Klick genügt. code: ein Bild mit Zeichen muss abgetippt werden (hält einfache
    # Skripte ab; ein Mensch löst es in Sekunden).
    mode: VerifyMode = "button"
    channel_id: ChannelId | None = None
    title: str | None = Field(default=None, max_length=MAX_TITLE)
    message: str | None = Field(
        default=None, max_length=MAX_MESSAGE, json_schema_extra={"x-widget": "textarea"}
    )
    # Wo die Panel-Nachricht steht. Wird vom Bot gesetzt (`/verification setup`), nicht im Formular
    # bearbeitet: Auto-Löschen lässt sie damit in Ruhe.
    panel_message_id: Snowflake | None = Field(default=None, json_schema_extra={"x-hidden": True})

    @model_validator(mode="after")
    def _needs_role(self) -> Self:
        if self.enabled and self.role_id is None:
            raise ValueError("die Verifizierung braucht eine Rolle")
        return self
