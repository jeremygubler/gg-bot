"""Regeln der temporären Sprachkanäle: Namen. Ohne Discord."""

import unicodedata

from ggbot.core.messages import render_template
from ggbot.core.settings.tempvoice import FIELDS

MAX_NAME = 100  # Grenze von Discord
FALLBACK_NAME = "Voice"


def clean_name(name: str) -> str:
    """Ein Kanalname ohne Steuerzeichen und Zeilenumbrüche, auf die Länge von Discord gekürzt.

    Der Anzeigename kommt vom Nutzer und kann beliebig sein; Discord lehnt manches ab und anderes
    sähe im Kanalbaum kaputt aus.
    """
    kept = "".join(
        " " if ch.isspace() else ch
        for ch in name
        if unicodedata.category(ch)[0] != "C" or ch.isspace()
    )
    collapsed = " ".join(kept.split())
    return collapsed[:MAX_NAME].strip()


def channel_name(template: str, *, user: str, number: int) -> str:
    """Name nach Vorlage; nie leer (dann `FALLBACK_NAME`) und nie länger als `MAX_NAME`."""
    rendered = render_template(template, FIELDS, user=clean_name(user), number=number)
    return clean_name(rendered) or FALLBACK_NAME
