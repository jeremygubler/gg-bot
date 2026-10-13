"""Regeln der Vorschläge: Stimmen und Text. Ohne Discord."""

from dataclasses import dataclass

from ggbot.core.errors import UserError

MAX_TEXT = 1000
UP = 1
DOWN = -1


@dataclass(frozen=True)
class Tally:
    up: int = 0
    down: int = 0

    @property
    def score(self) -> int:
        return self.up - self.down


def toggle(current: int | None, clicked: int) -> int | None:
    """Neue Stimme nach einem Klick: dieselbe Taste nimmt sie zurück, die andere wechselt."""
    if clicked not in (UP, DOWN):
        raise ValueError("Stimme muss +1 oder -1 sein")
    return None if current == clicked else clicked


def clean_text(text: str) -> str:
    cleaned = " ".join(text.split()) if "\n" not in text else text.strip()
    if not cleaned:
        raise UserError("error.suggest_empty")
    if len(cleaned) > MAX_TEXT:
        raise UserError("error.suggest_long", limit=MAX_TEXT)
    return cleaned
