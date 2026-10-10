"""Geburtstage: gültige Daten und wer heute feiert. Rein, die Zeit kommt herein."""

import calendar
from datetime import date, datetime
from zoneinfo import ZoneInfo


def valid_date(day: int, month: int) -> bool:
    """Gültig ist jedes Datum, das in irgendeinem Jahr existiert (auch der 29. Februar)."""
    if not 1 <= month <= 12:
        return False
    return 1 <= day <= calendar.monthrange(2024, month)[1]  # 2024 ist ein Schaltjahr


def is_birthday(day: int, month: int, today: date) -> bool:
    """Heute? Wer am 29. Februar geboren ist, feiert in anderen Jahren am 28."""
    if (day, month) == (29, 2) and not calendar.isleap(today.year):
        return (today.day, today.month) == (28, 2)
    return (day, month) == (today.day, today.month)


def local_today(now: datetime, timezone: str) -> date:
    """Das heutige Datum des Servers (seine Zeitzone), nicht das der Maschine."""
    return now.astimezone(ZoneInfo(timezone)).date()
