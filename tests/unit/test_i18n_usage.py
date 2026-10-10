"""Jeder im Code verwendete Übersetzungsschlüssel muss in beiden Katalogen existieren.

Ein fehlender Schlüssel wäre sonst erst im Betrieb ein KeyError, mitten in einer Moderationsaktion.
"""

import ast
import re
from pathlib import Path

import pytest

from ggbot.core.i18n import LOCALES, translator
from ggbot.core.settings.eventlog import EVENT_TYPES

SRC = Path(__file__).resolve().parents[2] / "src" / "ggbot"
# Jeder Schlüsselraum des Katalogs: ein neuer muss hier stehen, sonst wird er nicht geprüft.
KEY_PREFIXES = (
    "error.",
    "mod.",
    "automod.",
    "logging.",
    "leveling.",
    "help.",
    "config.",
    "cmd.",
    "roles.",
    "welcome.",
    "ticket.",
)
KEY_PATTERN = re.compile(r"^[a-z_]+(\.[a-z_0-9]+)+$")
# Sieht aus wie ein Schlüssel, ist aber keiner (z. B. der Name eines Scheduler-Jobs).
NOT_KEYS = frozenset({"mod.unban", "welcome.png", "ticket.delete_channel"})

# Zusammengesetzte Schlüssel (f"…{wert}"): hier stehen alle Werte, die vorkommen können.
DYNAMIC_FAMILIES: dict[str, list[str]] = {
    "mod.dm.{}": ["warn", "timeout", "kick", "ban", "tempban", "softban"],
    "mod.done.{}": [
        "warn", "unwarn", "timeout", "untimeout", "kick", "ban", "tempban", "softban", "unban",
    ],
    "mod.type.{}": ["warn", "timeout", "untimeout", "kick", "ban", "tempban", "softban", "unban"],
    "automod.rule.{}": [
        "spam", "duplicates", "caps", "invites", "links", "mentions", "words", "raid", "accountage",
    ],
    "automod.action.{}": ["delete", "warn", "timeout", "kick", "ban"],
    "automod.join_reason.{}": ["raid", "accountage"],
    "automod.link_mode_{}": ["blacklist", "whitelist"],
    "logging.title.{}": [*EVENT_TYPES, "member_kicked"],
    "logging.status.need_{}": ["message_content", "members"],
    "error.logging_needs_{}": ["message_content", "members"],
    "leveling.announce_{}": ["off", "current", "channel", "dm"],
    "leveling.reward_mode_{}": ["stack", "replace"],
    "roles.mode_hint_{}": ["normal", "unique", "verify"],
}  # fmt: skip


def literal_keys() -> dict[str, list[str]]:
    """Schlüssel -> Fundstellen: jede Zeichenkette im Code, die wie ein Schlüssel aussieht.

    Teile von f-Strings zählen nicht (dafür gibt es DYNAMIC_FAMILIES).
    """
    found: dict[str, list[str]] = {}
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        in_fstring = {
            id(part)
            for node in ast.walk(tree)
            if isinstance(node, ast.JoinedStr)
            for part in ast.walk(node)
            if isinstance(part, ast.Constant)
        }
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in in_fstring
                and node.value.startswith(KEY_PREFIXES)
                and KEY_PATTERN.match(node.value)
                and node.value not in NOT_KEYS
            ):
                found.setdefault(node.value, []).append(f"{path.name}:{node.lineno}")
    return found


def test_es_gibt_schluessel_im_code() -> None:
    assert len(literal_keys()) > 200  # sonst prüft der Test ins Leere


def test_jeder_feste_schluessel_existiert_in_allen_sprachen() -> None:
    missing = [
        f"{key} ({', '.join(places)}) fehlt in {locale}"
        for key, places in sorted(literal_keys().items())
        for locale in LOCALES
        if not translator.has(locale, key)
    ]
    assert not missing, "\n".join(missing)


@pytest.mark.parametrize("locale", LOCALES)
def test_jeder_zusammengesetzte_schluessel_existiert(locale: str) -> None:
    missing = [
        template.format(value)
        for template, values in DYNAMIC_FAMILIES.items()
        for value in values
        if not translator.has(locale, template.format(value))
    ]
    assert not missing, missing


def test_zusammengesetzte_schluessel_im_code_sind_alle_erfasst() -> None:
    """Jeder f-String mit Schlüsselpräfix muss in DYNAMIC_FAMILIES stehen."""
    templates: set[str] = set()
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.JoinedStr):
                continue
            first = node.values[0] if node.values else None
            if not (
                isinstance(first, ast.Constant)
                and isinstance(first.value, str)
                and first.value.startswith(KEY_PREFIXES)
            ):
                continue
            parts = [v.value if isinstance(v, ast.Constant) else "{}" for v in node.values]
            templates.add("".join(str(p) for p in parts))
    unknown = templates - set(DYNAMIC_FAMILIES)
    assert not unknown, f"Nicht in DYNAMIC_FAMILIES: {sorted(unknown)}"


def test_alle_oberen_schluesselraeume_des_katalogs_werden_geprueft() -> None:
    roots = {key.split(".")[0] + "." for key in translator.catalogs["en"]}
    assert roots <= set(KEY_PREFIXES), roots - set(KEY_PREFIXES)
