"""Typisierte Server-Einstellungen. Ein Modell pro Modul."""

from typing import ClassVar, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ggbot.core.duration import MAX_TEMPBAN, MAX_TIMEOUT, MIN_TEMPBAN


class SettingsModel(BaseModel):
    """Basis aller Modul-Einstellungen.

    Neue Felder brauchen einen Default, dann bleiben gespeicherte Stände gültig.
    Unbekannte Felder aus älteren/neueren Ständen werden ignoriert.
    """

    model_config = ConfigDict(extra="ignore", validate_assignment=True)

    module: ClassVar[str]
    schema_version: ClassVar[int] = 1


class GeneralSettings(SettingsModel):
    module: ClassVar[str] = "general"

    # Akzentfarbe der Embeds des Bots (RGB als Zahl).
    embed_color: int = Field(default=0x5865F2, ge=0, le=0xFFFFFF)


EscalationAction = Literal["timeout", "kick", "ban", "tempban"]


class EscalationRule(BaseModel):
    """ "Bei genau `warns` aktiven Verwarnungen führt der Bot `action` aus"."""

    model_config = ConfigDict(extra="ignore")

    warns: int = Field(ge=1, le=100)
    action: EscalationAction
    duration_seconds: int | None = None

    @model_validator(mode="after")
    def _check_duration(self) -> Self:
        if self.action == "timeout":
            if self.duration_seconds is None or not 1 <= self.duration_seconds <= MAX_TIMEOUT:
                raise ValueError("timeout braucht eine Dauer zwischen 1 s und 28 Tagen")
        elif self.action == "tempban":
            if (
                self.duration_seconds is None
                or not MIN_TEMPBAN <= self.duration_seconds <= MAX_TEMPBAN
            ):
                raise ValueError("tempban braucht eine Dauer zwischen 1 Minute und 365 Tagen")
        elif self.duration_seconds is not None:
            raise ValueError(f"{self.action} hat keine Dauer")
        return self


class ModerationSettings(SettingsModel):
    module: ClassVar[str] = "moderation"

    # Das Ziel einer Aktion per DM informieren (schlägt die DM fehl, läuft die Aktion trotzdem).
    dm_on_action: bool = True
    # Verwarnungen zählen nur so lange für die Eskalation; None = für immer.
    warn_expiry_days: int | None = Field(default=None, ge=1, le=3650)
    escalation: list[EscalationRule] = Field(default_factory=list, max_length=20)

    @field_validator("escalation")
    @classmethod
    def _unique_sorted(cls, rules: list[EscalationRule]) -> list[EscalationRule]:
        thresholds = [rule.warns for rule in rules]
        if len(set(thresholds)) != len(thresholds):
            raise ValueError("jede Verwarnungsanzahl darf nur eine Regel haben")
        return sorted(rules, key=lambda rule: rule.warns)
