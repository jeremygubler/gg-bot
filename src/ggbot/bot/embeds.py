"""Darstellung von Cases, Historie und Notizen. Reine Formatierung, keine Logik."""

from datetime import datetime

import discord

from ggbot.core.duration import format_duration
from ggbot.core.i18n import translator
from ggbot.core.services.moderation import CaseView, HistoryPage, NoteView

MAX_NOTES_SHOWN = 10
_REASON_PREVIEW = 60


def _ts(moment: datetime, style: str = "F") -> str:
    return f"<t:{int(moment.timestamp())}:{style}>"


def _short(text: str | None, locale: str, limit: int = _REASON_PREVIEW) -> str:
    text = text or translator.t(locale, "mod.no_reason")
    text = text.replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def case_embed(case: CaseView, locale: str, color: int) -> discord.Embed:
    t = translator.t
    embed = discord.Embed(
        title=t(
            locale, "mod.case.title", case_no=case.case_no, type=t(locale, f"mod.type.{case.type}")
        ),
        color=color,
        timestamp=case.created_at,
    )
    embed.add_field(
        name=t(locale, "mod.case.target"), value=f"<@{case.target_id}>\n`{case.target_id}`"
    )
    embed.add_field(name=t(locale, "mod.case.moderator"), value=f"<@{case.moderator_id}>")
    embed.add_field(
        name=t(locale, "mod.case.reason"),
        value=case.reason or t(locale, "mod.no_reason"),
        inline=False,
    )
    if case.duration_seconds is not None:
        embed.add_field(
            name=t(locale, "mod.case.duration"), value=format_duration(case.duration_seconds)
        )
    if case.expires_at is not None:
        embed.add_field(name=t(locale, "mod.case.expires"), value=_ts(case.expires_at))
    if case.type in ("warn", "tempban"):
        state = "mod.case.active" if case.active else "mod.case.inactive"
        embed.add_field(name=t(locale, "mod.case.status"), value=t(locale, state))
    if case.ref_case_no is not None:
        embed.add_field(name=t(locale, "mod.case.reference"), value=f"#{case.ref_case_no}")
    return embed


def history_embed(page: HistoryPage, user_label: str, locale: str, color: int) -> discord.Embed:
    t = translator.t
    embed = discord.Embed(title=t(locale, "mod.history.title", user=user_label), color=color)
    summary = t(
        locale,
        "mod.history.summary",
        total=page.total,
        warns=page.active_warns,
        notes=page.notes,
    )
    if not page.cases:
        embed.description = f"{summary}\n\n{t(locale, 'mod.history.empty')}"
        return embed
    lines = [
        t(
            locale,
            "mod.history.line",
            case_no=c.case_no,
            type=t(locale, f"mod.type.{c.type}"),
            ts=int(c.created_at.timestamp()),
            reason=_short(c.reason, locale),
        )
        for c in page.cases
    ]
    embed.description = summary + "\n\n" + "\n".join(lines)
    embed.set_footer(text=t(locale, "mod.history.footer", page=page.page, pages=page.pages))
    return embed


def notes_embed(notes: list[NoteView], user_label: str, locale: str, color: int) -> discord.Embed:
    t = translator.t
    embed = discord.Embed(title=t(locale, "mod.note.title", user=user_label), color=color)
    if not notes:
        embed.description = t(locale, "mod.note.empty")
        return embed
    shown = notes[-MAX_NOTES_SHOWN:]
    lines = [
        t(
            locale,
            "mod.note.line",
            id=n.id,
            author=n.author_id,
            ts=int(n.created_at.timestamp()),
            content=_short(n.content, locale, 300),
        )
        for n in shown
    ]
    if len(notes) > len(shown):
        lines.insert(0, t(locale, "mod.note.more", count=len(notes) - len(shown)))
    embed.description = "\n\n".join(lines)
    return embed
