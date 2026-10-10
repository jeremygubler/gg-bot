"""Transkript eines Tickets als eigenständige HTML-Datei. Reine Funktion, ohne Discord.

Alles, was ein Nutzer schreiben kann, wird maskiert. Links gibt es nur zu http(s)-Adressen,
und die Seite lädt weder Skripte noch fremde Inhalte: ein Transkript, das ein Support-Mitglied im
Browser öffnet, darf nie Code eines Ticket-Erstellers ausführen.
"""

from dataclasses import dataclass
from datetime import datetime
from html import escape
from urllib.parse import urlsplit

MAX_MESSAGES = 2000
MAX_CONTENT = 4000

_CSP = (
    '<meta http-equiv="Content-Security-Policy" '
    "content=\"default-src 'none'; style-src 'unsafe-inline'\">"
)
_CSS = (
    "body{font-family:system-ui,sans-serif;background:#313338;color:#dbdee1;margin:0;padding:24px}"
    "h1{font-size:20px}.meta{color:#949ba4;font-size:13px;margin-bottom:16px}"
    ".msg{padding:6px 0;border-top:1px solid #3f4147}.a{font-weight:600;color:#fff}"
    ".t{color:#949ba4;font-size:12px;margin-left:8px}.c{white-space:pre-wrap;word-break:break-word}"
    ".att,.emb{font-size:13px;color:#949ba4}a{color:#00a8fc}"
)


@dataclass(frozen=True)
class TranscriptMessage:
    author_id: int
    author_name: str
    at: datetime
    content: str
    attachments: tuple[tuple[str, str], ...] = ()  # (Dateiname, URL)
    embed_count: int = 0
    is_bot: bool = False


def _safe_url(url: str) -> str | None:
    parts = urlsplit(url)
    if parts.scheme in ("http", "https") and parts.netloc:
        return url
    return None


def render_transcript(
    messages: list[TranscriptMessage],
    *,
    title: str,
    meta: str,
    truncated_label: str,
    attachment_label: str,
    embed_label: str,
    truncated: bool = False,
) -> str:
    rows: list[str] = []
    for message in messages[:MAX_MESSAGES]:
        content = message.content[:MAX_CONTENT]
        parts = [
            f'<div class="msg"><span class="a">{escape(message.author_name)}</span>'
            f'<span class="t">{message.at:%Y-%m-%d %H:%M:%S} UTC</span>'
            f'<div class="c">{escape(content)}</div>'
        ]
        for name, url in message.attachments:
            safe = _safe_url(url)
            label = escape(name)
            link = (
                f'<a href="{escape(safe, quote=True)}" rel="noopener noreferrer">{label}</a>'
                if safe
                else label
            )
            parts.append(f'<div class="att">{escape(attachment_label)}: {link}</div>')
        if message.embed_count:
            parts.append(f'<div class="emb">{escape(embed_label)}: {message.embed_count}</div>')
        parts.append("</div>")
        rows.append("".join(parts))
    note = f'<p class="meta">{escape(truncated_label)}</p>' if truncated else ""
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        f"{_CSP}<title>{escape(title)}</title><style>{_CSS}</style></head><body>"
        f'<h1>{escape(title)}</h1><div class="meta">{escape(meta)}</div>{note}{"".join(rows)}'
        "</body></html>"
    )
