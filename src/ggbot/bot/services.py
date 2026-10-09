from dataclasses import dataclass

from redis.asyncio import Redis

from ggbot.core.config import AppConfig
from ggbot.core.db.engine import Database
from ggbot.core.ratelimit import Cooldown
from ggbot.core.scheduler import Scheduler
from ggbot.core.services.guilds import GuildService
from ggbot.core.settings.store import SettingsStore


@dataclass
class Services:
    """Alles, was Cogs von aussen brauchen. Wird einmal gebaut und durchgereicht."""

    config: AppConfig
    db: Database
    redis: Redis
    guilds: GuildService
    settings: SettingsStore
    scheduler: Scheduler
    cooldown: Cooldown

    @classmethod
    def build(cls, config: AppConfig, db: Database, redis: Redis) -> "Services":
        return cls(
            config=config,
            db=db,
            redis=redis,
            guilds=GuildService(db, redis, default_locale=config.default_locale),
            settings=SettingsStore(db, redis),
            scheduler=Scheduler(db),
            cooldown=Cooldown(redis),
        )
