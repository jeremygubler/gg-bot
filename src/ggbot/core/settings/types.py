"""Typen für Discord-IDs in Einstellungen.

Snowflakes sind 64-Bit-Zahlen; JavaScript rechnet mit 53 Bit Mantisse und würde sie
beim Parsen von JSON stillschweigend verfälschen. Im JSON stehen sie deshalb als
String, im Python-Objekt bleiben es `int`. Gelesen werden beide Formen, damit
bereits gespeicherte Stände (Zahlen) gültig bleiben.

Die `x-picker`-Hinweise im JSON-Schema sagen dem Dashboard, welche Auswahl es
anbieten soll; sie ändern an der Validierung nichts.
"""

import re
from typing import Annotated, Any

from pydantic import BeforeValidator, PlainSerializer, WithJsonSchema

_DIGITS = re.compile(r"\A[0-9]{1,20}\Z")


MAX_SNOWFLAKE = 2**63 - 1  # Spaltentyp BIGINT


def _from_str(value: Any) -> Any:
    """Strikt: sonst liesse Pydantics Zahlenparser auch " 1", "1\\n" oder "-1" durch."""
    if isinstance(value, str):
        if not _DIGITS.match(value):
            raise ValueError("Discord-ID muss aus 1 bis 20 Ziffern bestehen")
        value = int(value)
    if isinstance(value, int) and not isinstance(value, bool) and not 0 <= value <= MAX_SNOWFLAKE:
        raise ValueError("Discord-ID ausserhalb des gueltigen Bereichs")
    return value


def _schema(kind: str | None) -> WithJsonSchema:
    schema: dict[str, Any] = {"type": "string", "pattern": r"^[0-9]{1,20}$"}
    if kind is not None:
        # Steht im JSON-Schema selbst, weil WithJsonSchema das Schema ersetzt und
        # Zusätze aus Field(json_schema_extra=…) dabei verloren gingen.
        schema["x-picker"] = kind
    return WithJsonSchema(schema)


# Ausgeschrieben statt per Fabrik, damit mypy `int` sieht und nicht `Any`.
Snowflake = Annotated[
    int,
    BeforeValidator(_from_str),
    PlainSerializer(str, return_type=str, when_used="json"),
    _schema(None),
]
ChannelId = Annotated[
    int,
    BeforeValidator(_from_str),
    PlainSerializer(str, return_type=str, when_used="json"),
    _schema("channel"),
]
RoleId = Annotated[
    int,
    BeforeValidator(_from_str),
    PlainSerializer(str, return_type=str, when_used="json"),
    _schema("role"),
]
