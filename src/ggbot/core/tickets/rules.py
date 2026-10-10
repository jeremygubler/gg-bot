"""Regeln rund um Tickets als reine Funktionen: wer darf was, wann ist ein Ticket verwaist."""

import re
from collections.abc import Collection
from datetime import UTC, datetime, timedelta

MAX_QUESTIONS = 5  # Grenze von Discord-Formularen
MAX_QUESTION_LENGTH = 45  # Länge der Beschriftung eines Formularfelds
MAX_ANSWER_LENGTH = 1000
DISCORD_EPOCH_MS = 1_420_070_400_000


def can_manage(
    member_role_ids: Collection[int], support_role_ids: Collection[int], *, is_manager: bool
) -> bool:
    """Support-Mitglied: eine Support-Rolle der Kategorie oder Server-Verwalter."""
    return is_manager or bool(set(member_role_ids) & set(support_role_ids))


def can_close(
    user_id: int,
    creator_id: int,
    member_role_ids: Collection[int],
    support_role_ids: Collection[int],
    *,
    is_manager: bool,
    creators_may_close: bool,
) -> bool:
    if user_id == creator_id:
        return creators_may_close or can_manage(
            member_role_ids, support_role_ids, is_manager=is_manager
        )
    return can_manage(member_role_ids, support_role_ids, is_manager=is_manager)


def snowflake_time(snowflake: int) -> datetime:
    return datetime.fromtimestamp(((snowflake >> 22) + DISCORD_EPOCH_MS) / 1000, tz=UTC)


def is_inactive(
    now: datetime, created_at: datetime, last_message_at: datetime | None, hours: int
) -> bool:
    """Seit `hours` Stunden keine Nachricht (bzw. seit der Erstellung, wenn nie jemand schrieb).
    `hours == 0` schaltet das Schliessen aus."""
    if hours <= 0:
        return False
    last = max(created_at, last_message_at) if last_message_at else created_at
    return now - last >= timedelta(hours=hours)


_UNSAFE = re.compile(r"[^a-z0-9\-]+")


def channel_name(template: str, *, number: int, category: str, user: str) -> str:
    """Kanalname aus der Vorlage; Discord erlaubt Kleinbuchstaben, Ziffern und Bindestriche.

    Immer mit der Nummer, auch wenn die Vorlage sie vergisst: so sind Namen eindeutig und das
    Ticket aus dem Kanalnamen erkennbar.
    """
    raw = template.replace("{number}", f"{number:04d}").replace("{category}", category)
    raw = raw.replace("{user}", user).lower().replace(" ", "-")
    name = _UNSAFE.sub("", raw).strip("-")
    name = re.sub(r"-{2,}", "-", name)
    suffix = f"{number:04d}"
    if suffix not in name:
        name = f"{name}-{suffix}" if name else f"ticket-{suffix}"
    if len(name) > 100:
        name = name[: 100 - len(suffix) - 1].rstrip("-") + "-" + suffix
    return name


def clean_questions(raw: str) -> list[str]:
    """Fragen aus einem Eingabefeld (getrennt durch `;`): bereinigt, höchstens 5, je 45 Zeichen."""
    questions = [q.strip() for q in raw.split(";")]
    questions = [q for q in questions if q]
    if len(questions) > MAX_QUESTIONS:
        raise ValueError(f"höchstens {MAX_QUESTIONS} Fragen")
    if any(len(q) > MAX_QUESTION_LENGTH for q in questions):
        raise ValueError(f"jede Frage höchstens {MAX_QUESTION_LENGTH} Zeichen")
    return questions
