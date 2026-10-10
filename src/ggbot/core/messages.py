"""Textvorlagen mit festen Platzhaltern: nie ein Absturz, nie ein Zugriff auf Attribute."""

import string
from collections.abc import Collection

MAX_TEMPLATE = 300


def validate_template(template: str, allowed: Collection[str]) -> None:
    """Wirft `ValueError` bei kaputter Vorlage, unbekannten Platzhaltern oder Formatangaben.

    `{user.__class__}` und `{0}` zählen als unbekannt: so kommt über eine Vorlage nie ein
    Attributzugriff auf Objekte zustande.
    """
    if len(template) > MAX_TEMPLATE:
        raise ValueError(f"Vorlage länger als {MAX_TEMPLATE} Zeichen")
    for _, field, spec, conv in string.Formatter().parse(template):
        if field is None:
            continue
        if field not in allowed:
            raise ValueError(f"unbekannter Platzhalter: {{{field}}}")
        if spec or conv:
            raise ValueError("Formatangaben und Konvertierungen sind nicht erlaubt")


def render_template(template: str, allowed: Collection[str], **values: object) -> str:
    validate_template(template, allowed)
    return template.format(**values)
