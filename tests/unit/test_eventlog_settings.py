import pytest
from pydantic import ValidationError

from ggbot.core.settings.eventlog import (
    EVENT_TYPES,
    NEEDS_MEMBERS,
    NEEDS_MESSAGE_CONTENT,
    LoggingSettings,
)


def test_standard_ist_alles_aus() -> None:
    settings = LoggingSettings()
    assert settings.enabled() == set()
    assert settings.ignore_bots is True


def test_jede_art_kann_einen_eigenen_kanal_haben() -> None:
    channels = {event: 1000 + i for i, event in enumerate(EVENT_TYPES)}
    settings = LoggingSettings(channels=channels)
    assert settings.enabled() == set(EVENT_TYPES)
    assert len(set(settings.channels.values())) == len(EVENT_TYPES)


def test_unbekannte_art_wird_abgelehnt() -> None:
    with pytest.raises(ValidationError, match="unbekannte Ereignisart"):
        LoggingSettings(channels={"typo": 1})


@pytest.mark.parametrize("channel_id", [0, -5])
def test_ungueltige_kanal_id(channel_id: int) -> None:
    with pytest.raises(ValidationError):
        LoggingSettings(channels={"voice": channel_id})


def test_zu_viele_ignorierte_kanaele() -> None:
    with pytest.raises(ValidationError):
        LoggingSettings(ignored_channels=list(range(101)))


def test_intent_zuordnung_ist_vollstaendig_und_ueberschneidungsfrei() -> None:
    assert set(EVENT_TYPES) >= NEEDS_MESSAGE_CONTENT
    assert set(EVENT_TYPES) >= NEEDS_MEMBERS
    assert not NEEDS_MESSAGE_CONTENT & NEEDS_MEMBERS


def test_roundtrip() -> None:
    original = LoggingSettings(channels={"voice": 5}, ignored_channels=[1, 2], ignore_bots=False)
    assert LoggingSettings.model_validate_json(original.model_dump_json()) == original
