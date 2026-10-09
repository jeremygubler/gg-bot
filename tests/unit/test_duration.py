import pytest

from ggbot.core.duration import format_duration, parse_duration
from ggbot.core.errors import UserError


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        ("30s", 30),
        ("10m", 600),
        ("2h", 7200),
        ("1d", 86400),
        ("1w", 604800),
        ("1d12h", 129600),
        ("1h 30m", 5400),
        (" 5M ", 300),
        ("90m", 5400),
    ],
)
def test_gueltige_dauern(text: str, seconds: int) -> None:
    assert parse_duration(text) == seconds


@pytest.mark.parametrize(
    "text", ["", "10", "abc", "h", "0m", "1x", "1h foo", "foo 1h", "-5m", "1h-"]
)
def test_ungueltige_dauern(text: str) -> None:
    with pytest.raises(UserError) as exc:
        parse_duration(text)
    assert exc.value.key == "error.invalid_duration"


def test_format_roundtrip() -> None:
    assert format_duration(90061) == "1d 1h 1m 1s"
    assert format_duration(3600) == "1h"
    assert format_duration(0) == "0s"
    assert parse_duration(format_duration(129600).replace(" ", "")) == 129600
