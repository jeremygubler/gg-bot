import pytest

from ggbot.core.customcmd.template import (
    FIELDS,
    MAX_OUTPUT,
    MAX_RANDOM_OPTIONS,
    MAX_TEMPLATE,
    Field,
    Random,
    Text,
    parse,
    render,
    validate,
)

VALUES = {
    "user": "<@1>", "name": "Alice", "server": "Test", "channel": "<#2>",
    "args": "hallo welt", "members": 42, "uses": 7,
}  # fmt: skip


def first(n: int) -> int:
    return 0


def last(n: int) -> int:
    return n - 1


def test_text_und_felder() -> None:
    assert parse("Hallo {name}!") == [Text("Hallo "), Field("name"), Text("!")]
    assert render("Hallo {name} auf {server}", VALUES, first) == "Hallo Alice auf Test"


@pytest.mark.parametrize("name", FIELDS)
def test_jedes_feld_ist_erlaubt_und_wird_ersetzt(name: str) -> None:
    assert render("{" + name + "}", VALUES, first) == str(VALUES[name])


def test_zahlen_werden_zu_text() -> None:
    assert (
        render("{members} Mitglieder, {uses}. Aufruf", VALUES, first) == "42 Mitglieder, 7. Aufruf"
    )


def test_fehlender_wert_ist_leer_nicht_abstuerzen() -> None:
    assert render("a{args}b", {}, first) == "ab"


def test_klammern_maskieren() -> None:
    assert render("{{name}} und }}", VALUES, first) == "{name} und }"
    assert parse("{{") == [Text("{")]


def test_zufall_nimmt_die_funktion() -> None:
    assert render("{random:ja|nein|vielleicht}", VALUES, first) == "ja"
    assert render("{random:ja|nein|vielleicht}", VALUES, last) == "vielleicht"
    assert render("{random: a | b }", VALUES, last) == "b"  # Leerraum wird entfernt


def test_zufall_index_ausserhalb_wird_abgefangen() -> None:
    assert render("{random:a|b}", VALUES, lambda n: 99) in ("a", "b")


def test_zufall_einzelne_moeglichkeit() -> None:
    assert parse("{random:x}") == [Random(("x",))]


def test_argumente_werden_nicht_erneut_ausgewertet() -> None:
    # Was jemand als Argument tippt, ist Text: kein Platzhalter, kein Zufall.
    values = VALUES | {"args": "{user} {random:a|b} {{x}}"}
    assert render("sagt: {args}", values, first) == "sagt: {user} {random:a|b} {{x}}"


def test_ausgabe_wird_gekuerzt() -> None:
    assert len(render("{args}", {"args": "x" * 5000}, first)) == MAX_OUTPUT


@pytest.mark.parametrize(
    "template",
    [
        "{unbekannt}",
        "{user.__class__}",
        "{0}",
        "{}",
        "{ user }",
        "{USER}",
        "{user!r}",
        "{user:>10}",
        "{random}",
        "{random:}",
        "{random:a||b}",
        "{random:a|}",
        "{random:{user}}",
        "{random:{user}}}",  # sähe sonst wie eine gültige Zufallswahl "{user" aus
        "{",
        "offen {name",
        "zu }",
        "{{name}",
        "x" * (MAX_TEMPLATE + 1),
        "{random:" + "|".join(["a"] * (MAX_RANDOM_OPTIONS + 1)) + "}",
        "{random:" + "x" * 301 + "}",
    ],
)
def test_ungueltige_vorlagen(template: str) -> None:
    with pytest.raises(ValueError):  # noqa: PT011
        validate(template)


def test_grenzfaelle_gueltig() -> None:
    validate("x" * MAX_TEMPLATE)
    validate("{random:" + "|".join(["a"] * MAX_RANDOM_OPTIONS) + "}")
    validate("{random:" + "x" * 300 + "}")
    validate("")
    validate("{{}}")


def test_kein_zugriff_auf_attribute_oder_methoden() -> None:
    for evil in ("{name.__class__}", "{name.upper}", "{args[0]}", "{server.__init__.__globals__}"):
        with pytest.raises(ValueError):  # noqa: PT011
            validate(evil)
