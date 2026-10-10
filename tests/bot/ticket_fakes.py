"""Gemeinsame Fake-Discord-Objekte für die Ticket-Tests."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord

from ggbot.bot.client import GGBot
from ggbot.core.tickets.rules import DISCORD_EPOCH_MS
from tests.bot.test_cog_flows import GUILD_ID, make_guild, make_interaction, make_member
from tests.bot.test_panels import role

SUPPORT = 9
LOG = 71
PARENT = 72
CREATOR = 500
AGENT = 600
STRANGER = 700
NEXT_CHANNEL = [8000]


def snowflake_at(when: datetime) -> int:
    return (int(when.timestamp() * 1000) - DISCORD_EPOCH_MS) << 22


def fake_message(
    content: str, *, author: str = "Alice", at: datetime | None = None, **kw: Any
) -> Any:
    return SimpleNamespace(
        author=SimpleNamespace(id=1, display_name=author, bot=False),
        created_at=at or datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        content=content,
        attachments=kw.get("attachments", []),
        embeds=kw.get("embeds", []),
    )


def text_channel(channel_id: int | None = None, history: list[Any] | None = None) -> MagicMock:
    if channel_id is None:
        NEXT_CHANNEL[0] += 1
        channel_id = NEXT_CHANNEL[0]
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    channel.mention = f"<#{channel_id}>"
    channel.send = AsyncMock()
    channel.delete = AsyncMock()
    channel.set_permissions = AsyncMock()
    channel.last_message_id = None
    channel.permissions_for = MagicMock(return_value=discord.Permissions.all())
    messages = history if history is not None else []

    async def hist(limit: int | None = None, oldest_first: bool = False) -> AsyncIterator[Any]:
        for message in messages[:limit]:
            yield message

    channel.history = hist
    channel.messages = messages
    channel.fetch_message = AsyncMock()
    return channel


def category_channel() -> MagicMock:
    mock = MagicMock(spec=discord.CategoryChannel)
    mock.id = PARENT
    return mock


def make_world(bot: GGBot, *extra_roles: MagicMock) -> MagicMock:
    guild = make_guild(bot_position=10)
    guild.name = "Testserver"
    log = text_channel(LOG)
    parent = category_channel()
    channels: dict[int, Any] = {LOG: log, PARENT: parent}
    guild.channels_by_id = channels
    guild.log_channel = log
    guild.get_channel = MagicMock(side_effect=lambda cid: channels.get(cid))
    roles = {r.id: r for r in (role(SUPPORT, position=3), *extra_roles)}
    guild.get_role = MagicMock(side_effect=roles.get)
    guild.default_role.is_default = MagicMock(return_value=True)
    guild.me.guild_permissions = discord.Permissions(manage_channels=True, manage_roles=True)
    guild.created = []

    async def create(name: str, **kwargs: Any) -> MagicMock:
        channel = text_channel()
        channel.name = name
        channel.create_kwargs = kwargs
        channels[channel.id] = channel
        guild.created.append(channel)
        return channel

    guild.create_text_channel = AsyncMock(side_effect=create)
    bot.get_guild = MagicMock(return_value=guild)  # type: ignore[method-assign]
    bot._connection.user = SimpleNamespace(id=999)  # type: ignore[assignment]
    return guild


def member(guild: MagicMock, user_id: int, *role_ids: int, manager: bool = False) -> MagicMock:
    m = make_member(user_id, 1, guild)
    m.bot = False
    m.name = f"user{user_id}"
    m.display_name = f"User{user_id}"
    m.roles = [SimpleNamespace(id=r) for r in role_ids]
    m.guild_permissions = discord.Permissions(manage_guild=manager)
    m.send = AsyncMock()
    guild.members_by_id[user_id] = m
    return m


def click(
    bot: GGBot, guild: MagicMock, who: MagicMock, channel: MagicMock | None = None
) -> MagicMock:
    interaction = make_interaction(bot, guild, who, channel)
    interaction.channel_id = channel.id if channel is not None else None
    interaction.response.send_modal = AsyncMock()
    interaction.response.edit_message = AsyncMock()
    return interaction


def bot_user(bot: GGBot) -> None:
    bot._connection.user = SimpleNamespace(id=999)  # type: ignore[assignment]


__all__ = [
    "AGENT",
    "CREATOR",
    "GUILD_ID",
    "LOG",
    "PARENT",
    "STRANGER",
    "SUPPORT",
    "bot_user",
    "click",
    "fake_message",
    "make_world",
    "member",
    "snowflake_at",
    "text_channel",
]
