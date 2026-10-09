from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import discord

from ggbot.bot.log_diff import (
    channel_changes,
    permission_names,
    role_changes,
    snapshot_of,
    user_ref,
)
from tests.fakes import FakeUser


def role(**kw: object) -> MagicMock:
    values: dict[str, Any] = {
        "name": "Mod",
        "color": discord.Color(0),
        "permissions": discord.Permissions.none(),
        "hoist": False,
        "mentionable": False,
        "position": 3,
    }
    values.update(kw)
    mock = MagicMock(spec=discord.Role)
    for key, value in values.items():
        setattr(mock, key, value)
    return mock


def test_berechtigungsnamen() -> None:
    names = permission_names(discord.Permissions(kick_members=True, ban_members=True).value)
    assert names == {"kick_members", "ban_members"}


def test_rolle_ohne_aenderung_oder_nur_umsortiert() -> None:
    assert role_changes(role(), role()) == []
    assert role_changes(role(position=1), role(position=9)) == []


def test_rolle_alle_aenderungen() -> None:
    before = role()
    after = role(
        name="Admin",
        color=discord.Color(0xFF0000),
        permissions=discord.Permissions(ban_members=True),
        hoist=True,
        mentionable=True,
    )
    changes = {c.label.split(".")[-1]: (c.before, c.after) for c in role_changes(before, after)}
    assert changes["name"] == ("Mod", "Admin")
    assert changes["color"] == ("#000000", "#ff0000")
    assert changes["permissions"] == ("−–", "+ban_members")
    assert changes["hoist"] == ("False", "True")
    assert changes["mentionable"] == ("False", "True")


def test_rolle_berechtigungen_hinzu_und_weg() -> None:
    before = role(permissions=discord.Permissions(kick_members=True, ban_members=True))
    after = role(permissions=discord.Permissions(ban_members=True, manage_guild=True))
    (change,) = role_changes(before, after)
    assert change.before == "−kick_members"
    assert change.after == "+manage_guild"


def channel(**kw: object) -> MagicMock:
    values: dict[str, Any] = {
        "name": "allgemein",
        "topic": None,
        "slowmode_delay": 0,
        "nsfw": False,
        "category": None,
        "position": 1,
    }
    values.update(kw)
    mock = MagicMock(spec=discord.TextChannel)
    for key, value in values.items():
        setattr(mock, key, value)
    return mock


def test_kanal_position_zaehlt_nicht() -> None:
    assert channel_changes(channel(position=1), channel(position=7)) == []


def test_kanal_aenderungen() -> None:
    before = channel()
    after = channel(
        name="news",
        topic="Neues",
        slowmode_delay=10,
        nsfw=True,
        category=SimpleNamespace(name="Info"),
    )
    changes = {c.label.split(".")[-1]: (c.before, c.after) for c in channel_changes(before, after)}
    assert changes["name"] == ("allgemein", "news")
    assert changes["topic"] == ("–", "Neues")
    assert changes["slowmode"] == ("0", "10")
    assert changes["nsfw"] == ("False", "True")
    assert changes["category"] == ("–", "Info")


def test_nachricht_wird_zum_schnappschuss() -> None:
    author = FakeUser(1)
    message = MagicMock(spec=discord.Message)
    message.id = 5
    message.channel = SimpleNamespace(id=10)
    message.author = author
    message.content = "hallo"
    message.attachments = [SimpleNamespace(url="https://cdn/a.png")]
    message.created_at = None
    snapshot = snapshot_of(message)
    assert (snapshot.id, snapshot.channel_id, snapshot.content) == (5, 10, "hallo")
    assert snapshot.attachments == ("https://cdn/a.png",)
    assert snapshot.author is not None
    assert (snapshot.author.id, snapshot.author.name) == (1, "alice")
    assert user_ref(author).bot is False  # type: ignore[arg-type]
