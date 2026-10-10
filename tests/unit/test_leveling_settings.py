import pytest
from pydantic import ValidationError

from ggbot.core.leveling.announce import render_template, validate_template
from ggbot.core.settings.card import CardStyle
from ggbot.core.settings.leveling import LevelingSettings, VoiceXp


def test_standardwerte_entsprechen_mee6_und_sind_aus() -> None:
    s = LevelingSettings()
    assert (s.xp_min, s.xp_max, s.cooldown_seconds) == (15, 25, 60)
    assert s.enabled is False
    assert s.voice.enabled is False
    assert s.voice.requires_company is True
    assert s.announce == "current"
    assert s.reward_mode == "stack"
    assert s.multiplier == 1.0


@pytest.mark.parametrize(
    "payload",
    [
        {"xp_min": 30, "xp_max": 20},
        {"xp_min": 0},
        {"xp_max": 1001},
        {"cooldown_seconds": -1},
        {"cooldown_seconds": 3601},
        {"multiplier": 0.05},
        {"multiplier": 11},
        {"announce": "channel"},  # ohne Kanal
        {"announce": "laut"},
        {"voice": {"xp_min": 50, "xp_max": 10}},
        {"rewards": {0: 5}},
        {"rewards": {1001: 5}},
        {"rewards": {5: 0}},
        {"role_multipliers": {1: 0.05}},
        {"channel_multipliers": {1: 11}},
        {"no_xp_channels": list(range(101))},
        {"announce_message": "{unbekannt}"},
        {"announce_message": "{level"},
        {"announce_message": "x" * 301},
        {"announce_message": "{level:>10}"},
    ],
)
def test_ungueltige_einstellungen(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        LevelingSettings.model_validate(payload)


def test_zu_viele_belohnungen_und_boosts() -> None:
    with pytest.raises(ValidationError):
        LevelingSettings(rewards={i: i + 1 for i in range(1, 52)})
    with pytest.raises(ValidationError):
        LevelingSettings(role_multipliers={i: 2.0 for i in range(1, 52)})


def test_belohnungen_werden_nach_level_sortiert() -> None:
    s = LevelingSettings(rewards={20: 2, 5: 1, 10: 3})
    assert list(s.rewards) == [5, 10, 20]


def test_roundtrip_durch_json_behaelt_integer_schluessel() -> None:
    original = LevelingSettings(
        rewards={5: 100},
        role_multipliers={7: 1.5},
        channel_multipliers={8: 2.0},
        announce="channel",
        announce_channel_id=9,
        announce_message="GG {user}: Level {level}!",
    )
    again = LevelingSettings.model_validate_json(original.model_dump_json())
    assert again == original
    assert again.rewards == {5: 100}


def test_kartenstil_grenzen() -> None:
    assert CardStyle().template == "gradient"
    for bad in ({"accent": -1}, {"background": 0x1000000}, {"template": "neon"}):
        with pytest.raises(ValidationError):
            CardStyle.model_validate(bad)
    VoiceXp(xp_min=5, xp_max=5)


def test_vorlage_wird_gerendert() -> None:
    text = render_template(
        "GG {user} ({name}): Level {level}, {xp} XP", user="<@1>", name="Ann", level=5, xp=1250
    )
    assert text == "GG <@1> (Ann): Level 5, 1250 XP"
    validate_template("ohne Platzhalter")
    validate_template("{{wörtliche Klammern}}")


def test_vorlage_mit_klammern_und_unbekanntem_feld_ist_laut() -> None:
    with pytest.raises(ValueError, match="unbekannter Platzhalter"):
        render_template("{secret}", user="a", name="b", level=1, xp=1)
    with pytest.raises(ValueError, match="unbekannter Platzhalter"):
        render_template("{user.__class__}", user="a", name="b", level=1, xp=1)
    with pytest.raises(ValueError, match="unbekannter Platzhalter"):
        render_template("{0}", user="a", name="b", level=1, xp=1)
