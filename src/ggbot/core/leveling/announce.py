"""Vorlagen für die Level-Up-Nachricht: nur bekannte Platzhalter, nie ein Absturz."""

import string

ALLOWED_FIELDS = ("user", "name", "level", "xp")
MAX_TEMPLATE = 300


def validate_template(template: str) -> None:
    """Wirft `ValueError`, wenn die Vorlage kaputt ist oder unbekannte Platzhalter enthält."""
    if len(template) > MAX_TEMPLATE:
        raise ValueError(f"Vorlage länger als {MAX_TEMPLATE} Zeichen")
    for _, field, spec, _conv in string.Formatter().parse(template):
        if field is None:
            continue
        if field not in ALLOWED_FIELDS:
            raise ValueError(f"unbekannter Platzhalter: {{{field}}}")
        if spec:
            raise ValueError("Formatangaben sind nicht erlaubt")


def render_template(template: str, *, user: str, name: str, level: int, xp: int) -> str:
    validate_template(template)
    return template.format(user=user, name=name, level=level, xp=xp)
