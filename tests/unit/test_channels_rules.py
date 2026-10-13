"""Regeln für Vorschläge, Sticky-Nachrichten und temporäre Sprachkanäle."""

import pytest
from pydantic import ValidationError

from ggbot.core.errors import UserError
from ggbot.core.settings.suggestions import SuggestionSettings
from ggbot.core.settings.tempvoice import TempVoiceSettings
from ggbot.core.sticky import rules as sticky
from ggbot.core.suggestions import rules as sug
from ggbot.core.tempvoice import rules as voice

# --- Vorschläge ---


def test_gleicher_klick_nimmt_die_stimme_zurueck() -> None:
    assert sug.toggle(None, sug.UP) == sug.UP
    assert sug.toggle(sug.UP, sug.UP) is None
    assert sug.toggle(sug.DOWN, sug.DOWN) is None


def test_anderer_klick_wechselt_die_stimme() -> None:
    assert sug.toggle(sug.UP, sug.DOWN) == sug.DOWN
    assert sug.toggle(sug.DOWN, sug.UP) == sug.UP


def test_ungueltige_stimme_wird_abgelehnt() -> None:
    with pytest.raises(ValueError, match="Stimme"):
        sug.toggle(None, 2)


def test_stand_zaehlt_dafuer_minus_dagegen() -> None:
    assert sug.Tally(5, 2).score == 3
    assert sug.Tally().score == 0


def test_vorschlagstext_wird_bereinigt_und_begrenzt() -> None:
    assert sug.clean_text("  mehr   Kanäle  ") == "mehr Kanäle"
    with pytest.raises(UserError) as empty:
        sug.clean_text("   ")
    assert empty.value.key == "error.suggest_empty"
    with pytest.raises(UserError) as long:
        sug.clean_text("x" * (sug.MAX_TEXT + 1))
    assert long.value.key == "error.suggest_long"
    assert len(sug.clean_text("x" * sug.MAX_TEXT)) == sug.MAX_TEXT


def test_vorschlaege_brauchen_einen_kanal() -> None:
    with pytest.raises(ValidationError):
        SuggestionSettings(enabled=True)
    assert SuggestionSettings(enabled=True, channel_id=5).channel_id == 5


# --- Sticky ---


def test_sticky_text_wird_geprueft() -> None:
    assert sticky.clean_text("  Regeln  ") == "Regeln"
    with pytest.raises(UserError) as empty:
        sticky.clean_text(" ")
    assert empty.value.key == "error.sticky_empty"
    with pytest.raises(UserError) as long:
        sticky.clean_text("x" * (sticky.MAX_TEXT + 1))
    assert long.value.key == "error.sticky_long"


# --- Temporäre Sprachkanäle ---


def test_kanalname_aus_vorlage() -> None:
    assert voice.channel_name("{user}s Raum", user="Mia", number=3) == "Mias Raum"
    assert voice.channel_name("Raum {number}", user="Mia", number=3) == "Raum 3"


def test_kanalname_ist_nie_leer_und_nie_zu_lang() -> None:
    assert voice.channel_name("{user}", user="​\n\t", number=1) == voice.FALLBACK_NAME
    assert len(voice.channel_name("{user}", user="x" * 500, number=1)) == voice.MAX_NAME


def test_kanalname_ohne_steuerzeichen_und_zeilenumbrueche() -> None:
    cleaned = voice.clean_name("a\nb\x07c   d")
    assert "\n" not in cleaned
    assert "\x07" not in cleaned
    assert "  " not in cleaned


def test_vorlage_mit_unbekanntem_feld_ist_ungueltig() -> None:
    with pytest.raises(ValidationError):
        TempVoiceSettings(name_template="{nope}")


def test_sprachkanaele_brauchen_eine_lobby() -> None:
    with pytest.raises(ValidationError):
        TempVoiceSettings(enabled=True)


def test_grenzen_der_sprachkanal_einstellungen() -> None:
    for bad in ({"user_limit": 100}, {"max_channels": 0}, {"max_channels": 51}):
        with pytest.raises(ValidationError):
            TempVoiceSettings(**bad)
