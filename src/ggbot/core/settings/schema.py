"""Typisierte Server-Einstellungen. Ein Modell pro Modul."""

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field


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
