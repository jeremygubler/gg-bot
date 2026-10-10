"""Aussehen der Karten (Rank-Card, Willkommens-Card). Gemeinsam, damit beide gleich aussehen."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CARD_TEMPLATES = ("solid", "gradient", "stripes", "dots")


class CardStyle(BaseModel):
    """Farben als RGB-Zahl; die Vorlagen werden im Code gezeichnet, es gibt keine Uploads."""

    model_config = ConfigDict(extra="ignore")

    accent: int = Field(
        default=0x5865F2, ge=0, le=0xFFFFFF, json_schema_extra={"x-widget": "color"}
    )
    background: int = Field(
        default=0x23272A, ge=0, le=0xFFFFFF, json_schema_extra={"x-widget": "color"}
    )
    template: Literal["solid", "gradient", "stripes", "dots"] = "gradient"
