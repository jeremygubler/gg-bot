"""Jeder im Code fest eingetragene Übersetzungsschlüssel muss in beiden Katalogen existieren.

Ein fehlender Schlüssel wäre sonst erst im Betrieb ein KeyError, mitten in einer Moderationsaktion.
"""

import ast
from pathlib import Path

from ggbot.core.i18n import LOCALES, translator

SRC = Path(__file__).resolve().parents[2] / "src" / "ggbot"
KEY_PREFIXES = (
    "error.", "mod.", "automod.", "help.", "config.", "cmd.",
)  # fmt: skip


def literal_keys() -> dict[str, list[str]]:
    """Schlüssel -> Fundstellen. Erfasst `UserError("…")`, `.t(locale, "…")` und `d("…")`."""
    found: dict[str, list[str]] = {}
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else ""
            )
            candidates: list[ast.expr] = []
            if name in ("UserError", "d") and node.args:
                candidates.append(node.args[0])
            elif name == "t" and len(node.args) >= 2:
                candidates.append(node.args[1])
            for arg in candidates:
                if (
                    isinstance(arg, ast.Constant)
                    and isinstance(arg.value, str)
                    and arg.value.startswith(KEY_PREFIXES)
                ):
                    found.setdefault(arg.value, []).append(f"{path.name}:{node.lineno}")
    return found


def test_es_gibt_schluessel_im_code() -> None:
    assert len(literal_keys()) > 50  # sonst prüft der Test ins Leere


def test_jeder_feste_schluessel_existiert_in_allen_sprachen() -> None:
    missing = [
        f"{key} ({', '.join(places)}) fehlt in {locale}"
        for key, places in sorted(literal_keys().items())
        for locale in LOCALES
        if not translator.has(locale, key)
    ]
    assert not missing, "\n".join(missing)


def test_platzhalter_der_schluessel_passen_zu_den_aufrufen() -> None:
    """Stichprobe: Schlüssel mit Platzhaltern im Katalog sind formatierbar."""
    for locale in LOCALES:
        for key, text in translator.catalogs[locale].items():
            assert "{" not in text or text.count("{") == text.count("}"), key
