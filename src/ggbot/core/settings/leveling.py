"""Einstellungen des Levelings (Modul `leveling`)."""

from typing import ClassVar, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ggbot.core.leveling.announce import validate_template
from ggbot.core.leveling.xp import MAX_LEVEL
from ggbot.core.settings.card import CardStyle
from ggbot.core.settings.schema import SettingsModel
from ggbot.core.settings.types import ChannelId, RoleId

MAX_LIST = 100
MAX_REWARDS = 50
MAX_BOOSTS = 50
AnnounceMode = Literal["off", "current", "channel", "dm"]


class VoiceXp(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    xp_min: int = Field(default=10, ge=1, le=1000)
    xp_max: int = Field(default=20, ge=1, le=1000)
    # Nur XP, wenn mindestens ein weiterer Mensch im Kanal ist.
    requires_company: bool = True

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.xp_min > self.xp_max:
            raise ValueError("xp_min darf nicht grösser als xp_max sein")
        return self


class LevelingSettings(SettingsModel):
    module: ClassVar[str] = "leveling"

    enabled: bool = False
    xp_min: int = Field(default=15, ge=1, le=1000)
    xp_max: int = Field(default=25, ge=1, le=1000)
    cooldown_seconds: int = Field(default=60, ge=0, le=3600)
    # Globaler Faktor auf alle XP (Text und Voice).
    multiplier: float = Field(default=1.0, ge=0.1, le=10.0)
    voice: VoiceXp = Field(default_factory=VoiceXp)

    announce: AnnounceMode = "current"
    announce_channel_id: ChannelId | None = None
    announce_message: str | None = Field(default=None, json_schema_extra={"x-widget": "textarea"})

    # Level → Rolle.
    rewards: dict[int, RoleId] = Field(
        default_factory=dict,
        max_length=MAX_REWARDS,
        json_schema_extra={"x-key-type": "integer", "x-key-label": "level"},
    )
    reward_mode: Literal["stack", "replace"] = "stack"

    role_multipliers: dict[int, float] = Field(
        json_schema_extra={"x-key-picker": "role"}, default_factory=dict, max_length=MAX_BOOSTS
    )
    channel_multipliers: dict[int, float] = Field(
        json_schema_extra={"x-key-picker": "channel"}, default_factory=dict, max_length=MAX_BOOSTS
    )
    no_xp_channels: list[ChannelId] = Field(default_factory=list, max_length=MAX_LIST)
    no_xp_roles: list[RoleId] = Field(default_factory=list, max_length=MAX_LIST)

    card: CardStyle = Field(default_factory=CardStyle)

    # Öffentliche Rangliste im Dashboard (ohne Anmeldung lesbar). Aus, solange der Server sie nicht
    # ausdrücklich freigibt; angezeigt werden dann Anzeigename und Avatar der Top 100.
    public_leaderboard: bool = False

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.xp_min > self.xp_max:
            raise ValueError("xp_min darf nicht grösser als xp_max sein")
        if self.announce == "channel" and self.announce_channel_id is None:
            raise ValueError("announce=channel braucht einen Kanal")
        return self

    @field_validator("announce_message")
    @classmethod
    def _template(cls, value: str | None) -> str | None:
        if value is not None:
            validate_template(value)
        return value

    @field_validator("rewards")
    @classmethod
    def _rewards(cls, rewards: dict[int, RoleId]) -> dict[int, RoleId]:
        if any(not 1 <= level <= MAX_LEVEL for level in rewards):
            raise ValueError(f"Belohnungs-Level müssen zwischen 1 und {MAX_LEVEL} liegen")
        if any(role_id <= 0 for role_id in rewards.values()):
            raise ValueError("ungültige Rollen-ID")
        return dict(sorted(rewards.items()))

    @field_validator("role_multipliers", "channel_multipliers")
    @classmethod
    def _boosts(cls, boosts: dict[int, float]) -> dict[int, float]:
        if any(not 0.1 <= factor <= 10.0 for factor in boosts.values()):
            raise ValueError("Faktoren müssen zwischen 0,1 und 10 liegen")
        return boosts
