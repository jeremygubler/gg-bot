"""Regeln der Verifizierung ohne Discord und ohne Zufall aus der Umgebung."""

import hmac
from collections.abc import Callable

# Ohne die verwechselbaren Zeichen 0/O und 1/I: die Aufgabe soll schwer für Skripte sein, nicht für
# Menschen mit schlechter Schrift oder kleinem Bildschirm.
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 5
MAX_ATTEMPTS = 3
CODE_TTL_SECONDS = 300
# Nach drei Fehlversuchen gesperrt: sonst liesse sich der Code durch Raten knacken.
LOCK_SECONDS = 600
# Ein neues Bild höchstens alle so viele Sekunden, sonst liessen sich Fehlversuche neu würfeln.
START_COOLDOWN_SECONDS = 20


def new_code(choice: Callable[[str], str]) -> str:
    """Der Zufall kommt von aussen (`secrets.choice` im Betrieb, ein fester Wert im Test)."""
    return "".join(choice(ALPHABET) for _ in range(CODE_LENGTH))


def normalize(answer: str) -> str:
    """Gross, ohne Leerzeichen und Bindestriche: wer „ab c-d“ tippt, hat es richtig abgelesen."""
    return "".join(ch for ch in answer.upper() if not ch.isspace() and ch != "-")


def matches(expected: str, given: str) -> bool:
    """Zeitkonstanter Vergleich; die Antwort ist kein Geheimnis, aber das kostet nichts."""
    return hmac.compare_digest(normalize(expected).encode(), normalize(given).encode())
