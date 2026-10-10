"""Aufträge des Dashboards (`panel.publish`) bis zur Discord-Nachricht und zurück ins Statusfeld."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from redis.asyncio import Redis

from ggbot.bot.client import GGBot
from ggbot.bot.cogs import rolepanels as rolepanels_module
from ggbot.bot.cogs.rolepanels import RolePanelsCog
from ggbot.bot.cogs.ticketpanel import TicketPanelCog
from ggbot.bot.services import Services
from ggbot.core import events
from ggbot.core.config import AppConfig
from ggbot.core.db.engine import Database
from ggbot.core.panelstatus import get_status
from tests.bot.test_panels import role
from tests.bot.test_rolepanels_cog import CH, make_channel, make_message, panel_of, world
from tests.bot.ticket_fakes import GUILD_ID, SUPPORT, make_world, text_channel
from tests.fakes import BOT

GUILD = GUILD_ID


@pytest.fixture
async def bot(db: Database, redis: Redis) -> GGBot:
    instance = GGBot(Services.build(AppConfig(), db, redis))
    instance._connection.user = SimpleNamespace(id=BOT)  # type: ignore[assignment]
    return instance


@pytest.fixture(autouse=True)
def no_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rolepanels_module, "REACTION_PAUSE", 0)


def event(
    panel_id: int,
    action: str = "publish",
    kind: str = "role",
    channel_id: int | None = CH,
    message_id: int | None = None,
) -> events.PanelPublish:
    return events.PanelPublish(
        guild_id=GUILD,
        panel_id=panel_id,
        kind=kind,
        action=action,
        channel_id=channel_id,
        message_id=message_id,
    )


def wired(bot: GGBot, channel: MagicMock, *roles: MagicMock) -> MagicMock:
    guild = world(bot, channel, *roles)
    guild.get_channel = MagicMock(side_effect={channel.id: channel}.get)
    return guild


# --- Rollen-Panels ---


async def test_rollen_panel_wird_veroeffentlicht_und_der_status_gemeldet(bot: GGBot) -> None:
    cog = RolePanelsCog(bot)
    channel = make_channel()
    wired(bot, channel, role(1), role(2))
    panel = await panel_of(bot, "button", "normal", 1, 2)

    await cog.on_ggbot_panel_publish(event(panel.id))

    channel.send.assert_awaited_once()
    stored = await bot.services.rolepanels.get(GUILD, panel.id)
    assert (stored.channel_id, stored.message_id) == (CH, 900)
    status = await get_status(bot.services.redis, "role", GUILD, panel.id)
    assert status is not None
    assert status.state == "done"
    assert status.error_key is None


async def test_fehlende_rechte_im_kanal_werden_als_fehler_gemeldet(bot: GGBot) -> None:
    cog = RolePanelsCog(bot)
    channel = make_channel()
    channel.permissions_for = MagicMock(return_value=discord.Permissions(view_channel=True))
    wired(bot, channel, role(1))
    panel = await panel_of(bot, "button", "normal", 1)

    await cog.on_ggbot_panel_publish(event(panel.id))

    channel.send.assert_not_awaited()
    status = await get_status(bot.services.redis, "role", GUILD, panel.id)
    assert status is not None
    assert (status.state, status.error_key) == ("error", "error.missing_bot_permissions")
    assert "Send Messages" in status.error_params["permissions"]


@pytest.mark.parametrize("channel_id", [None, 12345])
async def test_unbekannter_oder_fehlender_kanal_ist_ein_fehler(
    bot: GGBot, channel_id: int | None
) -> None:
    cog = RolePanelsCog(bot)
    wired(bot, make_channel(), role(1))
    panel = await panel_of(bot, "button", "normal", 1)

    await cog.on_ggbot_panel_publish(event(panel.id, channel_id=channel_id))

    status = await get_status(bot.services.redis, "role", GUILD, panel.id)
    assert status is not None
    assert status.error_key == "error.panel_channel_invalid"


async def test_discord_lehnt_das_senden_ab(bot: GGBot) -> None:
    cog = RolePanelsCog(bot)
    channel = make_channel()
    channel.send = AsyncMock(side_effect=discord.HTTPException(MagicMock(status=500), "boom"))
    wired(bot, channel, role(1))
    panel = await panel_of(bot, "button", "normal", 1)

    await cog.on_ggbot_panel_publish(event(panel.id))

    status = await get_status(bot.services.redis, "role", GUILD, panel.id)
    assert status is not None
    assert (status.state, status.error_key) == ("error", "error.panel_publish_failed")


async def test_leeres_oder_externes_panel_wird_nicht_veroeffentlicht(bot: GGBot) -> None:
    cog = RolePanelsCog(bot)
    channel = make_channel()
    wired(bot, channel)
    empty = await panel_of(bot, "button", "normal")

    await cog.on_ggbot_panel_publish(event(empty.id))

    channel.send.assert_not_awaited()
    status = await get_status(bot.services.redis, "role", GUILD, empty.id)
    assert status is not None
    assert status.error_key == "error.panel_empty"


async def test_abgelehnte_reaktionen_sind_ein_hinweis_kein_fehler(bot: GGBot) -> None:
    cog = RolePanelsCog(bot)
    channel = make_channel()
    channel.last.add_reaction = AsyncMock(
        side_effect=discord.HTTPException(MagicMock(status=400), "Unknown Emoji")
    )
    wired(bot, channel, role(1))
    panel = await panel_of(bot, "reaction", "normal", 1)

    await cog.on_ggbot_panel_publish(event(panel.id))

    status = await get_status(bot.services.redis, "role", GUILD, panel.id)
    assert status is not None
    assert status.state == "done"
    assert status.notice_key == "roles.reaction_failed"
    assert (await bot.services.rolepanels.get(GUILD, panel.id)).published


async def test_aktualisieren_baut_die_vorhandene_nachricht_neu(bot: GGBot) -> None:
    cog = RolePanelsCog(bot)
    channel = make_channel()
    message = make_message(800)
    channel.stored[800] = message
    wired(bot, channel, role(1), role(2))
    panel = await panel_of(bot, "button", "normal", 1, 2)
    await bot.services.rolepanels.set_message(GUILD, panel.id, CH, 800)

    await cog.on_ggbot_panel_publish(event(panel.id, "refresh", channel_id=None))

    message.edit.assert_awaited_once()
    assert len(message.edit.await_args.kwargs["view"].children) == 2
    status = await get_status(bot.services.redis, "role", GUILD, panel.id)
    assert status is not None
    assert status.state == "done"


async def test_loeschen_entfernt_die_nachricht_ueber_ihre_ids(bot: GGBot) -> None:
    cog = RolePanelsCog(bot)
    channel = make_channel()
    partial = MagicMock()
    partial.delete = AsyncMock()
    channel.get_partial_message = MagicMock(return_value=partial)
    wired(bot, channel)

    await cog.on_ggbot_panel_publish(event(77, "delete", channel_id=CH, message_id=800))

    channel.get_partial_message.assert_called_once_with(800)
    partial.delete.assert_awaited_once()
    status = await get_status(bot.services.redis, "role", GUILD, 77)
    assert status is not None
    assert status.state == "done"


async def test_loeschen_einer_schon_geloeschten_nachricht_ist_kein_fehler(bot: GGBot) -> None:
    cog = RolePanelsCog(bot)
    channel = make_channel()
    partial = MagicMock()
    partial.delete = AsyncMock(side_effect=discord.NotFound(MagicMock(status=404), "gone"))
    channel.get_partial_message = MagicMock(return_value=partial)
    wired(bot, channel)

    await cog.on_ggbot_panel_publish(event(77, "delete", channel_id=CH, message_id=800))

    status = await get_status(bot.services.redis, "role", GUILD, 77)
    assert status is not None
    assert status.state == "done"


async def test_bot_nicht_auf_dem_server_meldet_fehler(bot: GGBot) -> None:
    cog = RolePanelsCog(bot)
    bot.get_guild = MagicMock(return_value=None)  # type: ignore[method-assign]

    await cog.on_ggbot_panel_publish(event(5))

    status = await get_status(bot.services.redis, "role", GUILD, 5)
    assert status is not None
    assert status.error_key == "error.panel_publish_failed"


async def test_ereignisse_anderer_art_werden_ignoriert(bot: GGBot, redis: Redis) -> None:
    cog = RolePanelsCog(bot)
    ticket_cog = TicketPanelCog(bot)

    await cog.on_ggbot_panel_publish(event(5, kind="ticket"))
    await ticket_cog.on_ggbot_panel_publish(event(5, kind="role"))

    assert await get_status(redis, "role", GUILD, 5) is None
    assert await get_status(redis, "ticket", GUILD, 5) is None


# --- Ticket-Panels ---


async def ticket_panel(bot: GGBot, *, with_category: bool = True) -> int:
    service = bot.services.tickets
    panel = await service.create_panel(GUILD, title="Support")
    if with_category:
        await service.add_category(GUILD, panel.id, name="Hilfe", support_role_id=SUPPORT)
    return panel.id


def ticket_world(bot: GGBot) -> tuple[MagicMock, MagicMock]:
    guild = make_world(bot)
    channel = text_channel(70)
    channel.send = AsyncMock(return_value=MagicMock(id=900))
    guild.channels_by_id[70] = channel
    return guild, channel


async def test_ticket_panel_wird_veroeffentlicht(bot: GGBot) -> None:
    cog = TicketPanelCog(bot)
    _guild, channel = ticket_world(bot)
    panel_id = await ticket_panel(bot)

    await cog.on_ggbot_panel_publish(event(panel_id, kind="ticket", channel_id=70))

    channel.send.assert_awaited_once()
    stored = await bot.services.tickets.get_panel(GUILD, panel_id)
    assert (stored.channel_id, stored.message_id) == (70, 900)
    status = await get_status(bot.services.redis, "ticket", GUILD, panel_id)
    assert status is not None
    assert status.state == "done"


async def test_ticket_panel_ohne_kategorie_ist_ein_fehler(bot: GGBot) -> None:
    cog = TicketPanelCog(bot)
    _guild, channel = ticket_world(bot)
    panel_id = await ticket_panel(bot, with_category=False)

    await cog.on_ggbot_panel_publish(event(panel_id, kind="ticket", channel_id=70))

    channel.send.assert_not_awaited()
    status = await get_status(bot.services.redis, "ticket", GUILD, panel_id)
    assert status is not None
    assert status.error_key == "error.tpanel_empty"


async def test_ticket_panel_ohne_manage_channels_meldet_den_fehler(bot: GGBot) -> None:
    cog = TicketPanelCog(bot)
    guild, channel = ticket_world(bot)
    guild.me.guild_permissions = discord.Permissions(manage_roles=True)
    panel_id = await ticket_panel(bot)

    await cog.on_ggbot_panel_publish(event(panel_id, kind="ticket", channel_id=70))

    channel.send.assert_not_awaited()
    status = await get_status(bot.services.redis, "ticket", GUILD, panel_id)
    assert status is not None
    assert status.error_key == "error.missing_bot_permissions"


async def test_ticket_panel_aktualisieren_und_loeschen(bot: GGBot) -> None:
    cog = TicketPanelCog(bot)
    guild, channel = ticket_world(bot)
    message = MagicMock()
    message.edit = AsyncMock()
    channel.fetch_message = AsyncMock(return_value=message)
    partial = MagicMock()
    partial.delete = AsyncMock()
    channel.get_partial_message = MagicMock(return_value=partial)
    guild.get_channel = MagicMock(side_effect=lambda cid: {70: channel}.get(cid))
    panel_id = await ticket_panel(bot)
    await bot.services.tickets.set_panel_message(GUILD, panel_id, 70, 900)

    await cog.on_ggbot_panel_publish(event(panel_id, "refresh", "ticket", channel_id=None))
    message.edit.assert_awaited_once()

    await cog.on_ggbot_panel_publish(event(panel_id, "delete", "ticket", 70, 900))
    partial.delete.assert_awaited_once()


async def test_ticket_event_ohne_server(bot: GGBot) -> None:
    cog = TicketPanelCog(bot)
    bot.get_guild = MagicMock(return_value=None)  # type: ignore[method-assign]

    await cog.on_ggbot_panel_publish(event(5, kind="ticket"))

    status = await get_status(bot.services.redis, "ticket", GUILD, 5)
    assert status is not None
    assert status.error_key == "error.panel_publish_failed"
