from datetime import UTC, datetime

from ggbot.core.tickets.transcript import (
    MAX_CONTENT,
    MAX_MESSAGES,
    TranscriptMessage,
    render_transcript,
)

AT = datetime(2026, 1, 1, 12, 30, 5, tzinfo=UTC)


def render(messages: list[TranscriptMessage], **kw: object) -> str:
    args = {
        "title": "Ticket #1",
        "meta": "meta",
        "truncated_label": "gekürzt",
        "attachment_label": "Anhang",
        "embed_label": "Embeds",
    } | kw
    return render_transcript(messages, **args)  # type: ignore[arg-type]


def msg(content: str = "hallo", **kw: object) -> TranscriptMessage:
    return TranscriptMessage(author_id=1, author_name="Alice", at=AT, content=content, **kw)  # type: ignore[arg-type]


def test_grundaufbau() -> None:
    html = render([msg("Hallo Welt"), msg("zweite")])
    assert html.startswith("<!doctype html>")
    assert "Hallo Welt" in html
    assert html.index("Hallo Welt") < html.index("zweite")  # Reihenfolge bleibt
    assert "2026-01-01 12:30:05 UTC" in html
    assert "<title>Ticket #1</title>" in html


def test_inhalt_wird_maskiert() -> None:
    evil = "<script>alert(1)</script><img src=x onerror=alert(2)>\"'&"
    html = render([msg(evil)])
    assert "<script>" not in html
    assert "<img" not in html
    assert "&lt;script&gt;" in html


def test_name_titel_und_meta_werden_maskiert() -> None:
    html = render([TranscriptMessage(1, "<b>Eve</b>", AT, "x")], title="<i>t</i>", meta="<u>m</u>")
    for tag in ("<b>", "<i>", "<u>"):
        assert tag not in html.split("</style>")[1]


def test_anhang_links_nur_http() -> None:
    good = msg(attachments=(("bild.png", "https://cdn.example/a.png?x=1&y=2"),))
    html = render([good])
    assert 'href="https://cdn.example/a.png?x=1&amp;y=2"' in html
    assert 'rel="noopener noreferrer"' in html
    for bad_url in (
        "javascript:alert(1)",
        "data:text/html,<script>1</script>",
        "file:///etc/passwd",
        "//evil.example/x",
        "",
    ):
        html = render([msg(attachments=(("x.png", bad_url),))])
        assert "href=" not in html, bad_url
        assert "x.png" in html  # der Dateiname bleibt als Text


def test_dateiname_wird_maskiert() -> None:
    html = render([msg(attachments=(('"><script>.png', "https://x.example/a"),))])
    assert "<script>" not in html


def test_url_attribut_kann_nicht_ausbrechen() -> None:
    html = render([msg(attachments=(("a", 'https://x.example/"onmouseover="alert(1)'),))])
    assert '"onmouseover=' not in html
    assert "&quot;onmouseover=&quot;" in html


def test_embeds_werden_gezaehlt() -> None:
    assert "Embeds: 3" in render([msg(embed_count=3)])
    assert "Embeds:" not in render([msg()])


def test_seite_laedt_nichts_fremdes() -> None:
    html = render([msg()])
    assert "default-src 'none'" in html
    assert "<script" not in html
    assert "<link" not in html


def test_grenzen() -> None:
    long = render([msg("x" * (MAX_CONTENT + 500))])
    assert "x" * MAX_CONTENT in long
    assert "x" * (MAX_CONTENT + 1) not in long
    many = render([msg(f"n{i}") for i in range(MAX_MESSAGES + 10)])
    assert f"n{MAX_MESSAGES - 1}<" in many
    assert f"n{MAX_MESSAGES}<" not in many


def test_hinweis_bei_kuerzung() -> None:
    assert "gekürzt" in render([msg()], truncated=True)
    assert "gekürzt" not in render([msg()])


def test_leeres_transkript() -> None:
    html = render([])
    assert "</html>" in html
    assert 'class="msg"' not in html


def test_zeilenumbrueche_bleiben_erhalten() -> None:
    assert "white-space:pre-wrap" in render([msg("a\nb")])
