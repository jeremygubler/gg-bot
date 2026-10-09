import string

import pytest

from ggbot.core.i18n import FALLBACK, LOCALES, translator


def _placeholders(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def test_gleiche_schluessel_in_allen_sprachen() -> None:
    keys = {loc: set(translator.catalogs[loc]) for loc in LOCALES}
    assert keys["de"] == keys["en"], keys["de"] ^ keys["en"]


def test_gleiche_platzhalter_in_allen_sprachen() -> None:
    for key, text in translator.catalogs[FALLBACK].items():
        for loc in LOCALES:
            assert _placeholders(translator.catalogs[loc][key]) == _placeholders(text), key


def test_unbekannte_sprache_faellt_auf_englisch() -> None:
    assert translator.t("fr", "help.title") == translator.t("en", "help.title")


def test_fehlender_schluessel_ist_laut() -> None:
    with pytest.raises(KeyError):
        translator.t("de", "gibt.es.nicht")


def test_platzhalter_werden_eingesetzt() -> None:
    assert "abc123" in translator.t("de", "error.generic", error_id="abc123")
