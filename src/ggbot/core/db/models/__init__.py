"""Alle Modelle hier importieren, damit Alembic sie über `Base.metadata` findet."""

from ggbot.core.db.models.audit import DashboardAudit
from ggbot.core.db.models.automod import AutomodHit
from ggbot.core.db.models.guild import Guild, GuildSetting
from ggbot.core.db.models.jobs import ScheduledJob
from ggbot.core.db.models.leveling import RankCardStyle, XpMember
from ggbot.core.db.models.moderation import ChannelLock, ModCase, ModCaseCounter, ModNote
from ggbot.core.db.models.notify import NotifySubscription
from ggbot.core.db.models.rolepanels import RolePanel, RolePanelItem
from ggbot.core.db.models.tickets import (
    Ticket,
    TicketCategory,
    TicketCounter,
    TicketFeedback,
    TicketPanel,
    TicketTranscript,
)

__all__ = [
    "AutomodHit",
    "ChannelLock",
    "DashboardAudit",
    "Guild",
    "GuildSetting",
    "ModCase",
    "ModCaseCounter",
    "ModNote",
    "NotifySubscription",
    "RankCardStyle",
    "RolePanel",
    "RolePanelItem",
    "ScheduledJob",
    "Ticket",
    "TicketCategory",
    "TicketCounter",
    "TicketFeedback",
    "TicketPanel",
    "TicketTranscript",
    "XpMember",
]
