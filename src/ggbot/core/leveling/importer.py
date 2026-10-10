"""Import von Leveldaten aus einer Datei (MEE6-Export, JSON oder CSV)."""

import csv
import io
import json
from dataclasses import dataclass
from typing import Any

from ggbot.core.errors import UserError
from ggbot.core.leveling.xp import MAX_XP, total_xp_for_level

MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 100_000


@dataclass(frozen=True)
class ImportRow:
    user_id: int
    xp: int


@dataclass(frozen=True)
class ParsedImport:
    rows: list[ImportRow]
    skipped: int  # Zeilen ohne verwertbare ID/XP


def _to_int(value: Any) -> int | None:
    """Ganze Zahl aus Zahl oder Text ("123"). Bool, Gleitkomma mit Rest und Müll → None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def _row(record: dict[str, Any]) -> ImportRow | None:
    user_id = _to_int(record.get("id", record.get("user_id")))
    if user_id is None or user_id <= 0:
        return None
    xp = _to_int(record.get("xp"))
    if xp is None:
        level = _to_int(record.get("level"))
        if level is None or level < 0:
            return None
        xp = total_xp_for_level(min(level, 1000))
    if xp < 0:
        return None
    return ImportRow(user_id, min(xp, MAX_XP))


def _records(data: Any) -> list[dict[str, Any]]:
    """Findet die Nutzerliste: MEE6 liefert {"players": [...]}, ein Export kann eine Liste solcher
    Seiten sein, und einfache Dateien sind schlicht eine Liste von Einträgen."""
    if isinstance(data, dict):
        players = data.get("players")
        if isinstance(players, list):
            return [p for p in players if isinstance(p, dict)]
        raise UserError("error.import_unknown_format")
    if isinstance(data, list):
        records: list[dict[str, Any]] = []
        for item in data:
            if isinstance(item, dict) and isinstance(item.get("players"), list):
                records.extend(p for p in item["players"] if isinstance(p, dict))
            elif isinstance(item, dict):
                records.append(item)
        return records
    raise UserError("error.import_unknown_format")


def parse_import(raw: bytes) -> ParsedImport:
    """Liest JSON oder CSV. Wirft `UserError`, wenn die Datei unbrauchbar ist.

    Doppelte Nutzer werden zusammengefasst (höchster XP-Wert gewinnt).
    """
    if len(raw) > MAX_BYTES:
        raise UserError("error.import_too_large", limit=MAX_BYTES // (1024 * 1024))
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise UserError("error.import_unknown_format") from exc
    stripped = text.lstrip()
    if not stripped:
        raise UserError("error.import_empty")

    if stripped[0] in "[{":
        try:
            records = _records(json.loads(stripped))
        except json.JSONDecodeError as exc:
            raise UserError("error.import_unknown_format") from exc
    else:
        dialect: type[csv.Dialect] | csv.Dialect
        try:
            dialect = csv.Sniffer().sniff(stripped.splitlines()[0], delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel()
        reader = csv.DictReader(io.StringIO(stripped), dialect=dialect)
        records = [
            {(key or "").strip().lower(): value for key, value in line.items()} for line in reader
        ]

    if len(records) > MAX_ROWS:
        raise UserError("error.import_too_many", limit=MAX_ROWS)
    best: dict[int, int] = {}
    skipped = 0
    for record in records:
        parsed = _row({str(k).lower(): v for k, v in record.items()})
        if parsed is None:
            skipped += 1
        else:
            best[parsed.user_id] = max(best.get(parsed.user_id, 0), parsed.xp)
    if not best:
        raise UserError("error.import_empty")
    return ParsedImport([ImportRow(uid, xp) for uid, xp in best.items()], skipped)
