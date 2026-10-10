"""Antwortvorlagen eigener Befehle: Text mit Platzhaltern, ohne Logik.

Bewusst keine Bedingungen und keine Schleifen: eine Vorlage ist eine Liste aus Text und festen
Platzhaltern. Es gibt nichts auszuwerten, also nichts auszubrechen. Zufall kommt als Funktion
herein (Regel: reine Module bestimmen weder Zeit noch Zufall selbst).
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

MAX_TEMPLATE = 1500
MAX_OUTPUT = 2000  # Nachrichtenlimit von Discord
MAX_RANDOM_OPTIONS = 20
MAX_OPTION_LENGTH = 300
FIELDS = ("user", "name", "server", "channel", "args", "members", "uses")


@dataclass(frozen=True)
class Text:
    value: str


@dataclass(frozen=True)
class Field:
    name: str


@dataclass(frozen=True)
class Random:
    options: tuple[str, ...]


Token = Text | Field | Random


def parse(template: str) -> list[Token]:
    """Zerlegt eine Vorlage. Wirft `ValueError` mit einer lesbaren Begründung.

    `{{` und `}}` sind die geschweiften Klammern selbst. Verschachtelte Platzhalter gibt es nicht.
    """
    if len(template) > MAX_TEMPLATE:
        raise ValueError(f"Vorlage länger als {MAX_TEMPLATE} Zeichen")
    tokens: list[Token] = []
    buffer: list[str] = []
    i = 0
    while i < len(template):
        char = template[i]
        if char == "{":
            if template[i + 1 : i + 2] == "{":
                buffer.append("{")
                i += 2
                continue
            end = template.find("}", i + 1)
            if end == -1:
                raise ValueError("Klammer { ohne schliessendes }")
            body = template[i + 1 : end]
            if "{" in body:
                raise ValueError("Platzhalter lassen sich nicht verschachteln")
            if buffer:
                tokens.append(Text("".join(buffer)))
                buffer = []
            tokens.append(_placeholder(body))
            i = end + 1
        elif char == "}":
            if template[i + 1 : i + 2] != "}":
                raise ValueError("Klammer } ohne öffnendes {")
            buffer.append("}")
            i += 2
        else:
            buffer.append(char)
            i += 1
    if buffer:
        tokens.append(Text("".join(buffer)))
    return tokens


def _placeholder(body: str) -> Token:
    if body.startswith("random:"):
        options = tuple(option.strip() for option in body[len("random:") :].split("|"))
        if len(options) > MAX_RANDOM_OPTIONS:
            raise ValueError(f"random: höchstens {MAX_RANDOM_OPTIONS} Möglichkeiten")
        if any(not option for option in options):
            raise ValueError("random: leere Möglichkeit")
        if any(len(option) > MAX_OPTION_LENGTH for option in options):
            raise ValueError(f"random: Möglichkeit länger als {MAX_OPTION_LENGTH} Zeichen")
        return Random(options)
    if body not in FIELDS:
        raise ValueError(f"unbekannter Platzhalter: {{{body}}}")
    return Field(body)


def validate(template: str) -> None:
    parse(template)


def render(
    template: str,
    values: Mapping[str, object],
    pick: Callable[[int], int],
) -> str:
    """Setzt die Werte ein. `pick(n)` liefert einen Index in `range(n)`."""
    parts: list[str] = []
    for token in parse(template):
        if isinstance(token, Text):
            parts.append(token.value)
        elif isinstance(token, Field):
            parts.append(str(values.get(token.name, "")))
        else:
            parts.append(token.options[pick(len(token.options)) % len(token.options)])
    return "".join(parts)[:MAX_OUTPUT]
