from dataclasses import dataclass

import httpx
from redis.asyncio import Redis

from ggbot.core.bulk import BulkGate
from ggbot.core.config import AppConfig
from ggbot.core.db.engine import Database
from ggbot.core.notify.twitch import TwitchClient
from ggbot.core.notify.youtube import YouTubeClient
from ggbot.core.ratelimit import Cooldown
from ggbot.core.scheduler import Scheduler
from ggbot.core.services.automod import AutomodService
from ggbot.core.services.channels import ChannelLockService
from ggbot.core.services.community import (
    BirthdayService,
    ScheduleService,
    StarboardService,
    StatChannelService,
)
from ggbot.core.services.engagement import EngagementService
from ggbot.core.services.eventlog import EventLogService
from ggbot.core.services.guilds import GuildService
from ggbot.core.services.leveling import LevelingService
from ggbot.core.services.moderation import ModerationService
from ggbot.core.services.notify import NotifyService
from ggbot.core.services.rolepanels import RolePanelService
from ggbot.core.services.tickets import TicketService
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
    tickets: TicketService
    notify: NotifyService
    engagement: EngagementService
    starboard: StarboardService
    birthdays: BirthdayService
    statchannels: StatChannelService
    schedules: ScheduleService
    http: httpx.AsyncClient
    twitch: TwitchClient
    youtube: YouTubeClient

    @classmethod
    def build(cls, config: AppConfig, db: Database, redis: Redis) -> "Services":
        guilds = GuildService(db, redis, default_locale=config.default_locale)
        settings = SettingsStore(db, redis)
        scheduler = Scheduler(db)
        # Ein Client für alle Anbieter; das kurze Zeitlimit hält hängende Anbieter fern.
        http = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0),
            headers={"User-Agent": "GG-Bot (+https://github.com/jeremygubler/gg-bot)"},
        )
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
            tickets=TicketService(db, settings, redis),
            notify=NotifyService(db),
            engagement=EngagementService(db, scheduler),
            starboard=StarboardService(db, settings),
            birthdays=BirthdayService(db, settings),
            statchannels=StatChannelService(db),
            schedules=ScheduleService(db, scheduler),
            http=http,
            twitch=TwitchClient(
                http, config.twitch_client_id, config.twitch_client_secret.get_secret_value()
            ),
            youtube=YouTubeClient(http),
        )
