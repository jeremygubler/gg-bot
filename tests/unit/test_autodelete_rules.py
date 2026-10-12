from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from ggbot.core.autodelete import rules
from ggbot.core.autodelete.rules import Message
from ggbot.core.settings.autodelete import AutodeleteSettings

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def msg(mid: int, age: timedelta, *, pinned: bool = False) -> Message:
    return Message(mid, NOW - age, pinned)


def plan(messages: list[Message], **kw: object) -> rules.Plan:
    args: dict[str, object] = {"now": NOW, "max_age_minutes": 60, "keep_pinned": True}
    return rules.plan(messages, **{**args, **kw})  # type: ignore[arg-type]


def test_genau_so_alt_wie_erlaubt_wird_geloescht_eine_sekunde_juenger_nicht() -> None:
    result = plan(
        [msg(1, timedelta(minutes=60)), msg(2, timedelta(minutes=60) - timedelta(seconds=1))]
    )

    assert result.bulk == (1,)


def test_junge_nachrichten_bleiben_auch_wenn_der_aufrufer_sie_liefert() -> None:
    assert plan([msg(1, timedelta(minutes=1)), msg(2, timedelta(seconds=0))]).total == 0


def test_angeheftete_bleiben_nur_wenn_gewuenscht() -> None:
    messages = [msg(1, timedelta(hours=3), pinned=True), msg(2, timedelta(hours=3))]

    assert plan(messages, keep_pinned=True).bulk == (2,)
    assert plan(messages, keep_pinned=False).bulk == (1, 2)


def test_geschuetzte_nachrichten_bleiben_immer() -> None:
    messages = [msg(1, timedelta(days=30)), msg(2, timedelta(hours=3)), msg(3, timedelta(hours=3))]

    result = plan(messages, protected=frozenset({1, 2}), keep_pinned=False)

    assert result.bulk == (3,)
    assert result.single == ()


def test_sammel_und_einzelloeschung_trennen_sich_an_vierzehn_tagen_minus_reserve() -> None:
    edge = rules.BULK_LIMIT - rules.BULK_MARGIN
    messages = [
        msg(1, edge - timedelta(seconds=1)),
        msg(2, edge),
        msg(3, rules.BULK_LIMIT + timedelta(days=1)),
    ]

    result = plan(messages, max_age_minutes=10)

    assert result.bulk == (1,)
    assert result.single == (2, 3)


def test_obergrenzen_je_lauf() -> None:
    messages = [msg(i, timedelta(hours=2)) for i in range(1, 501)]
    old = [msg(1000 + i, timedelta(days=30)) for i in range(100)]

    result = plan(messages + old)

    assert len(result.bulk) == rules.MAX_BULK_PER_RUN
    assert len(result.single) == rules.MAX_SINGLE_PER_RUN
    assert result.bulk[0] == 1  # die Reihenfolge der Eingabe bleibt


def test_eigene_obergrenzen() -> None:
    messages = [msg(i, timedelta(hours=2)) for i in range(10)]

    assert plan(messages, max_bulk=3).bulk == (0, 1, 2)
    assert plan(messages, max_bulk=0).total == 0


def test_leere_eingabe() -> None:
    assert plan([]) == rules.Plan((), ())


def test_cutoff() -> None:
    assert rules.cutoff(NOW, 90) == NOW - timedelta(minutes=90)


# --- Einstellungen ---


def test_standard_ist_aus_und_ohne_regeln() -> None:
    settings = AutodeleteSettings()

    assert settings.enabled is False
    assert settings.rules == []


@pytest.mark.parametrize("minutes", [0, 9, -5, 90 * 24 * 60 + 1])
def test_alter_ausserhalb_der_grenzen_wird_abgelehnt(minutes: int) -> None:
    with pytest.raises(ValidationError):
        AutodeleteSettings.model_validate(
            {"rules": [{"channel_id": "1", "max_age_minutes": minutes}]}
        )


def test_grenzen_selbst_sind_erlaubt() -> None:
    for minutes in (10, 90 * 24 * 60):
        AutodeleteSettings.model_validate(
            {"rules": [{"channel_id": "1", "max_age_minutes": minutes}]}
        )


def test_ein_kanal_nur_eine_regel() -> None:
    with pytest.raises(ValidationError, match="nur eine Regel"):
        AutodeleteSettings.model_validate(
            {
                "rules": [
                    {"channel_id": "1", "max_age_minutes": 60},
                    {"channel_id": 1, "max_age_minutes": 120},
                ]
            }
        )


def test_hoechstens_zwanzig_regeln() -> None:
    many = [{"channel_id": str(i + 1), "max_age_minutes": 60} for i in range(21)]

    with pytest.raises(ValidationError):
        AutodeleteSettings.model_validate({"rules": many})


def test_angeheftete_bleiben_standardmaessig_und_ids_sind_strings_im_json() -> None:
    settings = AutodeleteSettings.model_validate(
        {"rules": [{"channel_id": 987654321098765432, "max_age_minutes": 60}]}
    )

    assert settings.rules[0].keep_pinned is True
    assert settings.model_dump(mode="json")["rules"][0]["channel_id"] == "987654321098765432"


def test_vierzehn_tage_grenze_mit_festen_werten() -> None:
    """Fest statt aus den Konstanten gerechnet: eine zu knappe Reserve fällt sonst nicht auf."""
    safe = msg(1, timedelta(days=13, hours=22))  # sicher unter 14 Tagen: gesammelt
    close = msg(2, timedelta(days=13, hours=23, minutes=30))  # zu knapp: einzeln

    result = plan([safe, close], max_age_minutes=10)

    assert result.bulk == (1,)
    assert result.single == (2,)
