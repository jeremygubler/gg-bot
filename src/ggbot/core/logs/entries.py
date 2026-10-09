"""Log-Einträge als reine Daten; der Discord-Layer füllt die Schnappschüsse."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from ggbot.core.i18n import translator

FIELD_LIMIT = 1000  # Discord erlaubt 1024 Zeichen pro Feld
RED = 0xED4245
ORANGE = 0xFAA61A
GREEN = 0x57F287
BLUE = 0x5865F2
GREY = 0x99AAB5


@dataclass(frozen=True)
class UserRef:
    id: int
    name: str
    bot: bool = False


@dataclass(frozen=True)
class MessageSnapshot:
    id: int
    channel_id: int
    author: UserRef | None
    content: str
    attachments: tuple[str, ...] = ()
    created_at: datetime | None = None


@dataclass(frozen=True)
class Executor:
    """Wer es getan hat, laut Audit-Log. Fehlt das Recht, gibt es keinen."""

    user_id: int | None
    reason: str | None = None


@dataclass(frozen=True)
class Change:
    label: str  # i18n-Schlüssel des Feldnamens
    before: str
    after: str


@dataclass(frozen=True)
class LogField:
    name: str
    value: str
    inline: bool = True


@dataclass(frozen=True)
class LogFile:
    filename: str
    content: bytes


@dataclass(frozen=True)
class LogEntry:
    event: str
    title: str
    color: int
    at: datetime
    description: str = ""
    fields: tuple[LogField, ...] = ()
    file: LogFile | None = None


def clip(text: str, limit: int = FIELD_LIMIT) -> str:
    """Kürzt auf `limit` Zeichen; Backticks werden entschärft, damit Code-Blöcke heil bleiben."""
    text = text.replace("```", "'''")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _t(locale: str, key: str, **params: object) -> str:
    return translator.t(locale, key, **params)


def _user(user: UserRef | None, locale: str) -> str:
    if user is None:
        return _t(locale, "logging.value.unknown")
    return f"<@{user.id}>\n`{user.name}` · `{user.id}`"


def _block(text: str, locale: str) -> str:
    return f"```{clip(text)}```" if text else _t(locale, "logging.value.empty")


def _executor_fields(locale: str, executor: Executor | None) -> list[LogField]:
    if executor is None or executor.user_id is None:
        return []
    fields = [LogField(_t(locale, "logging.field.executor"), f"<@{executor.user_id}>")]
    if executor.reason:
        fields.append(
            LogField(_t(locale, "logging.field.reason"), clip(executor.reason, 300), False)
        )
    return fields


def _ts(moment: datetime, style: str = "F") -> str:
    return f"<t:{int(moment.timestamp())}:{style}>"


# --- Nachrichten ---


def message_deleted(
    locale: str, channel_id: int, message_id: int, snapshot: MessageSnapshot | None, at: datetime
) -> LogEntry:
    fields = [
        LogField(_t(locale, "logging.field.channel"), f"<#{channel_id}>"),
        LogField(_t(locale, "logging.field.message_id"), f"`{message_id}`"),
    ]
    if snapshot is None:
        # Nach Neustart oder bei zu alten Nachrichten kennt der Bot den Inhalt nicht mehr.
        description = _t(locale, "logging.message.not_cached")
        return LogEntry(
            "message_delete",
            _t(locale, "logging.title.message_delete"),
            RED,
            at,
            description,
            tuple(fields),
        )
    fields.insert(0, LogField(_t(locale, "logging.field.author"), _user(snapshot.author, locale)))
    fields.append(
        LogField(_t(locale, "logging.field.content"), _block(snapshot.content, locale), False)
    )
    if snapshot.attachments:
        links = "\n".join(snapshot.attachments[:10])
        fields.append(LogField(_t(locale, "logging.field.attachments"), clip(links), False))
    return LogEntry(
        "message_delete", _t(locale, "logging.title.message_delete"), RED, at, "", tuple(fields)
    )


def message_edited(
    locale: str,
    jump_url: str,
    author: UserRef | None,
    channel_id: int,
    before: str | None,
    after: str,
    at: datetime,
) -> LogEntry:
    not_cached = _t(locale, "logging.message.before_unknown")
    fields = [
        LogField(_t(locale, "logging.field.author"), _user(author, locale)),
        LogField(_t(locale, "logging.field.channel"), f"<#{channel_id}>"),
        LogField(
            _t(locale, "logging.field.before"),
            not_cached if before is None else _block(before, locale),
            False,
        ),
        LogField(_t(locale, "logging.field.after"), _block(after, locale), False),
        LogField(
            _t(locale, "logging.field.link"), f"[{_t(locale, 'logging.value.jump')}]({jump_url})"
        ),
    ]
    return LogEntry(
        "message_edit", _t(locale, "logging.title.message_edit"), ORANGE, at, "", tuple(fields)
    )


def render_bulk_file(
    locale: str, channel_id: int, ids: list[int], cached: list[MessageSnapshot]
) -> str:
    """Textdatei mit allen bekannten Nachrichten, älteste zuerst. Unbekannte stehen nur als ID."""
    known = {m.id: m for m in cached}
    lines = [
        _t(locale, "logging.bulk.header", channel=channel_id, count=len(ids), cached=len(known)),
        "",
    ]
    for message_id in sorted(ids):
        message = known.get(message_id)
        if message is None:
            lines.append(f"[{message_id}] {_t(locale, 'logging.message.not_cached')}")
            continue
        when = message.created_at.strftime("%Y-%m-%d %H:%M:%S UTC") if message.created_at else "?"
        who = f"{message.author.name} ({message.author.id})" if message.author else "?"
        body = message.content.replace("\r", "").replace("\n", "\n    ")
        lines.append(f"[{when}] {who}: {body}")
        lines.extend(f"    + {url}" for url in message.attachments)
    return "\n".join(lines) + "\n"


def messages_bulk_deleted(
    locale: str, channel_id: int, ids: list[int], cached: list[MessageSnapshot], at: datetime
) -> LogEntry:
    text = render_bulk_file(locale, channel_id, ids, cached)
    filename = f"deleted-{channel_id}-{int(at.timestamp())}.txt"
    fields = (
        LogField(_t(locale, "logging.field.channel"), f"<#{channel_id}>"),
        LogField(_t(locale, "logging.field.count"), str(len(ids))),
        LogField(_t(locale, "logging.field.known"), f"{len(cached)}/{len(ids)}"),
    )
    return LogEntry(
        "message_bulk",
        _t(locale, "logging.title.message_bulk"),
        RED,
        at,
        _t(locale, "logging.bulk.see_file"),
        fields,
        LogFile(filename, text.encode("utf-8")),
    )


# --- Mitglieder ---


def member_joined(
    locale: str, user: UserRef, account_created: datetime, member_count: int | None, at: datetime
) -> LogEntry:
    fields = [
        LogField(_t(locale, "logging.field.user"), _user(user, locale)),
        LogField(_t(locale, "logging.field.account_created"), _ts(account_created, "R")),
    ]
    if member_count is not None:
        fields.append(LogField(_t(locale, "logging.field.member_count"), str(member_count)))
    return LogEntry(
        "member_join", _t(locale, "logging.title.member_join"), GREEN, at, "", tuple(fields)
    )


def member_left(
    locale: str,
    user: UserRef,
    joined_at: datetime | None,
    role_names: tuple[str, ...],
    kicked_by: Executor | None,
    at: datetime,
) -> LogEntry:
    kicked = kicked_by is not None
    fields = [LogField(_t(locale, "logging.field.user"), _user(user, locale))]
    if joined_at is not None:
        fields.append(LogField(_t(locale, "logging.field.joined"), _ts(joined_at, "R")))
    if role_names:
        fields.append(
            LogField(_t(locale, "logging.field.roles"), clip(", ".join(role_names), 500), False)
        )
    fields.extend(_executor_fields(locale, kicked_by))
    title = "logging.title.member_kicked" if kicked else "logging.title.member_leave"
    return LogEntry("member_leave", _t(locale, title), RED, at, "", tuple(fields))


def member_updated(
    locale: str,
    user: UserRef,
    roles_added: tuple[str, ...],
    roles_removed: tuple[str, ...],
    nick: tuple[str | None, str | None],
    timeout: tuple[datetime | None, datetime | None],
    executor: Executor | None,
    at: datetime,
) -> LogEntry | None:
    """None, wenn nichts Protokollwürdiges passiert ist (Profilbild, Beitritts-Prüfung …)."""
    fields = [LogField(_t(locale, "logging.field.user"), _user(user, locale))]
    none = _t(locale, "logging.value.none")
    changed = False
    if roles_added:
        fields.append(
            LogField(_t(locale, "logging.field.roles_added"), clip(", ".join(roles_added), 500))
        )
        changed = True
    if roles_removed:
        fields.append(
            LogField(_t(locale, "logging.field.roles_removed"), clip(", ".join(roles_removed), 500))
        )
        changed = True
    if nick[0] != nick[1]:
        fields.append(
            LogField(
                _t(locale, "logging.field.nickname"),
                f"{clip(nick[0] or none, 100)} → {clip(nick[1] or none, 100)}",
            )
        )
        changed = True
    if timeout[0] != timeout[1]:
        after = timeout[1]
        value = _ts(after) if after is not None else _t(locale, "logging.value.timeout_ended")
        fields.append(LogField(_t(locale, "logging.field.timeout"), value))
        changed = True
    if not changed:
        return None
    fields.extend(_executor_fields(locale, executor))
    return LogEntry(
        "member_update", _t(locale, "logging.title.member_update"), BLUE, at, "", tuple(fields)
    )


def member_banned(locale: str, user: UserRef, executor: Executor | None, at: datetime) -> LogEntry:
    fields = [
        LogField(_t(locale, "logging.field.user"), _user(user, locale)),
        *_executor_fields(locale, executor),
    ]
    return LogEntry(
        "member_ban", _t(locale, "logging.title.member_ban"), RED, at, "", tuple(fields)
    )


def member_unbanned(
    locale: str, user: UserRef, executor: Executor | None, at: datetime
) -> LogEntry:
    fields = [
        LogField(_t(locale, "logging.field.user"), _user(user, locale)),
        *_executor_fields(locale, executor),
    ]
    return LogEntry(
        "member_unban", _t(locale, "logging.title.member_unban"), GREEN, at, "", tuple(fields)
    )


# --- Rollen und Kanäle ---


def _changes_text(locale: str, changes: list[Change]) -> str:
    return "\n".join(
        f"**{_t(locale, change.label)}:** {clip(change.before, 200)} → {clip(change.after, 200)}"
        for change in changes
    )


def role_event(
    locale: str,
    event: str,
    name: str,
    role_id: int,
    changes: list[Change],
    executor: Executor | None,
    at: datetime,
) -> LogEntry | None:
    """`event` ist role_create, role_delete oder role_update. Update ohne Änderung → None."""
    if event == "role_update" and not changes:
        return None  # nur umsortiert: das ergäbe bei jedem Verschieben ein Dutzend Einträge
    color = {"role_create": GREEN, "role_delete": RED}.get(event, BLUE)
    fields = [
        LogField(_t(locale, "logging.field.name"), clip(name, 100)),
        LogField(_t(locale, "logging.field.id"), f"`{role_id}`"),
    ]
    if changes:
        fields.append(
            LogField(_t(locale, "logging.field.changes"), _changes_text(locale, changes), False)
        )
    fields.extend(_executor_fields(locale, executor))
    return LogEntry(event, _t(locale, f"logging.title.{event}"), color, at, "", tuple(fields))


def channel_event(
    locale: str,
    event: str,
    name: str,
    channel_id: int,
    kind: str,
    changes: list[Change],
    executor: Executor | None,
    at: datetime,
) -> LogEntry | None:
    if event == "channel_update" and not changes:
        return None
    color = {"channel_create": GREEN, "channel_delete": RED}.get(event, BLUE)
    fields = [
        LogField(_t(locale, "logging.field.name"), clip(name, 100)),
        LogField(_t(locale, "logging.field.type"), kind),
        LogField(_t(locale, "logging.field.id"), f"`{channel_id}`"),
    ]
    if changes:
        fields.append(
            LogField(_t(locale, "logging.field.changes"), _changes_text(locale, changes), False)
        )
    fields.extend(_executor_fields(locale, executor))
    return LogEntry(event, _t(locale, f"logging.title.{event}"), color, at, "", tuple(fields))


# --- Sprache und Einladungen ---


def voice_changed(
    locale: str,
    user: UserRef,
    before: tuple[int, str] | None,
    after: tuple[int, str] | None,
    at: datetime,
) -> LogEntry | None:
    """Betreten, Verlassen oder Wechseln. Gleicher Kanal (Stummschaltung o. ä.) → None."""
    if before == after:
        return None
    if before is None and after is not None:
        key, color = "logging.voice.joined", GREEN
        text = _t(locale, key, channel=after[0])
    elif after is None and before is not None:
        key, color = "logging.voice.left", RED
        text = _t(locale, key, channel=before[0])
    else:
        assert before is not None
        assert after is not None
        text = _t(locale, "logging.voice.moved", before=before[0], after=after[0])
        color = BLUE
    fields = (LogField(_t(locale, "logging.field.user"), _user(user, locale)),)
    return LogEntry("voice", _t(locale, "logging.title.voice"), color, at, text, fields)


def invite_created(
    locale: str,
    code: str,
    channel_id: int | None,
    inviter: UserRef | None,
    max_uses: int,
    max_age_seconds: int,
    temporary: bool,
    at: datetime,
) -> LogEntry:
    unlimited = _t(locale, "logging.value.unlimited")
    fields = [
        LogField(_t(locale, "logging.field.code"), f"`{clip(code, 50)}`"),
        LogField(_t(locale, "logging.field.channel"), f"<#{channel_id}>" if channel_id else "?"),
        LogField(_t(locale, "logging.field.inviter"), _user(inviter, locale)),
        LogField(_t(locale, "logging.field.max_uses"), str(max_uses) if max_uses else unlimited),
        LogField(
            _t(locale, "logging.field.expires"),
            _ts(at + timedelta(seconds=max_age_seconds), "R")
            if max_age_seconds
            else _t(locale, "logging.value.never"),
        ),
    ]
    if temporary:
        fields.append(
            LogField(_t(locale, "logging.field.temporary"), _t(locale, "logging.value.yes"))
        )
    return LogEntry(
        "invite_create", _t(locale, "logging.title.invite_create"), GREEN, at, "", tuple(fields)
    )


def invite_deleted(locale: str, code: str, channel_id: int | None, at: datetime) -> LogEntry:
    fields = (
        LogField(_t(locale, "logging.field.code"), f"`{clip(code, 50)}`"),
        LogField(_t(locale, "logging.field.channel"), f"<#{channel_id}>" if channel_id else "?"),
    )
    return LogEntry("invite_delete", _t(locale, "logging.title.invite_delete"), RED, at, "", fields)


__all__ = [
    "Change",
    "Executor",
    "LogEntry",
    "LogField",
    "LogFile",
    "MessageSnapshot",
    "UserRef",
    "channel_event",
    "clip",
    "invite_created",
    "invite_deleted",
    "member_banned",
    "member_joined",
    "member_left",
    "member_unbanned",
    "member_updated",
    "message_deleted",
    "message_edited",
    "messages_bulk_deleted",
    "render_bulk_file",
    "role_event",
    "voice_changed",
]
