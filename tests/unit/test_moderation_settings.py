import pytest
from pydantic import ValidationError

from ggbot.core.duration import DAY, MAX_TEMPBAN, MAX_TIMEOUT
from ggbot.core.settings.schema import EscalationRule, ModerationSettings


def test_standard_hat_keine_eskalation() -> None:
    settings = ModerationSettings()
    assert settings.escalation == []
    assert settings.dm_on_action is True
    assert settings.warn_expiry_days is None


def test_regeln_werden_sortiert() -> None:
    settings = ModerationSettings(
        escalation=[
            EscalationRule(warns=5, action="kick"),
            EscalationRule(warns=3, action="timeout", duration_seconds=3600),
        ]
    )
    assert [r.warns for r in settings.escalation] == [3, 5]


def test_doppelter_schwellwert_wird_abgelehnt() -> None:
    with pytest.raises(ValidationError, match="nur eine Regel"):
        ModerationSettings(
            escalation=[
                EscalationRule(warns=3, action="kick"),
                EscalationRule(warns=3, action="ban"),
            ]
        )


@pytest.mark.parametrize(
    "rule",
    [
        {"warns": 3, "action": "timeout"},  # Dauer fehlt
        {"warns": 3, "action": "timeout", "duration_seconds": MAX_TIMEOUT + 1},
        {"warns": 3, "action": "timeout", "duration_seconds": 0},
        {"warns": 3, "action": "tempban"},
        {"warns": 3, "action": "tempban", "duration_seconds": 59},
        {"warns": 3, "action": "tempban", "duration_seconds": MAX_TEMPBAN + 1},
        {"warns": 3, "action": "kick", "duration_seconds": 60},
        {"warns": 3, "action": "ban", "duration_seconds": 60},
        {"warns": 0, "action": "kick"},
        {"warns": 101, "action": "kick"},
        {"warns": 3, "action": "softban"},
    ],
)
def test_ungueltige_regeln(rule: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        EscalationRule.model_validate(rule)


def test_gueltige_regeln() -> None:
    EscalationRule(warns=3, action="timeout", duration_seconds=MAX_TIMEOUT)
    EscalationRule(warns=4, action="tempban", duration_seconds=7 * DAY)
    EscalationRule(warns=5, action="kick")
    EscalationRule(warns=6, action="ban")


def test_warn_expiry_grenzen() -> None:
    for bad in (0, -1, 3651):
        with pytest.raises(ValidationError):
            ModerationSettings(warn_expiry_days=bad)
