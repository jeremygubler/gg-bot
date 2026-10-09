import time

import pytest

from ggbot.core.automod.rules import (
    blocked_domains,
    caps_ratio,
    disallowed_invites,
    extract_domains,
    find_invites,
    find_word_matches,
    too_many_caps,
    too_many_mentions,
    validate_entry,
)
from ggbot.core.automod.text import collapse, normalize
from ggbot.core.errors import UserError

# --- Grossschreibung ---


def test_caps_ratio_zaehlt_nur_buchstaben() -> None:
    assert caps_ratio("ABC 123 !!!") == (3, 100.0)
    assert caps_ratio("abc") == (3, 0.0)
    assert caps_ratio("12345 !!!") == (0, 0.0)
    assert caps_ratio("") == (0, 0.0)


def test_caps_erst_ab_mindestlaenge() -> None:
    assert not too_many_caps("HALLO", min_length=10, max_percent=70)
    assert too_many_caps("HALLO LEUTE WIE GEHTS", min_length=10, max_percent=70)


def test_caps_schwelle_ist_strikt_groesser() -> None:
    text = "ABCDEFG" + "hijklm"[:3]  # 7 von 10 = 70 %
    assert caps_ratio(text) == (10, 70.0)
    assert not too_many_caps(text, min_length=10, max_percent=70)
    assert too_many_caps(text, min_length=10, max_percent=69)


def test_caps_erkennt_umlaute_und_ignoriert_ziffern() -> None:
    assert too_many_caps("ÄÖÜÄÖÜÄÖÜÄÖÜ 12345", min_length=10, max_percent=70)
    assert not too_many_caps("Das ist ein ganz normaler Satz mit Ä", min_length=10, max_percent=70)


# --- Einladungen ---


@pytest.mark.parametrize(
    "text",
    [
        "komm https://discord.gg/abc123",
        "discord.gg/abc123",
        "DISCORD.GG/ABC123",
        "https://discord.com/invite/abc123",
        "https://discordapp.com/invite/abc123",
        "http://dsc.gg/abc123",
        "discord.gg//abc123",
        "hier:discord.gg/abc123!",
    ],
)
def test_einladungen_werden_erkannt(text: str) -> None:
    assert find_invites(text) == ["abc123"]


def test_keine_einladung_in_normalem_text() -> None:
    for text in ("discord ist toll", "gg wp", "https://discord.com/channels/1/2", "discord.gg"):
        assert find_invites(text) == []


def test_erlaubte_codes_sind_ausgenommen() -> None:
    assert disallowed_invites("discord.gg/Mein und discord.gg/fremd", ["mein"]) == ["fremd"]
    assert disallowed_invites("discord.gg/mein", ["MEIN"]) == []


# --- Links ---


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("schau https://example.com/pfad?x=1", ["example.com"]),
        ("HTTP://EXAMPLE.COM", ["example.com"]),
        ("www.example.com", ["example.com"]),
        ("example.com", ["example.com"]),
        ("(siehe https://example.com).", ["example.com"]),
        ("https://sub.example.co.uk:8080/x", ["sub.example.co.uk"]),
        ("<https://example.com>", ["example.com"]),
        ("**https://example.com**", ["example.com"]),
        # Zugangsdaten-Trick: der Hostname ist der Teil nach dem @
        ("https://gut.de@boese.com/x", ["boese.com"]),
        ("zwei: a.com und https://b.org", ["b.org", "a.com"]),
        ("kein link hier, auch nicht 3.14 oder v1.2", []),
        ("Satz.Neuer Satz", []),
        ("mail an test@example.com", []),
    ],
)
def test_domains_werden_extrahiert(text: str, expected: list[str]) -> None:
    assert extract_domains(text) == expected


def test_blacklist_trifft_domain_und_subdomains_aber_keine_aehnlichen() -> None:
    listed = ["example.com"]

    def check(text: str) -> list[str]:
        return blocked_domains(text, mode="blacklist", listed=listed)

    assert check("https://example.com/x") == ["example.com"]
    assert check("https://www.example.com") == ["example.com"]
    assert check("https://shop.example.com") == ["shop.example.com"]
    assert check("https://evilexample.com") == []
    assert check("https://example.com.evil.org") == []
    assert check("https://example.org") == []


def test_whitelist_erlaubt_nur_gelistete() -> None:
    listed = ["youtube.com", "youtu.be"]

    def check(text: str) -> list[str]:
        return blocked_domains(text, mode="whitelist", listed=listed)

    assert check("https://youtu.be/x und https://www.youtube.com/y") == []
    assert check("https://youtube.com.evil.org/x") == ["youtube.com.evil.org"]
    assert check("https://evil.org") == ["evil.org"]


