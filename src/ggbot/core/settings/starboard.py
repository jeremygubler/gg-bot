"""Einstellungen des Starboards (Modul `starboard`)."""

from typing import ClassVar, Self

from pydantic import Field, field_validator, model_validator

from ggbot.core.roles.plan import is_emoji
from ggbot.core.settings.schema import SettingsModel
from ggbot.core.settings.types import ChannelId

MAX_IGNORED = 50


class StarboardSettings(SettingsModel):
    module: ClassVar[str] = "starboard"

    enabled: bool = False
    channel_id: ChannelId | None = None
    emoji: str = "⭐"
    threshold: int = Field(default=3, ge=1, le=100)
    self_star: bool = False
    ignore_nsfw: bool = True
    ignored_channels: list[ChannelId] = Field(default_factory=list, max_length=MAX_IGNORED)

    @field_validator("emoji")
    @classmethod
    def _emoji(cls, value: str) -> str:
        value = value.strip()
        if not is_emoji(value):
            raise ValueError("kein gültiges Emoji")
        return value

    @model_validator(mode="after")
    def _channel(self) -> Self:
        if self.enabled and self.channel_id is None:
            raise ValueError("das Starboard braucht einen Kanal")
        return self
