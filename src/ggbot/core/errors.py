"""Fehler, die dem Nutzer gezeigt werden dürfen.

Alles andere ist ein Bug und wird geloggt, der Nutzer sieht nur eine Fehler-ID.
"""

from typing import Any


class UserError(Exception):
    """Freundlicher, übersetzbarer Fehler (i18n-Schlüssel + Parameter)."""

    def __init__(self, key: str, **params: Any) -> None:
        super().__init__(key)
        self.key = key
        self.params = params
