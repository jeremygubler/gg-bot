import json

import pytest

from ggbot.core.logging import configure_logging, get_logger, mask, register_secret

# Aus Teilen gebaut: ein Literal im Token-Format würde GitHub Push Protection auslösen.
FAKE_TOKEN = ".".join(["MTIzNDU2Nzg5MDEyMzQ1Njc4OQ", "GabcDE", "abcdefghijklmnopqrstuvwxyz0"])


def test_discord_token_muster_wird_maskiert() -> None:
    assert FAKE_TOKEN not in mask(f"login mit {FAKE_TOKEN} fehlgeschlagen")


def test_passwort_in_url_wird_maskiert() -> None:
    text = mask("postgresql+asyncpg://ggbot:supergeheim@db:5432/ggbot")
    assert "supergeheim" not in text
    assert "ggbot:***@db" in text


def test_registriertes_secret_wird_maskiert() -> None:
    register_secret("mein-session-secret")
    assert mask("x mein-session-secret y") == "x *** y"


def test_json_ausgabe_maskiert_auch_verschachtelt(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO", "json")
    get_logger("t").info("event", detail={"tokens": [FAKE_TOKEN]})
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["event"] == "event"
    assert FAKE_TOKEN not in json.dumps(line)


def test_stacktrace_wird_maskiert(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO", "json")
    try:
        raise RuntimeError(f"kaputt {FAKE_TOKEN}")
    except RuntimeError:
        get_logger("t").exception("boom")
    assert FAKE_TOKEN not in capsys.readouterr().out
