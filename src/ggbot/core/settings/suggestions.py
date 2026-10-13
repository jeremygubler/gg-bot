"""Einstellungen der Vorschläge (Modul `suggestions`)."""

from typing import ClassVar, Self

from pydantic import model_validator

from ggbot.core.settings.schema import SettingsModel
from ggbot.core.settings.types import ChannelId


class SuggestionSettings(SettingsModel):
    module: ClassVar[str] = "suggestions"

    enabled: bool = False
    channel_id: ChannelId | None = None
    # Wer seinen eigenen Vorschlag hochstimmen darf. Aus: sonst zählt die erste Stimme immer.
    self_vote: bool = False
    # Der Autor bekommt eine DM, sobald das Team entschieden hat (sofern er DMs erlaubt).
    dm_author: bool = True

    @model_validator(mode="after")
    def _needs_channel(self) -> Self:
        if self.enabled and self.channel_id is None:
            raise ValueError("die Vorschläge brauchen einen Kanal")
        return self
