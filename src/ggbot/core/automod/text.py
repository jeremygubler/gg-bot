"""Textnormalisierung für Wortfilter und Duplikaterkennung."""

import re
import unicodedata

# Unicode-Wörter. RE2-`\w` und `\b` kennen nur ASCII, deshalb zerlegen wir selbst.
_TOKEN = re.compile(r"[^\W]+", re.UNICODE)


def normalize(text: str) -> str:
    """Vergleichsform: NFKD, ohne Akzente und unsichtbare Zeichen, kleingeschrieben.

    So treffen "Ärger", "arger" und "ARGER" dasselbe, und Zero-Width-Zeichen mitten im Wort
    helfen nicht beim Umgehen.
    """
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    kept = [
        ch for ch in decomposed if unicodedata.category(ch) not in ("Mn", "Cf") and ch not in "​‌‍﻿"
    ]
    return unicodedata.normalize("NFKC", "".join(kept))


def tokens(normalized: str) -> list[str]:
    return _TOKEN.findall(normalized)


def collapse(text: str) -> str:
    """Für Duplikate: gleiche Wörter, egal wie Leerzeichen und Grossschreibung stehen."""
    return " ".join(tokens(normalize(text)))
