"""Ankündigungs-Nachricht: Grenzen von Discord und Bereinigung."""

import pytest
from pydantic import ValidationError

from ggbot.core.announce import message as m
from ggbot.core.errors import UserError


def norm(**kwargs: object) -> m.Message:
    return m.normalize(m.Message.model_validate(kwargs))


def test_nur_text_genuegt() -> None:
    assert norm(content="  Hallo  ").content == "Hallo"


def test_nur_embed_genuegt() -> None:
    assert norm(embed={"title": "Titel"}).embed is not None


def test_leere_nachricht_wird_abgelehnt() -> None:
    cases: tuple[dict[str, object], ...] = (
        {},
        {"content": "   "},
        {"embed": {}},
        {"content": " ", "embed": {"title": " "}},
    )
    for kwargs in cases:
        with pytest.raises(UserError) as error:
            norm(**kwargs)
        assert error.value.key == "error.announce_empty"


def test_leeres_embed_wird_zu_none() -> None:
    message = norm(content="x", embed={"title": "  ", "color": 5})
    assert message.embed is None


def test_leere_zeichenketten_werden_zu_none() -> None:
    embed = norm(embed={"title": "T", "footer": "  ", "author": ""}).embed
    assert embed is not None
    assert embed.footer is None
    assert embed.author is None


@pytest.mark.parametrize(
    ("field", "limit"),
    [
        ("title", m.MAX_TITLE),
        ("description", m.MAX_DESCRIPTION),
        ("footer", m.MAX_FOOTER),
        ("author", m.MAX_AUTHOR),
    ],
)
def test_felder_haben_die_grenzen_von_discord(field: str, limit: int) -> None:
    m.Embed.model_validate({field: "x" * limit})
    with pytest.raises(ValidationError):
        m.Embed.model_validate({field: "x" * (limit + 1)})


def test_text_und_felder_grenzen() -> None:
    m.Message(content="x" * m.MAX_CONTENT)
    with pytest.raises(ValidationError):
        m.Message(content="x" * (m.MAX_CONTENT + 1))
    field = {"name": "n", "value": "v"}
    m.Embed.model_validate({"fields": [field] * m.MAX_FIELDS})
    with pytest.raises(ValidationError):
        m.Embed.model_validate({"fields": [field] * (m.MAX_FIELDS + 1)})
    with pytest.raises(ValidationError):
        m.Embed.model_validate({"fields": [{"name": "", "value": "v"}]})
    with pytest.raises(ValidationError):
        m.Embed.model_validate({"fields": [{"name": "n", "value": "x" * (m.MAX_FIELD_VALUE + 1)}]})


def test_farbe_liegt_im_bereich() -> None:
    m.Embed.model_validate({"color": m.MAX_COLOR})
    for bad in (-1, m.MAX_COLOR + 1):
        with pytest.raises(ValidationError):
            m.Embed.model_validate({"color": bad})


def test_unbekannte_felder_werden_abgelehnt() -> None:
    with pytest.raises(ValidationError):
        m.Embed.model_validate({"title": "x", "video": "y"})


def test_gesamtgroesse_ist_begrenzt() -> None:
    ok = {"description": "x" * 4000, "fields": [{"name": "n", "value": "v" * 1000}]}
    assert norm(embed=ok).embed is not None  # 4000 + 1 + 1000 = 5001
    too_big = {"description": "x" * 4096, "fields": [{"name": "n", "value": "v" * 1024}] * 2}
    with pytest.raises(UserError) as error:
        norm(embed=too_big)
    assert error.value.key == "error.announce_too_long"


def test_gesamtgroesse_grenze_genau() -> None:
    at_limit: dict[str, object] = {
        "description": "x" * 4096,
        "fields": [{"name": "n", "value": "v" * 1024}],
    }
    # 4096 + 1 + 1024 = 5121 → ok; mit Titel und Fuss bis exakt 6000.
    at_limit["title"] = "t" * 256
    at_limit["footer"] = "f" * (m.MAX_TOTAL - 4096 - 1 - 1024 - 256)
    assert m.Embed.model_validate(at_limit).size == m.MAX_TOTAL
    norm(embed=at_limit)
    at_limit["footer"] = "f" * (m.MAX_TOTAL - 4096 - 1 - 1024 - 256 + 1)
    with pytest.raises(UserError):
        norm(embed=at_limit)


@pytest.mark.parametrize("field", ["image_url", "thumbnail_url"])
@pytest.mark.parametrize(
    "url",
    [
        "http://example.org/a.png",
        "javascript:alert(1)",
        "data:image/png;base64,AAAA",
        "https://exa mple.org/a.png",
        "https://",
        "//example.org/a.png",
    ],
)
def test_bild_adressen_muessen_https_sein(field: str, url: str) -> None:
    with pytest.raises(UserError) as error:
        norm(embed={"title": "x", field: url})
    assert error.value.key == "error.announce_url"
    assert error.value.params == {"field": field}


def test_gueltige_bild_adresse() -> None:
    embed = norm(embed={"image_url": "https://example.org/a.png"}).embed
    assert embed is not None
    assert embed.image_url == "https://example.org/a.png"


def test_embed_dict_enthaelt_nur_gesetztes() -> None:
    embed = m.Embed(
        title="T", color=0xFF0000, fields=[m.EmbedField(name="a", value="b", inline=True)]
    )
    assert m.embed_dict(embed) == {
        "type": "rich",
        "title": "T",
        "color": 0xFF0000,
        "fields": [{"name": "a", "value": "b", "inline": True}],
    }
    assert m.embed_dict(m.Embed(color=0)) == {"type": "rich", "color": 0}  # Schwarz ist eine Farbe
    full = m.embed_dict(
        m.Embed(
            description="d",
            author="a",
            footer="f",
            image_url="https://x.org/i.png",
            thumbnail_url="https://x.org/t.png",
        )
    )
    assert full["author"] == {"name": "a"}
    assert full["footer"] == {"text": "f"}
    assert full["image"] == {"url": "https://x.org/i.png"}
    assert full["thumbnail"] == {"url": "https://x.org/t.png"}
