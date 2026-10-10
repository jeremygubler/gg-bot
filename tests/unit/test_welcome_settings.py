import pytest
from pydantic import ValidationError

from ggbot.core.settings.card import CardStyle
from ggbot.core.settings.welcome import MAX_AUTOROLES, WelcomeSettings


def test_standard_ist_ausgeschaltet() -> None:
    s = WelcomeSettings()
    assert (s.join_enabled, s.leave_enabled, s.dm_enabled) == (False, False, False)
    assert s.autoroles == []
    assert s.bot_autoroles == []
    assert s.join_card is True
    assert s.card == CardStyle()


def test_alte_staende_ohne_felder_bleiben_gueltig() -> None:
    assert WelcomeSettings.model_validate({}) == WelcomeSettings()
    assert WelcomeSettings.model_validate({"unbekannt": 1}) == WelcomeSettings()


@pytest.mark.parametrize("field", ["join", "leave"])
def test_einschalten_braucht_einen_kanal(field: str) -> None:
    with pytest.raises(ValidationError):
        WelcomeSettings.model_validate({f"{field}_enabled": True})
    ok = WelcomeSettings.model_validate({f"{field}_enabled": True, f"{field}_channel_id": 5})
    assert getattr(ok, f"{field}_channel_id") == 5


@pytest.mark.parametrize(
    ("field", "template"),
    [
        ("join_message", "{user} {name} {server} {count}"),
        ("leave_message", "{name} {server} {count}"),
        ("dm_message", "{name} {server}"),
    ],
)
def test_erlaubte_platzhalter(field: str, template: str) -> None:
    assert getattr(WelcomeSettings.model_validate({field: template}), field) == template


@pytest.mark.parametrize(
    ("field", "template"),
    [
        ("leave_message", "{user} ist weg"),  # Ausgetretene werden nicht erwähnt
        ("dm_message", "{count}"),
        ("join_message", "{level}"),
        ("join_message", "{user.__class__}"),
        ("join_message", "{0}"),
        ("join_message", "{user!r}"),
        ("join_message", "{name:>99999}"),
        ("join_message", "kaputt {"),
        ("join_message", "x" * 301),
    ],
)
def test_ungueltige_vorlagen(field: str, template: str) -> None:
    with pytest.raises(ValidationError):
        WelcomeSettings.model_validate({field: template})


def test_autorollen_ohne_doppelte_in_reihenfolge() -> None:
    s = WelcomeSettings.model_validate({"autoroles": [3, 1, 3, 2, 1]})
    assert s.autoroles == [3, 1, 2]


def test_autorollen_grenzen() -> None:
    WelcomeSettings.model_validate({"autoroles": list(range(1, MAX_AUTOROLES + 1))})
    with pytest.raises(ValidationError):
        WelcomeSettings.model_validate({"autoroles": list(range(1, MAX_AUTOROLES + 2))})
    with pytest.raises(ValidationError):
        WelcomeSettings.model_validate({"bot_autoroles": [0]})
    with pytest.raises(ValidationError):
        WelcomeSettings.model_validate({"autoroles": [-4]})


def test_kartenstil_ist_der_gleiche_wie_beim_leveling() -> None:
    from ggbot.core.settings.leveling import LevelingSettings

    assert type(WelcomeSettings().card) is type(LevelingSettings().card)
    with pytest.raises(ValidationError):
        WelcomeSettings.model_validate({"card": {"accent": 0x1000000}})
