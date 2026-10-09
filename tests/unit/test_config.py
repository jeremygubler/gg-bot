import pytest
from pydantic import ValidationError

from ggbot.core.config import AppConfig


def test_sync_driver_wird_abgelehnt() -> None:
    with pytest.raises(ValidationError):
        AppConfig(database_url="postgresql://u:p@h/db")


def test_shard_ids_aus_kommaliste(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHARD_IDS", "0, 2,3")
    assert AppConfig().shard_ids == [0, 2, 3]


def test_token_fehlt_ist_laut() -> None:
    with pytest.raises(RuntimeError):
        AppConfig(discord_token="").require_token()


def test_token_nie_in_repr() -> None:
    config = AppConfig(discord_token="geheim-geheim")
    assert "geheim" not in repr(config)
    assert "geheim" not in config.model_dump_json()
