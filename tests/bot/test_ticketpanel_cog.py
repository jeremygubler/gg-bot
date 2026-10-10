"""/ticketpanel: Panels, Kategorien, Einstellungen."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from redis.asyncio import Redis

from ggbot.bot import tickets as tk
from ggbot.bot.client import GGBot
from ggbot.bot.cogs.ticketpanel import TicketPanelCog
from ggbot.bot.services import Services
from ggbot.core.config import AppConfig
from ggbot.core.db.engine import Database
from ggbot.core.errors import UserError
from tests.bot.test_cog_flows import sent_text
from tests.bot.test_panels import role
from tests.bot.ticket_fakes import (
    GUILD_ID,
    PARENT,
    SUPPORT,
    click,
    make_world,
    member,
    text_channel,
)
from tests.fakes import invoke

ADMIN = 11


@pytest.fixture
async def bot(db: Database, redis: Redis) -> GGBot:
    return GGBot(Services.build(AppConfig(), db, redis))


def admin(guild: MagicMock) -> MagicMock:
    return member(guild, ADMIN, manager=True)


async def panel_with_category(bot: GGBot) -> int:
    service = bot.services.tickets
    panel = await service.create_panel(GUILD_ID, title="Support")
    await service.add_category(
        GUILD_ID, panel.id, name="Hilfe", support_role_id=SUPPORT, emoji="🛟"
    )
    return panel.id


async def test_panel_anlegen_und_auflisten(bot: GGBot) -> None:
    cog = TicketPanelCog(bot)
    guild = make_world(bot)
    interaction = click(bot, guild, admin(guild))
    await invoke(cog.create, cog, interaction, "Support", "Beschreibung")
    panel = (await bot.services.tickets.list_panels(GUILD_ID))[0]
    assert (panel.title, panel.description) == ("Support", "Beschreibung")
    assert f"#{panel.id}" in sent_text(interaction.response.send_message)

    listing = click(bot, guild, admin(guild))
    await invoke(cog.list_, cog, listing)
    assert "Support" in sent_text(listing.response.send_message)
    assert listing.response.send_message.await_args.kwargs["allowed_mentions"].roles is False


async def test_liste_leer(bot: GGBot) -> None:
    cog = TicketPanelCog(bot)
    guild = make_world(bot)
    interaction = click(bot, guild, admin(guild))
    await invoke(cog.list_, cog, interaction)
    assert "/ticketpanel create" in sent_text(interaction.response.send_message)


async def test_kategorie_hinzufuegen(bot: GGBot) -> None:
    cog = TicketPanelCog(bot)
    guild = make_world(bot, role(20, position=2))
    panel = await bot.services.tickets.create_panel(GUILD_ID, title="p")
    parent = guild.channels_by_id[PARENT]
    await invoke(
        cog.category_add,
        cog,
        click(bot, guild, admin(guild)),
        panel.id,
        "Bug melden",
        guild.get_role(20),
        "🐛",
        "Fehler melden",
        parent,
        "Hi {user}",
        "Was?; Wo?",
    )
    cat = (await bot.services.tickets.get_panel(GUILD_ID, panel.id)).categories[0]
    assert (cat.name, cat.emoji, cat.parent_id, cat.support_role_ids) == (
        "Bug melden",
        "🐛",
        PARENT,
        (20,),
    )
    assert (cat.welcome_message, cat.questions) == ("Hi {user}", ("Was?", "Wo?"))


@pytest.mark.parametrize(
    ("kwargs", "key"),
    [
        ({"emoji": "kein emoji"}, "error.panel_emoji_invalid"),
        ({"questions": "a;b;c;d;e;f"}, "error.tcat_questions"),
        ({"questions": "x" * 46}, "error.tcat_questions"),
        ({"welcome": "{level}"}, "error.tcat_welcome_invalid"),
    ],
)
async def test_kategorie_ungueltig(bot: GGBot, kwargs: dict[str, Any], key: str) -> None:
    cog = TicketPanelCog(bot)
    guild = make_world(bot)
    panel = await bot.services.tickets.create_panel(GUILD_ID, title="p")
    args = {
        "emoji": None,
        "description": None,
        "parent": None,
        "welcome": None,
        "questions": None,
    } | kwargs
    with pytest.raises(UserError) as exc:
        await invoke(
            cog.category_add,
            cog,
            click(bot, guild, admin(guild)),
            panel.id,
            "K",
            guild.get_role(SUPPORT),
            *args.values(),
        )
    assert exc.value.key == key
    assert (await bot.services.tickets.get_panel(GUILD_ID, panel.id)).categories == ()


async def test_everyone_ist_keine_support_rolle(bot: GGBot) -> None:
    cog = TicketPanelCog(bot)
    guild = make_world(bot)
    panel_id = await panel_with_category(bot)
    everyone = guild.default_role
    with pytest.raises(UserError) as exc:
        await invoke(
            cog.category_add,
            cog,
            click(bot, guild, admin(guild)),
            panel_id,
            "Neu",
            everyone,
            None,
            None,
            None,
            None,
            None,
        )
    assert exc.value.key == "error.tcat_role_everyone"
    with pytest.raises(UserError) as exc:
        await invoke(
            cog.role_add, cog, click(bot, guild, admin(guild)), panel_id, "Hilfe", everyone
        )
    assert exc.value.key == "error.tcat_role_everyone"


async def test_rollen_der_kategorie(bot: GGBot) -> None:
    cog = TicketPanelCog(bot)
    guild = make_world(bot, role(20, position=2))
    panel_id = await panel_with_category(bot)
    await invoke(
        cog.role_add, cog, click(bot, guild, admin(guild)), panel_id, "hilfe", guild.get_role(20)
    )
    cat = (await bot.services.tickets.get_panel(GUILD_ID, panel_id)).categories[0]
    assert cat.support_role_ids == (SUPPORT, 20)
    await invoke(
        cog.role_remove,
        cog,
        click(bot, guild, admin(guild)),
        panel_id,
        "Hilfe",
        guild.get_role(SUPPORT),
    )
    assert (await bot.services.tickets.get_panel(GUILD_ID, panel_id)).categories[
        0
    ].support_role_ids == (20,)
    with pytest.raises(UserError) as exc:
        await invoke(
            cog.role_remove,
            cog,
            click(bot, guild, admin(guild)),
            panel_id,
            "Hilfe",
            guild.get_role(20),
        )
    assert exc.value.key == "error.tcat_last_role"


async def test_veroeffentlichen(bot: GGBot) -> None:
    cog = TicketPanelCog(bot)
    guild = make_world(bot)
    channel = text_channel(70)
    channel.send = AsyncMock(return_value=SimpleNamespace(id=900))
    panel_id = await panel_with_category(bot)
    interaction = click(bot, guild, admin(guild))
    await invoke(cog.publish, cog, interaction, panel_id, channel)
    kwargs = channel.send.await_args.kwargs
    assert kwargs["embed"].title == "Support"
    assert "Hilfe" in kwargs["embed"].description
    child = kwargs["view"].children[0]
    assert child.custom_id == tk.open_id(
        (await bot.services.tickets.get_panel(GUILD_ID, panel_id)).categories[0].id
    )
    assert kwargs["allowed_mentions"].roles is False
    stored = await bot.services.tickets.get_panel(GUILD_ID, panel_id)
    assert (stored.channel_id, stored.message_id) == (70, 900)


async def test_erneut_veroeffentlichen_ersetzt_die_alte_nachricht(bot: GGBot) -> None:
    cog = TicketPanelCog(bot)
    guild = make_world(bot)
    channel = text_channel(70)
    old = MagicMock()
    old.delete = AsyncMock()
    channel.fetch_message = AsyncMock(return_value=old)
    channel.send = AsyncMock(return_value=SimpleNamespace(id=901))
    guild.channels_by_id[70] = channel
    panel_id = await panel_with_category(bot)
    await bot.services.tickets.set_panel_message(GUILD_ID, panel_id, 70, 900)
    await invoke(cog.publish, cog, click(bot, guild, admin(guild)), panel_id, channel)
    old.delete.assert_awaited_once()
    assert (await bot.services.tickets.get_panel(GUILD_ID, panel_id)).message_id == 901


@pytest.mark.parametrize("case", ["empty", "channel_perms", "manage_channels", "manage_roles"])
async def test_veroeffentlichen_fehler(bot: GGBot, case: str) -> None:
    cog = TicketPanelCog(bot)
    guild = make_world(bot)
    channel = text_channel(70)
    if case == "empty":
        panel_id = (await bot.services.tickets.create_panel(GUILD_ID, title="leer")).id
        key = "error.tpanel_empty"
    else:
        panel_id = await panel_with_category(bot)
        key = "error.missing_bot_permissions"
        if case == "channel_perms":
            channel.permissions_for = MagicMock(return_value=discord.Permissions(view_channel=True))
        elif case == "manage_channels":
            guild.me.guild_permissions = discord.Permissions(manage_roles=True)
        else:
            guild.me.guild_permissions = discord.Permissions(manage_channels=True)
    with pytest.raises(UserError) as exc:
        await invoke(cog.publish, cog, click(bot, guild, admin(guild)), panel_id, channel)
    assert exc.value.key == key
    channel.send.assert_not_awaited()


async def test_loeschen_entfernt_nachricht(bot: GGBot) -> None:
    cog = TicketPanelCog(bot)
    guild = make_world(bot)
    channel = text_channel(70)
    message = MagicMock()
    message.delete = AsyncMock()
    channel.fetch_message = AsyncMock(return_value=message)
    guild.channels_by_id[70] = channel
    panel_id = await panel_with_category(bot)
    await bot.services.tickets.set_panel_message(GUILD_ID, panel_id, 70, 900)
    await invoke(cog.delete, cog, click(bot, guild, admin(guild)), panel_id)
    message.delete.assert_awaited_once()
    assert await bot.services.tickets.list_panels(GUILD_ID) == []


async def test_kategorie_aendern_aktualisiert_die_nachricht(bot: GGBot) -> None:
    cog = TicketPanelCog(bot)
    guild = make_world(bot, role(20, position=2))
    channel = text_channel(70)
    message = MagicMock()
    message.edit = AsyncMock()
    channel.fetch_message = AsyncMock(return_value=message)
    guild.channels_by_id[70] = channel
    panel_id = await panel_with_category(bot)
    await bot.services.tickets.set_panel_message(GUILD_ID, panel_id, 70, 900)
    await invoke(
        cog.category_add,
        cog,
        click(bot, guild, admin(guild)),
        panel_id,
        "Bug",
        guild.get_role(20),
        None,
        None,
        None,
        None,
        None,
    )
    assert len(message.edit.await_args.kwargs["view"].children) == 2
    await invoke(cog.category_remove, cog, click(bot, guild, admin(guild)), panel_id, "Bug")
    assert len(message.edit.await_args.kwargs["view"].children) == 1


async def test_fremdes_panel(bot: GGBot) -> None:
    cog = TicketPanelCog(bot)
    guild = make_world(bot)
    other = await bot.services.tickets.create_panel(GUILD_ID + 1, title="fremd")
    channel = text_channel(70)
    for call in (
        invoke(cog.publish, cog, click(bot, guild, admin(guild)), other.id, channel),
        invoke(cog.delete, cog, click(bot, guild, admin(guild)), other.id),
        invoke(cog.category_remove, cog, click(bot, guild, admin(guild)), other.id, "x"),
    ):
        with pytest.raises(UserError) as exc:
            await call
        assert exc.value.key == "error.tpanel_not_found"


async def test_einstellungen(bot: GGBot) -> None:
    cog = TicketPanelCog(bot)
    guild = make_world(bot)
    log = guild.log_channel
    interaction = click(bot, guild, admin(guild))
    await invoke(
        cog.settings,
        cog,
        interaction,
        log,
        2,
        48,
        15,
        False,
        False,
        False,
        False,
        30,
        "{category}-{number}",
    )
    s = await bot.services.tickets.settings(GUILD_ID)
    assert (s.log_channel_id, s.max_open_per_user, s.auto_close_hours, s.close_delay_seconds) == (
        log.id,
        2,
        48,
        15,
    )
    assert (s.feedback, s.dm_transcript, s.creators_may_close, s.ping_support) == (
        False,
        False,
        False,
        False,
    )
    assert (s.retention_days, s.name_template) == (30, "{category}-{number}")
    # Einzelne Änderung lässt den Rest unberührt
    await invoke(
        cog.settings,
        cog,
        click(bot, guild, admin(guild)),
        None,
        5,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
    )
    s = await bot.services.tickets.settings(GUILD_ID)
    assert (s.max_open_per_user, s.log_channel_id, s.retention_days) == (5, log.id, 30)
    # false ist eine Änderung, kein "nicht angegeben"
    await invoke(
        cog.settings,
        cog,
        click(bot, guild, admin(guild)),
        None,
        None,
        None,
        None,
        True,
        None,
        None,
        None,
        None,
        None,
    )
    assert (await bot.services.tickets.settings(GUILD_ID)).feedback is True


async def test_einstellungen_fehler(bot: GGBot) -> None:
    cog = TicketPanelCog(bot)
    guild = make_world(bot)
    with pytest.raises(UserError):
        await invoke(cog.settings, cog, click(bot, guild, admin(guild)), *([None] * 10))
    with pytest.raises(UserError) as exc:
        await invoke(
            cog.settings,
            cog,
            click(bot, guild, admin(guild)),
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            "{level}",
        )
    assert exc.value.key == "error.ticket_settings_invalid"
    assert (await bot.services.tickets.settings(GUILD_ID)).name_template == "ticket-{number}"


async def test_status(bot: GGBot) -> None:
    cog = TicketPanelCog(bot)
    guild = make_world(bot)
    interaction = click(bot, guild, admin(guild))
    await invoke(cog.status, cog, interaction)
    text = sent_text(interaction.response.send_message)
    assert "ticket-{number}" in text
    assert "0.0" in text
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True


async def test_autocomplete(bot: GGBot) -> None:
    cog = TicketPanelCog(bot)
    guild = make_world(bot)
    panel_id = await panel_with_category(bot)
    await bot.services.tickets.create_panel(GUILD_ID + 1, title="Support fremd")
    interaction = click(bot, guild, admin(guild))
    assert [c.value for c in await cog._panel_choices(interaction, "supp")] == [panel_id]
    assert await cog._panel_choices(interaction, "zzz") == []
    interaction.namespace = SimpleNamespace(panel=panel_id)
    assert [c.value for c in await cog._category_choices(interaction, "hi")] == ["Hilfe"]
    interaction.namespace = SimpleNamespace(panel=panel_id + 999)
    assert await cog._category_choices(interaction, "") == []
    interaction.namespace = SimpleNamespace()
    assert await cog._category_choices(interaction, "") == []
    interaction.guild_id = None
    assert await cog._panel_choices(interaction, "") == []
