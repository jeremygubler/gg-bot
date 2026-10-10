import pytest

from ggbot.core.messages import render_template, validate_template
from ggbot.core.roles.plan import (
    Change,
    emoji_key,
    is_emoji,
    plan_reaction_added,
    plan_reaction_removed,
    plan_select,
    plan_toggle,
)

PANEL = {1, 2, 3}


def sets(change: Change) -> tuple[set[int], set[int]]:
    return set(change.add), set(change.remove)


# --- Button / einzelne Rolle ---


def test_normal_schaltet_um() -> None:
    assert sets(plan_toggle("normal", 1, PANEL, set())) == ({1}, set())
    assert sets(plan_toggle("normal", 1, PANEL, {1})) == (set(), {1})
    assert sets(plan_toggle("normal", 1, PANEL, {2})) == ({1}, set())  # andere bleiben


def test_unique_wechselt_und_zweiter_klick_nimmt_weg() -> None:
    assert sets(plan_toggle("unique", 1, PANEL, {2})) == ({1}, {2})
    assert sets(plan_toggle("unique", 1, PANEL, {1})) == (set(), {1})
    assert sets(plan_toggle("unique", 1, PANEL, set())) == ({1}, set())


def test_unique_fasst_fremde_rollen_nie_an() -> None:
    assert sets(plan_toggle("unique", 1, PANEL, {2, 99})) == ({1}, {2})


def test_verify_nur_hinzufuegen() -> None:
    assert sets(plan_toggle("verify", 1, PANEL, set())) == ({1}, set())
    assert plan_toggle("verify", 1, PANEL, {1}).empty  # nie wieder entfernen
    assert sets(plan_toggle("verify", 1, PANEL, {2})) == ({1}, set())  # andere bleiben unberührt


# --- Auswahlmenü ---


def test_menue_setzt_den_gewuenschten_stand() -> None:
    assert sets(plan_select("normal", {1, 2}, PANEL, {2, 3})) == ({1}, {3})
    assert sets(plan_select("normal", set(), PANEL, {1, 2})) == (set(), {1, 2})  # alles abgewählt


def test_menue_unique_wechselt() -> None:
    assert sets(plan_select("unique", {2}, PANEL, {1})) == ({2}, {1})
    assert sets(plan_select("unique", set(), PANEL, {1})) == (set(), {1})
    with pytest.raises(ValueError, match="unique"):
        plan_select("unique", {1, 2}, PANEL, set())


def test_menue_verify_entfernt_nie() -> None:
    assert sets(plan_select("verify", {1}, PANEL, {2})) == ({1}, set())
    assert plan_select("verify", set(), PANEL, {1, 2}).empty


def test_menue_fasst_rollen_ausserhalb_des_panels_nicht_an() -> None:
    assert sets(plan_select("normal", {1, 99}, PANEL, {99, 2})) == (
        {1},
        {2},
    )  # 99 gehört nicht dazu


def test_menue_ohne_aenderung_ist_leer() -> None:
    assert plan_select("normal", {1}, PANEL, {1}).empty


# --- Reaktionen ---


def test_reaktion_gesetzt() -> None:
    assert sets(plan_reaction_added("normal", 1, PANEL, set())) == ({1}, set())
    assert plan_reaction_added("normal", 1, PANEL, {1}).empty
    assert sets(plan_reaction_added("unique", 1, PANEL, {2, 99})) == ({1}, {2})
    assert sets(plan_reaction_added("verify", 1, PANEL, {2})) == ({1}, set())


def test_reaktion_entfernt() -> None:
    assert sets(plan_reaction_removed("normal", 1, {1, 2})) == (set(), {1})
    assert sets(plan_reaction_removed("unique", 1, {1})) == (set(), {1})
    assert plan_reaction_removed("verify", 1, {1}).empty  # verifiziert bleibt verifiziert
    assert plan_reaction_removed("normal", 1, set()).empty


# --- Emojis ---


@pytest.mark.parametrize(
    ("raw", "key"),
    [
        ("👍", "👍"),
        ("✔️", "✔"),  # mit Variationsselektor
        ("✔", "✔"),
        (" 👍 ", "👍"),
        ("<:blobwave:123456789012345678>", "c:123456789012345678"),
        ("<a:party:123456789012345678>", "c:123456789012345678"),
        ("<:other_name:123456789012345678>", "c:123456789012345678"),
    ],
)
def test_emoji_schluessel(raw: str, key: str) -> None:
    assert emoji_key(raw) == key


def test_gleiches_emoji_in_verschiedenen_schreibweisen_ist_gleich() -> None:
    assert emoji_key("✔️") == emoji_key("✔")
    assert emoji_key("<:aa:123456789012345678>") == emoji_key("<a:bb:123456789012345678>")
    assert emoji_key("👍") != emoji_key("👎")
    assert emoji_key("<:aa:123456789012345678>") != emoji_key("<:aa:123456789012345679>")


# --- Vorlagen ---

ALLOWED = ("user", "name", "server", "count")


def test_vorlage_wird_gerendert() -> None:
    text = render_template(
        "Hallo {user} ({name}) auf {server}, Nr. {count}",
        ALLOWED,
        user="<@1>",
        name="Ann",
        server="S",
        count=5,
    )
    assert text == "Hallo <@1> (Ann) auf S, Nr. 5"
    validate_template("ohne Platzhalter", ALLOWED)
    validate_template("{{wörtlich}}", ALLOWED)


@pytest.mark.parametrize(
    "template",
    ["{secret}", "{user.__class__}", "{0}", "{user!r}", "{count:>5}", "{", "x" * 301],
)
def test_ungueltige_vorlagen(template: str) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        validate_template(template, ALLOWED)


@pytest.mark.parametrize(
    "text",
    [
        "🎮",
        "✔️",
        "✔",
        "❤️",
        "👍🏽",
        "🇨🇭",
        "👨‍👩‍👧‍👦",
        "1️⃣",
        "#️⃣",
        "*️⃣",
        "©️",
        "<:party:123456789012345678>",
        "<a:tanz:123456789012345678>",
        "  🎮  ",
    ],
)
def test_ist_emoji(text: str) -> None:
    assert is_emoji(text)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "  ",
        "a",
        "hallo",
        "1",
        "12",
        "+",
        "<:kaputt:>",
        "<:x:1>",
        ":smile:",
        "🎮🎮🎮🎮🎮🎮🎮🎮🎮🎮🎮🎮🎮🎮🎮🎮🎮",
        "🎮 🎵",
        "🎮a",
        "\n",
    ],
)
def test_ist_kein_emoji(text: str) -> None:
    assert not is_emoji(text)
