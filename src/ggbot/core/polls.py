"""Umfragen: Eingaben prüfen. Die Umfrage selbst führt Discord (native Umfragen)."""

import math

from ggbot.core.duration import HOUR, parse_duration
from ggbot.core.errors import UserError

MIN_OPTIONS = 2
MAX_OPTIONS = 10  # Grenze von Discord
MAX_QUESTION = 300
MAX_OPTION = 55
MIN_HOURS = 1
MAX_HOURS = 32 * 24  # 32 Tage


def parse_options(text: str) -> list[str]:
    """Antworten aus einem Eingabefeld, getrennt durch `;`. Doppelte (ohne Gross-/Kleinschreibung)
    gelten als Fehler: zwei gleiche Knöpfe verteilten die Stimmen ohne erkennbaren Grund."""
    options = [part.strip() for part in text.split(";")]
    options = [o for o in options if o]
    if not MIN_OPTIONS <= len(options) <= MAX_OPTIONS:
        raise UserError("error.poll_options", low=MIN_OPTIONS, high=MAX_OPTIONS)
    if any(len(o) > MAX_OPTION for o in options):
        raise UserError("error.poll_option_long", limit=MAX_OPTION)
    if len({o.casefold() for o in options}) != len(options):
        raise UserError("error.poll_duplicate")
    return options


def check_question(question: str) -> str:
    question = question.strip()
    if not 1 <= len(question) <= MAX_QUESTION:
        raise UserError("error.poll_question", limit=MAX_QUESTION)
    return question


def duration_hours(text: str) -> int:
    """Dauer in ganzen Stunden (aufgerundet), wie Discord sie verlangt."""
    hours = math.ceil(parse_duration(text) / HOUR)
    if not MIN_HOURS <= hours <= MAX_HOURS:
        raise UserError("error.poll_duration", low=MIN_HOURS, high=MAX_HOURS)
    return hours
