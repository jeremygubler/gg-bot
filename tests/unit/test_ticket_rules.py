from datetime import UTC, datetime, timedelta

import pytest

from ggbot.core.tickets.rules import (
    DISCORD_EPOCH_MS,
    can_close,
    can_manage,
    channel_name,
    clean_questions,
    is_inactive,
    snowflake_time,
)

NOW = datetime(2026, 1, 10, 12, 0, tzinfo=UTC)


def test_support_ist_rolle_oder_verwalter() -> None:
    assert can_manage({1, 2}, {2, 9}, is_manager=False)
    assert not can_manage({1, 3}, {2, 9}, is_manager=False)
    assert not can_manage(set(), {2}, is_manager=False)
    assert can_manage(set(), {2}, is_manager=True)
    assert not can_manage({1}, set(), is_manager=False)  # keine Support-Rolle konfiguriert


@pytest.mark.parametrize(
    ("user", "roles", "manager", "creators", "expected"),
    [
        (5, set(), False, True, True),  # Ersteller darf
        (5, set(), False, False, False),  # Ersteller darf nicht
        (5, {9}, False, False, True),  # Ersteller ist selbst Support
        (6, set(), False, True, False),  # Fremder
        (6, {9}, False, False, True),  # Support
        (6, set(), True, False, True),  # Verwalter
    ],
)
def test_wer_schliessen_darf(
    user: int, roles: set[int], manager: bool, creators: bool, expected: bool
) -> None:
    assert (
        can_close(user, 5, roles, {9}, is_manager=manager, creators_may_close=creators) is expected
    )


def test_schnipsel_zeit() -> None:
    snowflake = (1_700_000_000_000 - DISCORD_EPOCH_MS) << 22
    assert snowflake_time(snowflake) == datetime.fromtimestamp(1_700_000_000, tz=UTC)
    assert snowflake_time(snowflake | 0x3FFFFF) == snowflake_time(snowflake)  # Rest-Bits egal


def test_inaktivitaet() -> None:
    created = NOW - timedelta(hours=30)
    assert is_inactive(NOW, created, None, 24)  # nie geschrieben, älter als 24 h
    assert not is_inactive(NOW, created, NOW - timedelta(hours=1), 24)
    assert is_inactive(NOW, created, NOW - timedelta(hours=24), 24)  # genau an der Grenze
    assert not is_inactive(NOW, created, NOW - timedelta(hours=23, minutes=59), 24)
    assert not is_inactive(NOW, NOW - timedelta(hours=2), None, 24)  # frisches Ticket
    assert not is_inactive(NOW, created, None, 0)  # ausgeschaltet
    assert not is_inactive(NOW, created, None, -5)
    # Eine Nachricht vor der Erstellung (Uhrenabweichung) zählt nicht als ältere Aktivität.
    assert not is_inactive(NOW, NOW - timedelta(hours=1), NOW - timedelta(hours=100), 24)


@pytest.mark.parametrize(
    ("template", "expected"),
    [
        ("ticket-{number}", "ticket-0042"),
        ("{category}-{number}", "hilfe-0042"),
        ("{user}-{number}", "max-mustermann-0042"),
        ("support", "support-0042"),  # Nummer fehlt in der Vorlage → wird angehängt
        ("Ticket {number}!!", "ticket-0042"),
        ("{number}", "0042"),
        ("   ", "ticket-0042"),
        ("{category}--{number}", "hilfe-0042"),
    ],
)
def test_kanalname(template: str, expected: str) -> None:
    name = channel_name(template, number=42, category="Hilfe", user="Max Mustermann")
    assert name == expected


def test_kanalname_entfernt_unsicheres() -> None:
    name = channel_name("{category}-{number}", number=7, category="Ärger & @everyone <b>", user="x")
    assert name == "rger-everyone-b-0007"
    assert all(ch.islower() or ch.isdigit() or ch == "-" for ch in name)
    assert "@" not in name
    assert "--" not in name


def test_kanalname_maximal_100_zeichen() -> None:
    name = channel_name("{category}-{number}", number=123, category="x" * 300, user="u")
    assert len(name) <= 100
    assert name.endswith("-0123")


def test_fragen() -> None:
    assert clean_questions("Name; Grund ;; ") == ["Name", "Grund"]
    assert clean_questions("") == []
    assert clean_questions("a;b;c;d;e") == ["a", "b", "c", "d", "e"]
    with pytest.raises(ValueError, match="höchstens 5"):
        clean_questions("a;b;c;d;e;f")
    with pytest.raises(ValueError, match="45"):
        clean_questions("x" * 46)
    assert clean_questions("x" * 45) == ["x" * 45]
