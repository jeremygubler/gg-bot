"""Alle Modelle hier importieren, damit Alembic sie über `Base.metadata` findet."""

from ggbot.core.db.models.announcements import Announcement
from ggbot.core.db.models.audit import DashboardAudit
from ggbot.core.db.models.automod import AutomodHit
from ggbot.core.db.models.channels import StickyMessage, TempVoiceChannel
from ggbot.core.db.models.engagement import (
    Birthday,
    CustomCommand,
    Giveaway,
    GiveawayEntry,
    Reminder,
    ScheduledMessage,
    StarboardEntry,
    StarboardVote,
    StatChannel,
)
from ggbot.core.db.models.guild import Guild, GuildSetting
from ggbot.core.db.models.jobs import ScheduledJob
from ggbot.core.db.models.leveling import RankCardStyle, XpMember
from ggbot.core.db.models.moderation import ChannelLock, ModCase, ModCaseCounter, ModNote
from ggbot.core.db.models.notify import NotifySubscription
from ggbot.core.db.models.rolepanels import RolePanel, RolePanelItem
from ggbot.core.db.models.suggestions import Suggestion, SuggestionCounter, SuggestionVote
from ggbot.core.db.models.tickets import (
    Ticket,
    TicketCategory,
    TicketCounter,
    TicketFeedback,
    TicketPanel,
    TicketTranscript,
)

__all__ = [
    "Announcement",
    "AutomodHit",
    "Birthday",
    "ChannelLock",
    "CustomCommand",
    "DashboardAudit",
    "Giveaway",
    "GiveawayEntry",
    "Guild",
    "GuildSetting",
    "ModCase",
    "ModCaseCounter",
    "ModNote",
    "NotifySubscription",
    "RankCardStyle",
    "Reminder",
    "RolePanel",
    "RolePanelItem",
    "ScheduledJob",
    "ScheduledMessage",
    "StarboardEntry",
    "StarboardVote",
    "StatChannel",
    "StickyMessage",
    "Suggestion",
    "SuggestionCounter",
    "SuggestionVote",
    "TempVoiceChannel",
    "Ticket",
    "TicketCategory",
    "TicketCounter",
    "TicketFeedback",
    "TicketPanel",
    "TicketTranscript",
    "XpMember",
]
