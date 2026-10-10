import pytest

from ggbot.core.errors import UserError
from ggbot.core.polls import (
    MAX_HOURS,
    MAX_OPTION,
    MAX_OPTIONS,
    MAX_QUESTION,
    check_question,
    duration_hours,
    parse_options,
)


def test_antworten() -> None:
    assert parse_options("Ja; Nein ;Vielleicht") == ["Ja", "Nein", "Vielleicht"]
    assert parse_options("a;b;;  ;") == ["a", "b"]  # Leeres fällt weg


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("", "error.poll_options"),
        ("nur eine", "error.poll_options"),
        (";".join(f"o{i}" for i in range(MAX_OPTIONS + 1)), "error.poll_options"),
        ("a;" + "x" * (MAX_OPTION + 1), "error.poll_option_long"),
        ("Ja;ja", "error.poll_duplicate"),
        ("Ja ; JA ", "error.poll_duplicate"),
    ],
)
def test_ungueltige_antworten(text: str, key: str) -> None:
    with pytest.raises(UserError) as exc:
        parse_options(text)
    assert exc.value.key == key


def test_grenzen_gueltig() -> None:
    assert len(parse_options(";".join(f"o{i}" for i in range(MAX_OPTIONS)))) == MAX_OPTIONS
    assert parse_options("a;" + "x" * MAX_OPTION)[1] == "x" * MAX_OPTION


def test_frage() -> None:
    assert check_question("  Wohin?  ") == "Wohin?"
    for bad in ("", "   ", "x" * (MAX_QUESTION + 1)):
        with pytest.raises(UserError) as exc:
            check_question(bad)
        assert exc.value.key == "error.poll_question"
    assert check_question("x" * MAX_QUESTION)


def test_dauer_in_stunden() -> None:
    assert duration_hours("24h") == 24
    assert duration_hours("3d") == 72
    assert duration_hours("90m") == 2  # aufgerundet
    assert duration_hours("30m") == 1  # Discord kennt nur ganze Stunden, mindestens eine
    assert duration_hours("10s") == 1
    assert duration_hours("1h") == 1
    assert duration_hours(f"{MAX_HOURS}h") == MAX_HOURS


@pytest.mark.parametrize("text", ["0h", f"{MAX_HOURS + 1}h", "33d", "769h"])
def test_dauer_ausserhalb(text: str) -> None:
    with pytest.raises(UserError) as exc:
        duration_hours(text)
    assert exc.value.key in ("error.poll_duration", "error.invalid_duration")


def test_dauer_ohne_einheit() -> None:
    with pytest.raises(UserError) as exc:
        duration_hours("24")
    assert exc.value.key == "error.invalid_duration"
