"""Dauern wie "90m", "2h" oder "1d12h" als Sekunden."""

import re

from ggbot.core.errors import UserError

MINUTE = 60
HOUR = 3600
DAY = 86400

MAX_TIMEOUT = 28 * DAY  # Limit von Discord
MIN_TEMPBAN = MINUTE
MAX_TEMPBAN = 365 * DAY

_UNITS = {"s": 1, "m": MINUTE, "h": HOUR, "d": DAY, "w": 7 * DAY}
_PART = re.compile(r"(\d+)\s*([smhdw])", re.IGNORECASE)


def parse_duration(text: str) -> int:
    """Einheit ist Pflicht: "10" wäre Minuten, Sekunden oder Tage, das rät niemand gern."""
    cleaned = text.strip().lower()
    pos = 0
    total = 0
    for match in _PART.finditer(cleaned):
        if cleaned[pos : match.start()].strip():
            raise UserError("error.invalid_duration", value=text)
        total += int(match.group(1)) * _UNITS[match.group(2)]
        pos = match.end()
    if pos == 0 or cleaned[pos:].strip() or total <= 0:
        raise UserError("error.invalid_duration", value=text)
    return total


def format_duration(seconds: int) -> str:
    """90061 → "1d 1h 1m 1s". Sprachneutral, damit sie in beide Kataloge passt."""
    if seconds <= 0:
        return "0s"
    parts = []
    for unit, size in (("d", DAY), ("h", HOUR), ("m", MINUTE), ("s", 1)):
        amount, seconds = divmod(seconds, size)
        if amount:
            parts.append(f"{amount}{unit}")
    return " ".join(parts)
