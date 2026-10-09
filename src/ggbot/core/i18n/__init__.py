"""Übersetzungen (DE/EN) aus TOML-Katalogen."""

import tomllib
from pathlib import Path
from typing import Any

LOCALES = ("de", "en")
FALLBACK = "en"
_DIR = Path(__file__).parent


def _flatten(prefix: str, data: dict[str, Any], out: dict[str, str]) -> None:
    for name, value in data.items():
        full = f"{prefix}.{name}" if prefix else name
        if isinstance(value, dict):
            _flatten(full, value, out)
        else:
            out[full] = str(value)


def _load(locale: str) -> dict[str, str]:
    with (_DIR / f"{locale}.toml").open("rb") as handle:
        raw = tomllib.load(handle)
    flat: dict[str, str] = {}
    _flatten("", raw, flat)
    return flat


class Translator:
    def __init__(self) -> None:
        self.catalogs: dict[str, dict[str, str]] = {loc: _load(loc) for loc in LOCALES}

    def has(self, locale: str, key: str) -> bool:
        return key in self.catalogs.get(locale, {})

    def t(self, locale: str, key: str, **params: Any) -> str:
        """Übersetzt `key`. Fehlt er in `locale`, greift Englisch; fehlt er überall,
        ist das ein Programmierfehler und wird laut."""
        text = self.catalogs.get(locale, {}).get(key)
        if text is None:
            text = self.catalogs[FALLBACK].get(key)
        if text is None:
            raise KeyError(f"i18n-Schlüssel fehlt: {key}")
        return text.format(**params) if params else text


translator = Translator()
