from datetime import UTC, datetime, timedelta

import pytest

from ggbot.core.i18n import LOCALES, translator
from ggbot.core.logs import entries as e
from ggbot.core.settings.eventlog import EVENT_TYPES

AT = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
ALICE = e.UserRef(1, "alice")


def values(entry: e.LogEntry) -> dict[str, str]:
    return {f.name: f.value for f in entry.fields}


@pytest.mark.parametrize("locale", LOCALES)
def test_jede_ereignisart_hat_einen_titel_in_allen_sprachen(locale: str) -> None:
    for event in EVENT_TYPES:
        key = f"logging.title.{event}"
        assert translator.has(locale, key), key
    assert translator.has(locale, "logging.title.member_kicked")


# --- Nachrichten ---


def test_geloeschte_nachricht_mit_inhalt() -> None:
    snap = e.MessageSnapshot(5, 10, ALICE, "Hallo Welt", ("https://cdn/x.png",), AT)
    entry = e.message_deleted("de", 10, 5, snap, AT)
    assert entry.event == "message_delete"
    fields = values(entry)
    assert "<@1>" in fields["Autor"]
    assert "Hallo Welt" in fields["Inhalt"]
    assert "https://cdn/x.png" in fields["Anhänge"]
    assert "<#10>" in fields["Kanal"]


def test_geloeschte_nachricht_ohne_cache_sagt_das_ehrlich() -> None:
    entry = e.message_deleted("de", 10, 5, None, AT)
    assert "nicht mehr bekannt" in entry.description
    assert values(entry)["Nachrichten-ID"] == "`5`"


def test_lange_inhalte_werden_gekuerzt_und_backticks_entschaerft() -> None:
    snap = e.MessageSnapshot(5, 10, ALICE, "```" + "x" * 5000, (), AT)
    value = values(e.message_deleted("en", 10, 5, snap, AT))["Content"]
    assert len(value) <= 1024
    assert value.count("```") == 2  # nur die umschliessenden
    assert value.rstrip("`").endswith("…")


def test_bearbeitung_zeigt_vorher_nachher_und_link() -> None:
    entry = e.message_edited("en", "https://discord.com/x", ALICE, 10, "alt", "neu", AT)
    fields = values(entry)
    assert "alt" in fields["Before"]
    assert "neu" in fields["After"]
    assert "https://discord.com/x" in fields["Link"]


def test_bearbeitung_ohne_bekanntes_vorher() -> None:
    entry = e.message_edited("en", "u", ALICE, 10, None, "neu", AT)
    assert values(entry)["Before"] == "(not known)"


def test_massenloeschung_liefert_datei_mit_allen_nachrichten() -> None:
    cached = [
        e.MessageSnapshot(2, 10, e.UserRef(2, "bob"), "zweite\nzeile", ("https://cdn/f.png",), AT),
        e.MessageSnapshot(1, 10, ALICE, "erste", (), AT - timedelta(minutes=1)),
    ]
    entry = e.messages_bulk_deleted("de", 10, [3, 2, 1], cached, AT)
    assert entry.file is not None
    text = entry.file.content.decode("utf-8")
    assert entry.file.filename.startswith("deleted-10-")
    assert entry.file.filename.endswith(".txt")
    lines = text.splitlines()
    assert "3 Nachrichten, davon 2" in lines[0]
    body = "\n".join(lines[2:])
    assert body.index("erste") < body.index("zeile")  # älteste zuerst (nach ID)
    assert "alice (1)" in text
    assert "    zeile" in text  # Folgezeilen eingerückt
    assert "+ https://cdn/f.png" in text
    assert "[3] " in text  # unbekannte nur als ID
    assert values(entry)["Bekannter Inhalt"] == "2/3"


def test_massenloeschung_mit_umlauten_ist_utf8() -> None:
    cached = [e.MessageSnapshot(1, 10, ALICE, "Grüsse ☃", (), AT)]
    entry = e.messages_bulk_deleted("de", 10, [1], cached, AT)
    assert entry.file is not None
    assert "Grüsse ☃" in entry.file.content.decode("utf-8")


# --- Mitglieder ---


def test_beitritt() -> None:
    entry = e.member_joined("de", ALICE, AT - timedelta(days=2), 123, AT)
    fields = values(entry)
    assert fields["Mitglieder jetzt"] == "123"
    assert "<t:" in fields["Konto erstellt"]
    assert e.member_joined("de", ALICE, AT, None, AT).fields[-1].name != "Mitglieder jetzt"


def test_austritt_und_kick() -> None:
    plain = e.member_left("de", ALICE, AT, ("Mod", "VIP"), None, AT)
    assert plain.title == "Mitglied gegangen"
    assert "Mod, VIP" in values(plain)["Rollen"]
    kicked = e.member_left("de", ALICE, None, (), e.Executor(9, "Spam"), AT)
    assert kicked.title == "Mitglied gekickt"
    assert values(kicked)["Durch"] == "<@9>"
    assert values(kicked)["Grund"] == "Spam"