def test_whitelist_ohne_eintraege_blockt_jeden_link() -> None:
    assert blocked_domains("https://a.com", mode="whitelist", listed=[]) == ["a.com"]
    assert blocked_domains("kein link", mode="whitelist", listed=[]) == []


def test_idn_und_gross_schreibung_in_der_liste() -> None:
    assert blocked_domains("https://bücher.de", mode="blacklist", listed=["BÜCHER.de"]) == [
        "xn--bcher-kva.de"
    ]


# --- Wortfilter ---


def test_normalisierung_vergleichsform() -> None:
    assert normalize("ÄRGER") == "arger"
    assert normalize("Straße") == "strasse"
    assert normalize("fu​ck") == "fuck"  # Zero-Width mitten im Wort
    assert normalize("ＡＢＣ") == "abc"  # Vollbreite Buchstaben
    assert collapse("  Hallo   WELT!! ") == "hallo welt"


def hits(text: str, *entries: tuple[str, str]) -> list[str]:
    return find_word_matches(text, list(entries))


def test_wort_trifft_nur_ganze_woerter() -> None:
    assert hits("das ist ein Arsch", ("word", "arsch")) == ["arsch"]
    assert hits("Klassenarbeit und Bassist", ("word", "ass")) == []
    assert hits("ass", ("word", "ass")) == ["ass"]
    assert hits("Du ARSCH!", ("word", "Arsch")) == ["Arsch"]


def test_wort_ignoriert_akzente_und_unsichtbares() -> None:
    assert hits("so ein Ärger", ("word", "arger")) == ["arger"]
    assert hits("so ein arger", ("word", "Ärger")) == ["Ärger"]
    assert hits("a​rsch", ("word", "arsch")) == ["arsch"]


def test_phrase_als_wort_eintrag() -> None:
    assert hits("ihr seid so böse leute", ("word", "so böse")) == ["so böse"]
    assert hits("ihr seid so sehr böse", ("word", "so böse")) == []
    assert hits("sooo böse", ("word", "so böse")) == []


def test_wildcard() -> None:
    assert hits("dumm dümmer dummkopf", ("wildcard", "dumm*")) == ["dumm*"]
    assert hits("klug", ("wildcard", "dumm*")) == []
    assert hits("hund", ("wildcard", "h?nd")) == ["h?nd"]
    assert hits("hnd", ("wildcard", "h?nd")) == []
    assert hits("xbadx", ("wildcard", "*bad*")) == ["*bad*"]
    # Sonderzeichen im Muster sind wörtlich gemeint
    assert hits("a.b", ("wildcard", "a.b")) == []  # Punkt trennt Wörter: "a" und "b"


def test_wildcard_darf_nicht_ueber_wortgrenzen_greifen() -> None:
    assert hits("dumm kopf", ("wildcard", "dumm*kopf")) == []


def test_regex() -> None:
    assert hits("Ruf 0800-123 an", ("regex", r"\d{4}-\d{3}")) == [r"\d{4}-\d{3}"]
    assert hits("nichts", ("regex", r"\d{4}")) == []
    assert hits("FOO bar", ("regex", r"foo\s+bar")) == [r"foo\s+bar"]


def test_regex_bleibt_bei_boesartigem_muster_linear() -> None:
    started = time.perf_counter()
    assert hits("a" * 50000 + "b", ("regex", r"(a+)+$")) == []
    assert time.perf_counter() - started < 1.0  # mit Backtracking: praktisch nie fertig


@pytest.mark.parametrize(
    ("type_", "pattern", "key"),
    [
        ("regex", "(unvollständig", "error.automod_regex_invalid"),
        ("regex", r"(?<=a)b", "error.automod_regex_invalid"),  # Lookbehind
        ("regex", r"(a)\1", "error.automod_regex_invalid"),  # Rückreferenz
        ("word", "   ", "error.automod_pattern_empty"),
        ("wildcard", "zwei wörter*", "error.automod_wildcard_space"),
    ],
)
def test_ungueltige_muster_werden_beim_anlegen_abgelehnt(
    type_: str, pattern: str, key: str
) -> None:
    with pytest.raises(UserError) as exc:
        validate_entry(type_, pattern)
    assert exc.value.key == key


def test_gueltige_muster_werden_angenommen() -> None:
    validate_entry("word", "böse")
    validate_entry("wildcard", "b?se*")
    validate_entry("regex", r"\d+")


# --- Erwähnungen ---


def test_erwaehnungen_zaehlen_nutzer_und_rollen_einmalig() -> None:
    assert not too_many_mentions({1, 2, 3}, {10, 11}, maximum=5)
    assert too_many_mentions({1, 2, 3}, {10, 11, 12}, maximum=5)
    assert not too_many_mentions({1}, set(), maximum=1)
    assert too_many_mentions({1, 2}, set(), maximum=1)
