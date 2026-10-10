"""Was bei Klick, Auswahl oder Reaktion mit den Rollen eines Mitglieds passiert."""

import re
import unicodedata
from collections.abc import Collection
from dataclasses import dataclass
from typing import Literal

PanelMode = Literal["normal", "unique", "verify"]


@dataclass(frozen=True)
class Change:
    add: frozenset[int] = frozenset()
    remove: frozenset[int] = frozenset()

    @property
    def empty(self) -> bool:
        return not (self.add or self.remove)


def plan_toggle(
    mode: PanelMode, clicked: int, panel_roles: Collection[int], current: Collection[int]
) -> Change:
    """Ein Klick auf einen Button (oder eine einzelne Reaktion).

    normal: umschalten. unique: genau eine Rolle des Panels; ein zweiter Klick auf dieselbe nimmt
    sie wieder weg. verify: nur hinzufügen, nie entfernen (wer verifiziert ist, bleibt es).
    """
    have = set(current)
    if mode == "verify":
        return Change() if clicked in have else Change(add=frozenset({clicked}))
    if clicked in have:
        return Change(remove=frozenset({clicked}))
    if mode == "unique":
        others = (set(panel_roles) - {clicked}) & have
        return Change(add=frozenset({clicked}), remove=frozenset(others))
    return Change(add=frozenset({clicked}))


def plan_select(
    mode: PanelMode,
    selected: Collection[int],
    panel_roles: Collection[int],
    current: Collection[int],
) -> Change:
    """Eine Auswahl im Menü: `selected` ist der gewünschte Stand der Panel-Rollen.

    Rollen ausserhalb des Panels fasst der Plan nie an. Bei verify wird nur hinzugefügt.
    """
    have = set(current) & set(panel_roles)
    wanted = set(selected) & set(panel_roles)
    if mode == "unique" and len(wanted) > 1:
        raise ValueError("unique erlaubt höchstens eine Rolle")
    add = wanted - have
    remove = set() if mode == "verify" else have - wanted
    return Change(add=frozenset(add), remove=frozenset(remove))


def plan_reaction_added(
    mode: PanelMode, role: int, panel_roles: Collection[int], current: Collection[int]
) -> Change:
    """Eine Reaktion wurde gesetzt: die Rolle kommt dazu, bei unique gehen die anderen weg."""
    have = set(current)
    add = frozenset() if role in have else frozenset({role})
    if mode == "unique":
        return Change(add=add, remove=frozenset((set(panel_roles) - {role}) & have))
    return Change(add=add)


def plan_reaction_removed(mode: PanelMode, role: int, current: Collection[int]) -> Change:
    """Eine Reaktion wurde weggenommen. Bei verify bleibt die Rolle."""
    if mode == "verify" or role not in set(current):
        return Change()
    return Change(remove=frozenset({role}))


# --- Emojis ---

_CUSTOM = re.compile(r"^<a?:\w{2,32}:(\d{15,25})>$")
_VARIATION_SELECTORS = "︎️"


def emoji_key(emoji: str) -> str:
    """Vergleichsform: Eigene Emojis als "c:<id>" (Name und Animation sind egal), Unicode ohne
    Variationsselektor ("✔" und "✔️" sind dasselbe)."""
    text = emoji.strip()
    match = _CUSTOM.match(text)
    if match:
        return f"c:{match.group(1)}"
    return text.translate({ord(ch): None for ch in _VARIATION_SELECTORS})


_KEYCAP = re.compile(r"^[0-9#*]️?⃣$")
_EMOJI_CATEGORIES = frozenset({"So", "Sk", "Mn", "Me", "Cf"})
MAX_EMOJI_CODEPOINTS = 16  # längste übliche Folge: Familie mit Hautton und Zero-Width-Joinern


def is_emoji(text: str) -> bool:
    """Ist das ein einzelnes Emoji (eigenes oder Unicode)? Ohne Emoji-Tabelle, darum nur eine
    Näherung: sie weist Text, Zahlen und Satzzeichen sicher ab; das letzte Wort hat Discord beim
    Setzen der Reaktion."""
    text = text.strip()
    if _CUSTOM.match(text) or _KEYCAP.match(text):
        return True
    if not text or len(text) > MAX_EMOJI_CODEPOINTS:
        return False
    return all(unicodedata.category(ch) in _EMOJI_CATEGORIES for ch in text)
