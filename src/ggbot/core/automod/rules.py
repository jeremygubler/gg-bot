"""Die einzelnen Automod-Prüfungen als reine Funktionen."""

import re
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlsplit

import re2

from ggbot.core.automod.text import collapse, normalize, tokens
from ggbot.core.errors import UserError

# --- Grossschreibung ---


def caps_ratio(content: str) -> tuple[int, float]:
    """(Anzahl Buchstaben, Anteil Grossbuchstaben in Prozent). Nur Buchstaben zählen."""
    letters = [ch for ch in content if ch.isalpha()]
    if not letters:
        return 0, 0.0
    upper = sum(1 for ch in letters if ch.isupper())
    return len(letters), 100.0 * upper / len(letters)


def too_many_caps(content: str, *, min_length: int, max_percent: int) -> bool:
    count, percent = caps_ratio(content)
    return count >= min_length and percent > max_percent


# --- Einladungen ---

_INVITE = re.compile(
    r"(?:discord(?:app)?\.com/invite|discord\.gg|discord\.me|discord\.io|dsc\.gg)/+([\w-]+)",
    re.IGNORECASE,
)


def find_invites(content: str) -> list[str]:
    """Alle Einladungscodes im Text, kleingeschrieben."""
    return [code.lower() for code in _INVITE.findall(content)]


def disallowed_invites(content: str, allowed_codes: list[str]) -> list[str]:
    allowed = {code.lower() for code in allowed_codes}
    return [code for code in find_invites(content) if code not in allowed]


# --- Links ---

_URL = re.compile(r"(?:https?://|www\.)[^\s<>\"'`]+", re.IGNORECASE)
_BARE = re.compile(
    r"(?<![\w@.-])((?:[a-z0-9-]+\.)+(?:[a-z]{2,24}))(?::\d+)?(?:/[^\s<>\"'`]*)?(?![\w@-])",
    re.IGNORECASE,
)
# Ohne http:// oder www. zählt "wort.wort" nur mit bekannter Endung als Link. Sonst würde ein
# vergessenes Leerzeichen ("Ende.Nächster Satz") als Domain gelten. Endungen, die hier fehlen,
# fallen nur ohne Schema durch; mit http:// oder www. wird jede Domain erkannt.
_KNOWN_TLDS = frozenset(
    [
        "com",
        "net",
        "org",
        "info",
        "biz",
        "edu",
        "gov",
        "int",
        "mil",
        "io",
        "gg",
        "me",
        "tv",
        "co",
        "cc",
        "ly",
        "to",
        "sh",
        "ai",
        "app",
        "dev",
        "xyz",
        "top",
        "site",
        "online",
        "store",
        "shop",
        "club",
        "live",
        "fun",
        "link",
        "click",
        "work",
        "icu",
        "vip",
        "pro",
        "wiki",
        "page",
        "cloud",
        "tech",
        "space",
        "website",
        "one",
        "world",
        "today",
        "news",
        "media",
        "blog",
        "art",
        "games",
        "game",
        "bet",
        "casino",
        "win",
        "gift",
        "cash",
        "money",
        "download",
        "stream",
        "video",
        "chat",
        "social",
        "email",
        "zip",
        "mov",
        "ru",
        "ua",
        "pl",
        "cz",
        "sk",
        "hu",
        "ro",
        "bg",
        "tr",
        "gr",
        "it",
        "es",
        "pt",
        "fr",
        "nl",
        "be",
        "lu",
        "ch",
        "at",
        "de",
        "dk",
        "se",
        "no",
        "fi",
        "is",
        "ie",
        "uk",
        "eu",
        "us",
        "ca",
        "mx",
        "br",
        "ar",
        "cl",
        "co",
        "jp",
        "kr",
        "cn",
        "hk",
        "tw",
        "in",
        "id",
        "th",
        "vn",
        "au",
        "nz",
        "za",
        "ng",
        "ke",
        "eg",
        "ma",
        "il",
        "ae",
        "sa",
        "ir",
        "pk",
        "bd",
        "ws",
        "su",
        "tk",
        "ml",
        "ga",
        "cf",
        "gq",
        "pw",
        "la",
        "gl",
        "fm",
        "im",
        "ac",
    ]
)
_TRAILING = ".,;:!?)]}>*_~'\""


def normalize_domain(domain: str) -> str:
    """ "WWW.Example.COM." → "example.com" (nur www wird entfernt, andere Subdomains bleiben)."""
    domain = domain.strip().lower().rstrip(".")
    domain = domain.removeprefix("www.")
    try:
        return domain.encode("idna").decode("ascii")
    except UnicodeError:
        return domain


