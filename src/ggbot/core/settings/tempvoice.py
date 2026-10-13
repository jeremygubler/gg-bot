"""Einstellungen der temporären Sprachkanäle (Modul `tempvoice`)."""

from typing import ClassVar, Self

from pydantic import Field, model_validator

from ggbot.core.messages import MAX_TEMPLATE, validate_template
from ggbot.core.settings.schema import SettingsModel
from ggbot.core.settings.types import ChannelId

FIELDS = ("user", "number")
MAX_CHANNELS = 50


class TempVoiceSettings(SettingsModel):
    module: ClassVar[str] = "tempvoice"

    enabled: bool = False
    # Wer diesen Sprachkanal betritt, bekommt einen eigenen und wird hineinverschoben.
    lobby_channel_id: ChannelId | None = None
    # Kategorie für die neuen Kanäle; leer = die der Lobby.
    category_id: ChannelId | None = None
    # `{user}` = Anzeigename des Besitzers, `{number}` = laufende Nummer.
    name_template: str = Field(default="{user}", min_length=1, max_length=MAX_TEMPLATE)
    user_limit: int = Field(default=0, ge=0, le=99)  # 0 = unbegrenzt
    # Obergrenze gleichzeitiger Kanäle: Discord begrenzt auch das Anlegen, und ein Fehlgriff oder
    # Missbrauch soll den Server nicht mit Kanälen füllen.
    max_channels: int = Field(default=25, ge=1, le=MAX_CHANNELS)

    @model_validator(mode="after")
    def _check(self) -> Self:
        try:
            validate_template(self.name_template, FIELDS)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        if self.enabled and self.lobby_channel_id is None:
            raise ValueError("die temporären Sprachkanäle brauchen einen Lobby-Kanal")
        return self
