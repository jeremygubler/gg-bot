"""Vorlagen für die Level-Up-Nachricht."""

from ggbot.core import messages

ALLOWED_FIELDS = ("user", "name", "level", "xp")
MAX_TEMPLATE = messages.MAX_TEMPLATE


def validate_template(template: str) -> None:
    messages.validate_template(template, ALLOWED_FIELDS)


def render_template(template: str, *, user: str, name: str, level: int, xp: int) -> str:
    return messages.render_template(
        template, ALLOWED_FIELDS, user=user, name=name, level=level, xp=xp
    )
