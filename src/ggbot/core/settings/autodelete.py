"""Einstellungen des Auto-Löschens (Modul `autodelete`)."""

from typing import ClassVar, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ggbot.core.settings.schema import SettingsModel
from ggbot.core.settings.types import ChannelId

MAX_RULES = 20
# Zehn Minuten: darunter löscht der Bot Nachrichten, die gerade erst gelesen werden.
MIN_AGE_MINUTES = 10
# Neunzig Tage. Länger zurück löscht der Bot Nachricht für Nachricht (Discord erlaubt Sammellöschen
# nur bis 14 Tage), das dauert entsprechend.
MAX_AGE_MINUTES = 90 * 24 * 60


class AutodeleteRule(BaseModel):
    """Nachrichten in diesem Kanal werden nach `max_age_minutes` gelöscht."""

    model_config = ConfigDict(extra="ignore")

    channel_id: ChannelId
    max_age_minutes: int = Field(ge=MIN_AGE_MINUTES, le=MAX_AGE_MINUTES)
    # Angeheftete Nachrichten sind meist Regeln oder Hinweise: stehen lassen.
    keep_pinned: bool = True


class AutodeleteSettings(SettingsModel):
    module: ClassVar[str] = "autodelete"

    # Aus, bis jemand es ausdrücklich einschaltet: Löschen lässt sich nicht rückgängig machen.
    enabled: bool = False
    rules: list[AutodeleteRule] = Field(default_factory=list, max_length=MAX_RULES)

    @model_validator(mode="after")
    def _one_rule_per_channel(self) -> Self:
        channels = [rule.channel_id for rule in self.rules]
        if len(channels) != len(set(channels)):
            raise ValueError("ein Kanal darf nur eine Regel haben")
        return self