def extract_domains(content: str) -> list[str]:
    """Alle Hostnamen, die ein Mensch als Link lesen würde (mit und ohne http://)."""
    found: list[str] = []
    stripped = content
    for match in _URL.finditer(content):
        raw = match.group(0).rstrip(_TRAILING)
        if raw.lower().startswith("www."):
            raw = "http://" + raw
        host = urlsplit(raw).hostname  # entfernt Zugangsdaten ("gut.de@boese.de" → boese.de)
        if host:
            found.append(normalize_domain(host))
        stripped = stripped.replace(match.group(0), " ")
    for match in _BARE.finditer(stripped):
        domain = normalize_domain(match.group(1))
        if domain.rsplit(".", 1)[-1] in _KNOWN_TLDS:
            found.append(domain)
    return found


def domain_matches(domain: str, listed: str) -> bool:
    """`listed` trifft sich selbst und alle Subdomains, aber nicht "evilexample.com"."""
    listed = normalize_domain(listed)
    return domain == listed or domain.endswith("." + listed)


def blocked_domains(content: str, *, mode: str, listed: list[str]) -> list[str]:
    """Hostnamen, die der Regel widersprechen.

    blacklist: alles auf der Liste ist verboten. whitelist: alles ausser der Liste.
    """
    domains = extract_domains(content)
    if mode == "whitelist":
        return [d for d in domains if not any(domain_matches(d, item) for item in listed)]
    return [d for d in domains if any(domain_matches(d, item) for item in listed)]


# --- Wortfilter ---

_WORD_CHARS = r"[\pL\pN_]"


@dataclass(frozen=True)
class WordEntryKey:
    type: str
    pattern: str


def validate_entry(type: str, pattern: str) -> None:
    """Wirft `UserError`, wenn das Muster unbrauchbar ist (schon beim Anlegen)."""
    if not pattern.strip():
        raise UserError("error.automod_pattern_empty")
    if type == "wildcard" and any(ch.isspace() for ch in pattern):
        raise UserError("error.automod_wildcard_space")
    if type in ("wildcard", "regex") and _compile(type, pattern).search(""):
        # Ein Muster, das auf nichts passt ("a*", "*"), träfe jede Nachricht.
        raise UserError("error.automod_pattern_matches_all")


def _wildcard_to_regex(pattern: str) -> str:
    out = []
    for ch in normalize(pattern):
        if ch == "*":
            out.append(f"{_WORD_CHARS}*")
        elif ch == "?":
            out.append(_WORD_CHARS)
        else:
            out.append(re.escape(ch))
    return "".join(out)


@lru_cache(maxsize=2048)
def _compile(type: str, pattern: str) -> "re2._Regexp":
    options = re2.Options()
    options.case_sensitive = False
    source = _wildcard_to_regex(pattern) if type == "wildcard" else pattern
    if type == "wildcard":
        source = f"^(?:{source})$"
    try:
        return re2.compile(source, options)
    except re2.error as exc:
        # RE2 kennt bewusst weder Rückreferenzen noch Lookarounds: dadurch ist die Laufzeit
        # immer linear und ein bösartiges Muster kann den Bot nicht einfrieren.
        raise UserError("error.automod_regex_invalid", detail=str(exc)[:100]) from exc


def find_word_matches(content: str, entries: list[tuple[str, str]]) -> list[str]:
    """Muster (wie eingetragen), die im Text vorkommen. `entries` = (type, pattern)."""
    normalized = normalize(content)
    words = tokens(normalized)
    padded = " " + " ".join(words) + " "
    hits: list[str] = []
    for type_, pattern in entries:
        if type_ == "word":
            phrase = " ".join(tokens(normalize(pattern)))
            if phrase and f" {phrase} " in padded:
                hits.append(pattern)
        elif type_ == "wildcard":
            compiled = _compile(type_, pattern)
            if any(compiled.search(word) for word in words):
                hits.append(pattern)
        elif _compile(type_, pattern).search(normalized):
            hits.append(pattern)
    return hits


# --- Erwähnungen ---


def too_many_mentions(user_ids: set[int], role_ids: set[int], *, maximum: int) -> bool:
    return len(user_ids) + len(role_ids) > maximum


__all__ = [
    "blocked_domains",
    "caps_ratio",
    "collapse",
    "disallowed_invites",
    "extract_domains",
    "find_invites",
    "find_word_matches",
    "too_many_caps",
    "too_many_mentions",
    "validate_entry",
]
