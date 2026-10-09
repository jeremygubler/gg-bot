"""Konfiguration aus der Umgebung. Secrets sind SecretStr und landen nie im Log."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    discord_token: SecretStr = SecretStr("")
    database_url: str = "postgresql+asyncpg://ggbot:ggbot@localhost:5432/ggbot"
    redis_url: str = "redis://localhost:6379/0"

    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"

    default_locale: Literal["de", "en"] = "de"

    # Sharding: leer = discord.py entscheidet (ein Prozess, alle Shards).
    shard_count: int | None = Field(default=None, ge=1)
    shard_ids: list[int] | None = None

    # Privilegierter Intent: erst einschalten, wenn er im Developer Portal freigegeben ist.
    enable_message_content_intent: bool = False

    # Privilegierter Intent für Beitritts-Regeln (Anti-Raid, Konto-Alter), ebenfalls nach Freigabe.
    enable_members_intent: bool = False

    # Nachrichten, die der Bot im Speicher hält. Nur daraus zeigt das Event-Log den Inhalt
    # gelöschter oder bearbeiteter Nachrichten; in der Datenbank wird nichts davon gespeichert.
    message_cache_size: int = Field(default=5000, ge=100, le=100_000)

    # Nur für die Entwicklung: Slash-Commands sofort auf einer Test-Guild statt global.
    dev_guild_id: int | None = None

    @field_validator("database_url")
    @classmethod
    def _async_driver(cls, value: str) -> str:
        # Ein "postgresql://" würde den synchronen Treiber ziehen und erst beim
        # ersten Query scheitern. Lieber beim Start.
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError("DATABASE_URL muss mit postgresql+asyncpg:// beginnen")
        return value

    @field_validator("shard_ids", mode="before")
    @classmethod
    def _split_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return [int(part) for part in value.split(",") if part.strip()]
        return value

    def require_token(self) -> str:
        token = self.discord_token.get_secret_value()
        if not token:
            raise RuntimeError("DISCORD_TOKEN ist nicht gesetzt")
        return token


@lru_cache
def get_config() -> AppConfig:
    return AppConfig()