def test_mitgliedsaenderung_nur_bei_relevantem() -> None:
    same: tuple[str | None, str | None] = ("x", "x")
    no_timeout: tuple[datetime | None, datetime | None] = (None, None)
    assert e.member_updated("en", ALICE, (), (), same, no_timeout, None, AT) is None
    entry = e.member_updated(
        "en", ALICE, ("Mod",), ("Gast",), ("alt", None), (None, AT + timedelta(hours=1)),
        e.Executor(9), AT,
    )  # fmt: skip
    assert entry is not None
    fields = values(entry)
    assert fields["Roles added"] == "Mod"
    assert fields["Roles removed"] == "Gast"
    assert fields["Nickname"] == "alt → none"
    assert "<t:" in fields["Timeout until"]
    assert fields["By"] == "<@9>"


def test_timeout_ende() -> None:
    entry = e.member_updated("en", ALICE, (), (), (None, None), (AT, None), None, AT)
    assert entry is not None
    assert values(entry)["Timeout until"] == "ended"


def test_bann_und_entbannung_mit_und_ohne_ausfuehrenden() -> None:
    banned = e.member_banned("en", ALICE, e.Executor(9, "Betrug"), AT)
    assert values(banned)["By"] == "<@9>"
    assert values(banned)["Reason"] == "Betrug"
    unknown = e.member_unbanned("en", ALICE, None, AT)
    assert "By" not in values(unknown)  # kein Audit-Log-Recht: kein erfundener Wert
    assert e.member_unbanned("en", ALICE, e.Executor(None), AT).fields == unknown.fields


# --- Rollen, Kanäle ---


def test_rolle_umsortieren_ergibt_keinen_eintrag() -> None:
    assert e.role_event("en", "role_update", "Mod", 7, [], None, AT) is None
    assert e.channel_event("en", "channel_update", "allgemein", 7, "text", [], None, AT) is None


def test_rolle_erstellt_geloescht_geaendert() -> None:
    created = e.role_event("en", "role_create", "Mod", 7, [], e.Executor(9), AT)
    assert created is not None
    assert created.title == "Role created"
    deleted = e.role_event("en", "role_delete", "Mod", 7, [], None, AT)
    assert deleted is not None
    assert deleted.color == e.RED
    changed = e.role_event(
        "en",
        "role_update",
        "Mod",
        7,
        [e.Change("logging.change.color", "#000000", "#ff0000")],
        None,
        AT,
    )
    assert changed is not None
    assert "**Color:** #000000 → #ff0000" in values(changed)["Changes"]


def test_kanal_ereignisse() -> None:
    entry = e.channel_event(
        "de", "channel_update", "news", 8, "text",
        [e.Change("logging.change.topic", "alt", "neu")], e.Executor(9), AT,
    )  # fmt: skip
    assert entry is not None
    assert entry.title == "Kanal geändert"
    assert "**Thema:** alt → neu" in values(entry)["Änderungen"]
    assert values(entry)["Typ"] == "text"


# --- Sprache, Einladungen ---


def test_sprachwechsel() -> None:
    joined = e.voice_changed("de", ALICE, None, (5, "Lobby"), AT)
    assert joined is not None
    assert "ist <#5> beigetreten" in joined.description
    left = e.voice_changed("de", ALICE, (5, "Lobby"), None, AT)
    assert left is not None
    assert "verlassen" in left.description
    moved = e.voice_changed("de", ALICE, (5, "Lobby"), (6, "Spiele"), AT)
    assert moved is not None
    assert "<#5>" in moved.description
    assert "<#6>" in moved.description
    assert e.voice_changed("de", ALICE, (5, "Lobby"), (5, "Lobby"), AT) is None
    assert e.voice_changed("de", ALICE, None, None, AT) is None


def test_einladung_erstellt() -> None:
    entry = e.invite_created("en", "abc", 10, ALICE, 0, 0, False, AT)
    fields = values(entry)
    assert fields["Max uses"] == "unlimited"
    assert fields["Expires"] == "never"
    assert "Temporary membership" not in fields
    limited = e.invite_created("en", "abc", None, None, 5, 3600, True, AT)
    fields = values(limited)
    assert fields["Max uses"] == "5"
    assert "<t:" in fields["Expires"]
    assert fields["Temporary membership"] == "yes"
    assert fields["Channel"] == "?"


def test_einladung_geloescht() -> None:
    entry = e.invite_deleted("en", "abc", 10, AT)
    assert values(entry)["Code"] == "`abc`"


def test_nutzer_ohne_bekannte_person() -> None:
    snap = e.MessageSnapshot(5, 10, None, "x", (), AT)
    assert values(e.message_deleted("en", 10, 5, snap, AT))["Author"] == "unknown"
