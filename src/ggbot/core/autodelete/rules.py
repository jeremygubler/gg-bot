"""Welche Nachrichten das Auto-Löschen entfernt. Rein: Zeit und Nachrichten kommen als Parameter."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

# Discord löscht gesammelt nur Nachrichten, die jünger als 14 Tage sind. Eine Stunde Abstand, damit
# ein leicht abweichende Uhr nicht den ganzen Stapel scheitern lässt: lieber einzeln löschen.
BULK_LIMIT = timedelta(days=14)
BULK_MARGIN = timedelta(hours=1)
# Obergrenzen je Lauf und Kanal. Ein Rückstand baut sich über mehrere Läufe ab; ein Fehlgriff
# (falscher Kanal) richtet so nicht in einem Atemzug alles aus, und die Einzel-Löschungen bleiben
# unter dem Ratenlimit (etwa 5 je 5 Sekunden).
MAX_BULK_PER_RUN = 200
MAX_SINGLE_PER_RUN = 20


@dataclass(frozen=True)
class Message:
    id: int
    created_at: datetime
    pinned: bool = False


@dataclass(frozen=True)
class Plan:
    bulk: tuple[int, ...]
    single: tuple[int, ...]

    @property
    def total(self) -> int:
        return len(self.bulk) + len(self.single)


def cutoff(now: datetime, max_age_minutes: int) -> datetime:
    """Was älter (oder genau so alt) ist, darf weg."""
    return now - timedelta(minutes=max_age_minutes)


def plan(
    messages: Iterable[Message],
    *,
    now: datetime,
    max_age_minutes: int,
    keep_pinned: bool,
    protected: frozenset[int] = frozenset(),
    max_bulk: int = MAX_BULK_PER_RUN,
    max_single: int = MAX_SINGLE_PER_RUN,
) -> Plan:
    """Sortiert die löschbaren Nachrichten in Sammel- und Einzellöschung.

    Geschützt sind angeheftete Nachrichten (wenn gewünscht) und alles in `protected` (die Panels des
    Bots: ohne ihre Nachricht wären die Knöpfe weg). Zu junge Nachrichten werden nie genommen, auch
    wenn der Aufrufer sie liefert: der Altersschutz hängt nicht am Filter davor.
    """
    limit = cutoff(now, max_age_minutes)
    bulk: list[int] = []
    single: list[int] = []
    for message in messages:
        if message.created_at > limit:
            continue
        if message.id in protected or (keep_pinned and message.pinned):
            continue
        if now - message.created_at < BULK_LIMIT - BULK_MARGIN:
            if len(bulk) < max_bulk:
                bulk.append(message.id)
        elif len(single) < max_single:
            single.append(message.id)
    return Plan(tuple(bulk), tuple(single))
