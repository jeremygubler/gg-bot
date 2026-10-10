from dataclasses import dataclass

from redis.asyncio import Redis

from ggbot.core.bulk import BulkGate
from ggbot.core.config import AppConfig
from ggbot.core.db.engine import Database
from ggbot.core.ratelimit import Cooldown
from ggbot.core.scheduler import Scheduler
from ggbot.core.services.automod import AutomodService
from ggbot.core.services.channels import ChannelLockService
from ggbot.core.services.eventlog import EventLogService
from ggbot.core.services.guilds import GuildService
from ggbot.core.services.leveling import LevelingService
from ggbot.core.services.moderation import ModerationService
from ggbot.core.services.rolepanels import RolePanelService
from ggbot.core.services.welcome import WelcomeService
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
    moderation: ModerationService
    channel_locks: ChannelLockService
    bulk_gate: BulkGate
    automod: AutomodService
    eventlog: EventLogService
    leveling: LevelingService
    rolepanels: RolePanelService
    welcome: WelcomeService

    @classmethod
    def build(cls, config: AppConfig, db: Database, redis: Redis) -> "Services":
        guilds = GuildService(db, redis, default_locale=config.default_locale)
        settings = SettingsStore(db, redis)
        scheduler = Scheduler(db)
        return cls(
            config=config,
            db=db,
            redis=redis,
            guilds=guilds,
            settings=settings,
            scheduler=scheduler,
            cooldown=Cooldown(redis),
            moderation=ModerationService(db, settings, scheduler, guilds),
            channel_locks=ChannelLockService(db),
            bulk_gate=BulkGate(),
            automod=AutomodService(db, settings, redis),
            eventlog=EventLogService(settings),
            leveling=LevelingService(db, settings, redis),
            rolepanels=RolePanelService(db),
            welcome=WelcomeService(settings, redis),
        )
