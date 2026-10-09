import pytest
from pydantic import ValidationError

from ggbot.core.duration import MAX_TIMEOUT
from ggbot.core.settings.automod import (
    AutomodSettings,
    InvitesRule,
    JoinAction,
    LinksRule,
    RuleAction,
    WordEntry,
    WordsRule,
)


def test_alle_regeln_sind_standardmaessig_aus() -> None:
    settings = AutomodSettings()
    assert not settings.any_message_rule()
    assert not settings.any_join_rule()
    assert settings.exempt_moderators is True
    assert settings.log_channel_id is None


def test_standardaktion_loescht() -> None:
    assert RuleAction().delete is True


def test_aktion_ohne_wirkung_wird_abgelehnt() -> None:
    with pytest.raises(ValidationError, match="tut nichts"):
        RuleAction(delete=False, warn=False, timeout_seconds=None)
    RuleAction(delete=False, warn=True)
    RuleAction(delete=False, timeout_seconds=60)


@pytest.mark.parametrize("seconds", [0, -1, MAX_TIMEOUT + 1])
def test_timeout_grenzen(seconds: int) -> None:
    with pytest.raises(ValidationError):
        RuleAction(timeout_seconds=seconds)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"action": "timeout"},
        {"action": "kick", "timeout_seconds": 60},
        {"action": "ban", "timeout_seconds": 60},
        {"action": "timeout", "timeout_seconds": MAX_TIMEOUT + 1},
        {"action": "mute"},
    ],
)
def test_beitrittsaktion_ungueltig(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        JoinAction.model_validate(kwargs)


def test_beitrittsaktion_gueltig() -> None:
    JoinAction(action="timeout", timeout_seconds=3600)
    JoinAction(action="kick")
    JoinAction(action="ban")


def test_ungueltiges_wortmuster_wird_beim_anlegen_abgelehnt() -> None:
    with pytest.raises(ValidationError, match="automod_regex_invalid"):
        WordEntry(type="regex", pattern="(kaputt")
    with pytest.raises(ValidationError, match="automod_wildcard_space"):
        WordEntry(type="wildcard", pattern="zwei wörter*")
    WordEntry(type="regex", pattern=r"\d+")


def test_zu_langes_muster_und_zu_viele_eintraege() -> None:
    with pytest.raises(ValidationError):
        WordEntry(type="word", pattern="x" * 201)
    entries = [WordEntry(type="word", pattern=f"wort{i}") for i in range(201)]
    with pytest.raises(ValidationError):
        WordsRule(entries=entries)


def test_domains_und_codes_werden_normalisiert() -> None:
    assert LinksRule(domains=["WWW.Example.COM", "example.com", " b.org. "]).domains == [
        "b.org",
        "example.com",
    ]
    assert InvitesRule(allowed_codes=["ABC", "abc", " "]).allowed_codes == ["abc"]


@pytest.mark.parametrize(
    "payload",
    [
        {"spam": {"messages": 1}},
        {"spam": {"seconds": 0}},
        {"duplicates": {"count": 1}},
        {"caps": {"max_percent": 20}},
        {"caps": {"min_length": 2}},
        {"mentions": {"max_mentions": 1}},
        {"raid": {"joins": 1}},
        {"raid": {"mode_minutes": 0}},
        {"accountage": {"min_days": 0}},
        {"exempt_channels": list(range(101))},
    ],
)
def test_grenzwerte(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        AutomodSettings.model_validate(payload)


def test_unbekannte_felder_werden_ignoriert() -> None:
    settings = AutomodSettings.model_validate({"spam": {"enabled": True, "zukunft": 1}, "x": 2})
    assert settings.spam.enabled is True
    assert settings.any_message_rule()


def test_roundtrip_durch_json() -> None:
    original = AutomodSettings(
        words=WordsRule(enabled=True, entries=[WordEntry(type="regex", pattern=r"\d+")])
    )
    again = AutomodSettings.model_validate_json(original.model_dump_json())
    assert again == original
