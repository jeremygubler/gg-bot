"""Prüfungen für Panels, die der Bot bei Slash-Befehlen aus Discord-Objekten ableitet.

Die API hat keine Discord-Objekte, nur den Snapshot des Bots. Hier stehen dieselben Regeln als
reine Funktionen darüber (kein I/O), damit das Dashboard keine Panels anlegt, die der Bot später
nicht umsetzen könnte.
"""

from ggbot.core.errors import UserError
from ggbot.core.roles.plan import is_emoji
from ggbot.core.snapshot import ChannelInfo, GuildSnapshot, RoleInfo
from ggbot.core.tickets.rules import MAX_QUESTION_LENGTH, MAX_QUESTIONS

TEXT_TYPES = {"text", "news"}


def _role(snapshot: GuildSnapshot, role_id: int) -> RoleInfo:
    role = next((r for r in snapshot.roles if r.id == role_id), None)
    if role is None:
        raise UserError("error.dashboard_role_missing")
    return role


def _channel(snapshot: GuildSnapshot, channel_id: int) -> ChannelInfo:
    channel = next((c for c in snapshot.channels if c.id == channel_id), None)
    if channel is None:
        raise UserError("error.panel_channel_invalid")
    return channel


def check_grantable(snapshot: GuildSnapshot, role_id: int) -> None:
    """Darf der Bot die Rolle vergeben (nicht @everyone, nicht verwaltet, unter seiner höchsten)?"""
    role = _role(snapshot, role_id)
    if role_id == snapshot.guild_id or role.managed or role.position >= snapshot.bot_top_position:
        raise UserError("error.role_unassignable", role=role.name)


def check_support_role(snapshot: GuildSnapshot, role_id: int) -> None:
    """@everyone als Support-Rolle machte jedes Ticket für alle sichtbar."""
    _role(snapshot, role_id)
    if role_id == snapshot.guild_id:
        raise UserError("error.tcat_role_everyone")


def check_text_channel(snapshot: GuildSnapshot, channel_id: int) -> None:
    if _channel(snapshot, channel_id).type not in TEXT_TYPES:
        raise UserError("error.panel_channel_invalid")


def check_category_channel(snapshot: GuildSnapshot, channel_id: int) -> None:
    if _channel(snapshot, channel_id).type != "category":
        raise UserError("error.panel_channel_invalid")


def check_emoji(value: str | None) -> str | None:
    """Leer wird None; sonst muss es wie ein Emoji aussehen."""
    if value is None or not value.strip():
        return None
    if not is_emoji(value):
        raise UserError("error.panel_emoji_invalid", value=value.strip()[:20])
    return value.strip()


def clean_question_list(questions: list[str]) -> list[str]:
    """Wie `clean_questions` (Slash-Befehl), aber für eine Liste statt `;`-getrenntem Text."""
    cleaned = [q.strip() for q in questions if q.strip()]
    if any(";" in q for q in cleaned):
        raise UserError("error.tcat_questions", detail=";")
    if len(cleaned) > MAX_QUESTIONS:
        raise UserError("error.tcat_questions", detail=f"max. {MAX_QUESTIONS}")
    if any(len(q) > MAX_QUESTION_LENGTH for q in cleaned):
        raise UserError("error.tcat_questions", detail=f"max. {MAX_QUESTION_LENGTH}")
    return cleaned
