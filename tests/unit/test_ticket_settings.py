import pytest
from pydantic import ValidationError

from ggbot.core.settings.tickets import TicketSettings


def test_standardwerte() -> None:
    s = TicketSettings()
    assert (s.max_open_per_user, s.auto_close_hours, s.close_delay_seconds) == (3, 0, 10)
    assert s.feedback
    assert s.dm_transcript
    assert s.creators_may_close
    assert s.ping_support
    assert (s.log_channel_id, s.retention_days, s.name_template) == (None, 0, "ticket-{number}")


def test_alte_staende_bleiben_gueltig() -> None:
    assert TicketSettings.model_validate({}) == TicketSettings()
    assert TicketSettings.model_validate({"neu": 1}) == TicketSettings()


@pytest.mark.parametrize(
    "data",
    [
        {"max_open_per_user": 0},
        {"max_open_per_user": 11},
        {"auto_close_hours": -1},
        {"auto_close_hours": 721},
        {"close_delay_seconds": 301},
        {"retention_days": 3651},
        {"name_template": ""},
        {"name_template": "x" * 51},
        {"name_template": "{user.__class__}"},
        {"name_template": "{level}"},
        {"name_template": "{number!r}"},
        {"name_template": "{number:>9999}"},
        {"name_template": "{"},
    ],
)
def test_ungueltig(data: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        TicketSettings.model_validate(data)


def test_grenzwerte_gueltig() -> None:
    s = TicketSettings.model_validate(
        {"max_open_per_user": 10, "auto_close_hours": 720, "close_delay_seconds": 0,
         "retention_days": 3650, "name_template": "{category}-{user}-{number}"}
    )  # fmt: skip
    assert s.name_template == "{category}-{user}-{number}"
