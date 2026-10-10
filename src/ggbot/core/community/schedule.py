"""Geplante Nachrichten: wann die nächste kommt. Rein."""

from datetime import datetime, timedelta


def next_run(previous: datetime, interval: timedelta, now: datetime) -> datetime:
    """Der nächste Termin nach `now` im Raster von `previous` + k·`interval`.

    War der Bot lange aus, wird nicht für jede verpasste Runde nachgeholt (das gäbe eine Flut),
    sondern der nächste Termin im alten Raster genommen.
    """
    if interval <= timedelta(0):
        raise ValueError("Intervall muss positiv sein")
    candidate = previous + interval
    if candidate > now:
        return candidate
    missed = (now - candidate) // interval + 1
    return candidate + missed * interval
