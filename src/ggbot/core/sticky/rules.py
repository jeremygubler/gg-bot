"""Regeln der Sticky-Nachrichten. Ohne Discord."""

from ggbot.core.errors import UserError

MAX_STICKY_PER_GUILD = 10
MAX_TEXT = 1500
# So lange wartet der Bot nach einer Nachricht, bevor er die Sticky neu setzt. Ohne Pause käme
# sie bei jeder Nachricht neu (Ratenlimit, flackernder Kanal); so höchstens einmal je Fenster.
REPOST_DELAY_SECONDS = 8


def clean_text(text: str) -> str:
    cleaned = text.strip()
    if not cleaned:
        raise UserError("error.sticky_empty")
    if len(cleaned) > MAX_TEXT:
        raise UserError("error.sticky_long", limit=MAX_TEXT)
    return cleaned
