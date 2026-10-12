from itertools import cycle

import pytest

from ggbot.core.verification import rules


def test_code_hat_die_laenge_und_nur_erlaubte_zeichen() -> None:
    import secrets

    for _ in range(200):
        code = rules.new_code(secrets.choice)
        assert len(code) == rules.CODE_LENGTH
        assert set(code) <= set(rules.ALPHABET)


def test_alphabet_enthaelt_keine_verwechselbaren_zeichen() -> None:
    assert not set("0O1I") & set(rules.ALPHABET)
    assert len(set(rules.ALPHABET)) == len(rules.ALPHABET)


def test_zufall_kommt_von_aussen_und_ist_damit_reproduzierbar() -> None:
    letters = cycle("ABC")

    code = rules.new_code(lambda _alphabet: next(letters))

    assert code == "ABCAB"


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("AB2CD", True),
        ("ab2cd", True),
        (" ab 2-cd ", True),
        ("AB2C", False),
        ("AB2CDE", False),
        ("", False),
    ],
)
def test_antwort_wird_grosszuegig_gelesen_aber_streng_geprueft(given: str, expected: bool) -> None:
    assert rules.matches("AB2CD", given) is expected


def test_normalisieren_entfernt_nur_leerraum_und_bindestriche() -> None:
    assert rules.normalize("a b\tc\n-d_e") == "ABCD_E"


def test_grenzen_sind_sinnvoll() -> None:
    assert rules.MAX_ATTEMPTS >= 2
    assert rules.LOCK_SECONDS > rules.CODE_TTL_SECONDS  # Sperre überlebt den Code
