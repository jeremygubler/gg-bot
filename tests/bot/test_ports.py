from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from ggbot.bot.ports import DiscordModerationPort
from ggbot.core.errors import UserError


def http_error(cls: type[discord.HTTPException]) -> discord.HTTPException:
    response = cast(
        Any, SimpleNamespace(status=403 if cls is discord.Forbidden else 404, reason="x")
    )
    return cls(response, "boom")


def make_port() -> tuple[DiscordModerationPort, MagicMock, MagicMock]:
    guild = MagicMock(spec=discord.Guild)
    guild.name = "Test"
    guild.me = SimpleNamespace(id=999)
    guild.kick = AsyncMock()
    guild.ban = AsyncMock()
    guild.unban = AsyncMock()
    guild.fetch_member = AsyncMock()
    guild.get_member = MagicMock(return_value=None)
    client = MagicMock(spec=discord.Client)
    client.get_user = MagicMock(return_value=None)
    client.fetch_user = AsyncMock()
    return DiscordModerationPort(client, guild), guild, client


async def test_kick_und_ban_geben_grund_und_loeschfenster_weiter() -> None:
    port, guild, _ = make_port()
    await port.kick(5, "grund")
    assert guild.kick.await_args.kwargs["reason"] == "grund"
    await port.ban(5, "grund", 86400)
    assert guild.ban.await_args.kwargs == {"reason": "grund", "delete_message_seconds": 86400}


@pytest.mark.parametrize("method", ["kick", "ban"])
async def test_forbidden_wird_zu_user_error(method: str) -> None:
    port, guild, _ = make_port()
    getattr(guild, method).side_effect = http_error(discord.Forbidden)
    with pytest.raises(UserError) as exc:
        await (port.kick(5, "x") if method == "kick" else port.ban(5, "x", 0))
    assert exc.value.key == "error.mod_forbidden"


@pytest.mark.parametrize("method", ["kick", "ban"])
async def test_not_found_wird_zu_user_error(method: str) -> None:
    port, guild, _ = make_port()
    getattr(guild, method).side_effect = http_error(discord.NotFound)
    with pytest.raises(UserError) as exc:
        await (port.kick(5, "x") if method == "kick" else port.ban(5, "x", 0))
    assert exc.value.key == "error.mod_target_not_found"


async def test_unban_meldet_nicht_gebannt_als_false() -> None:
    port, guild, _ = make_port()
    assert await port.unban(5, "x") is True
    guild.unban.side_effect = http_error(discord.NotFound)
    assert await port.unban(5, "x") is False
    guild.unban.side_effect = http_error(discord.Forbidden)
    with pytest.raises(UserError):
        await port.unban(5, "x")


async def test_timeout_holt_fehlendes_mitglied_per_rest() -> None:
    port, guild, _ = make_port()
    member = MagicMock(spec=discord.Member)
    member.timeout = AsyncMock()
    guild.fetch_member.return_value = member
    until = datetime(2026, 1, 1, tzinfo=UTC)
    await port.timeout(5, until, "x")
    member.timeout.assert_awaited_once_with(until, reason="x")


async def test_timeout_nicht_gefunden_und_verboten() -> None:
    port, guild, _ = make_port()
    guild.fetch_member.side_effect = http_error(discord.NotFound)
    with pytest.raises(UserError) as exc:
        await port.timeout(5, None, "x")
    assert exc.value.key == "error.mod_target_not_found"

    member = MagicMock(spec=discord.Member)
    member.timeout = AsyncMock(side_effect=http_error(discord.Forbidden))
    guild.fetch_member.side_effect = None
    guild.fetch_member.return_value = member
    with pytest.raises(UserError) as exc:
        await port.timeout(5, None, "x")
    assert exc.value.key == "error.mod_forbidden"


async def test_dm_zugestellt_oder_nicht() -> None:
    port, _, client = make_port()
    user = MagicMock(spec=discord.User)
    user.send = AsyncMock()
    client.fetch_user.return_value = user
    assert await port.dm(5, "hallo") is True
    user.send.assert_awaited_once_with("hallo")

    user.send.side_effect = http_error(discord.Forbidden)  # DMs geschlossen
    assert await port.dm(5, "hallo") is False
    client.fetch_user.side_effect = http_error(discord.NotFound)  # Konto gelöscht
    assert await port.dm(5, "hallo") is False


async def test_andere_http_fehler_werden_nicht_verschluckt() -> None:
    port, _, client = make_port()
    user = MagicMock(spec=discord.User)
    user.send = AsyncMock(
        side_effect=discord.HTTPException(
            cast(Any, SimpleNamespace(status=500, reason="x")), "kaputt"
        )
    )
    client.fetch_user.return_value = user
    with pytest.raises(discord.HTTPException):
        await port.dm(5, "hallo")
