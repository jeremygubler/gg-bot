"""Stat-Kanäle: welche Werte es gibt und wie der Kanalname daraus wird. Rein."""

from dataclasses import dataclass

from ggbot.core import messages

KINDS = ("members", "humans", "bots", "channels", "roles", "boosts")
# Diese Werte kennt Discord ohne Mitgliederliste nicht verlässlich.
NEEDS_MEMBERS = frozenset({"humans", "bots"})
MAX_NAME = 100  # Länge eines Kanalnamens
FIELDS = ("count",)


@dataclass(frozen=True)
class Snapshot:
    member_count: int
    bots: int | None  # None: unbekannt (kein Members-Intent)
    channels: int
    roles: int
    boosts: int


def value(kind: str, snap: Snapshot) -> int | None:
    """Der Wert, oder None, wenn er sich nicht verlässlich bestimmen lässt."""
    if kind == "members":
        return snap.member_count
    if kind == "bots":
        return snap.bots
    if kind == "humans":
        return None if snap.bots is None else max(snap.member_count - snap.bots, 0)
    if kind == "channels":
        return snap.channels
    if kind == "roles":
        return snap.roles
    if kind == "boosts":
        return snap.boosts
    return None


def validate_template(template: str) -> None:
    """Eine Vorlage braucht `{count}` und darf nichts anderes enthalten."""
    messages.validate_template(template, FIELDS)
    if "{count}" not in template:
        raise ValueError("die Vorlage braucht {count}")
    if len(render_name(template, 999_999_999)) > MAX_NAME:
        raise ValueError(f"der Name wäre länger als {MAX_NAME} Zeichen")


def render_name(template: str, count: int) -> str:
    return messages.render_template(template, FIELDS, count=f"{count:,}".replace(",", "."))
