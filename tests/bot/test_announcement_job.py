"""Senden und Bearbeiten einer Ankündigung durch den Bot, mit Fake-Discord-Objekten."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from redis.asyncio import Redis

from ggbot.bot.client import GGBot
from ggbot.bot.cogs.announcements import AnnouncementsCog
from ggbot.bot.services import Services
from ggbot.core.announce import message as msg
from ggbot.core.config import AppConfig
from ggbot.core.db.engine import Database
from ggbot.core.db.models import ScheduledJob
from tests.bot.ticket_fakes import GUILD_ID, make_world, text_channel

CH = 70
USER = 10


@pytest.fixture
async def bot(db: Database, redis: Redis) -> GGBot:
    return GGBot(Services.build(AppConfig(), db, redis))


def world(bot: GGBot) -> tuple[MagicMock, MagicMock]:
    guild = make_world(bot)
    channel = text_channel(CH)
    channel.guild = guild
    channel.send = AsyncMock(return_value=SimpleNamespace(id=900, delete=AsyncMock()))
    partial = MagicMock()
    partial.edit = AsyncMock()
    channel.get_partial_message = MagicMock(return_value=partial)
    channel.partial = partial
    guild.channels_by_id[CH] = channel
    return guild, channel


def job(announcement_id: int, rev: int) -> ScheduledJob:
    return ScheduledJob(
        guild_id=GUILD_ID, kind="announce.sync", payload={"id": announcement_id, "rev": rev}
    )


async def planned(bot: GGBot, message: msg.Message | None = None) -> tuple[int, int]:
    service = bot.services.announcements
    message = message or msg.Message(content="Hallo", embed=msg.Embed(title="Titel", color=255))
    created = await service.create(GUILD_ID, USER, "Test", message)
    view = await service.schedule(GUILD_ID, created.id, CH, None)
    return view.id, view.rev


async def test_senden_postet_text_und_embed_ohne_erwaehnungen(bot: GGBot) -> None:
    _, channel = world(bot)
    announcement_id, rev = await planned(bot)

    await AnnouncementsCog(bot)._job(job(announcement_id, rev))

    kwargs = channel.send.await_args.kwargs
    assert kwargs["content"] == "Hallo"
    assert kwargs["embeds"][0].title == "Titel"
    assert kwargs["embeds"][0].colour.value == 255
    mentions = kwargs["allowed_mentions"]
    assert (mentions.everyone, mentions.users, mentions.roles) == (False, False, False)
    view = await bot.services.announcements.get(GUILD_ID, announcement_id)
    assert (view.state, view.message_id, view.sent_at is not None) == ("sent", 900, True)


async def test_zweiter_lauf_desselben_jobs_sendet_nicht_nochmal(bot: GGBot) -> None:
    _, channel = world(bot)
    announcement_id, rev = await planned(bot)
    cog = AnnouncementsCog(bot)

    await cog._job(job(announcement_id, rev))
    channel.send.reset_mock()
    await cog._job(job(announcement_id, rev))

    channel.send.assert_not_awaited()  # hat schon eine Nachricht: das ist jetzt ein Abgleich


async def test_abgesagter_oder_neu_geplanter_job_sendet_nicht(bot: GGBot) -> None:
    _, channel = world(bot)
    announcement_id, rev = await planned(bot)
    await bot.services.announcements.cancel(GUILD_ID, announcement_id)

    await AnnouncementsCog(bot)._job(job(announcement_id, rev))

    channel.send.assert_not_awaited()
    assert (await bot.services.announcements.get(GUILD_ID, announcement_id)).state == "draft"


async def test_alter_job_nach_neuplanung_sendet_nicht(bot: GGBot) -> None:
    _, channel = world(bot)
    announcement_id, rev = await planned(bot)
    await bot.services.announcements.schedule(GUILD_ID, announcement_id, CH, None)

    await AnnouncementsCog(bot)._job(job(announcement_id, rev))  # Nummer 1, gültig ist 2

    channel.send.assert_not_awaited()


async def test_geloeschte_ankuendigung_ist_kein_fehler(bot: GGBot) -> None:
    _, channel = world(bot)
    announcement_id, rev = await planned(bot)
    await bot.services.announcements.delete(GUILD_ID, announcement_id)

    await AnnouncementsCog(bot)._job(job(announcement_id, rev))

    channel.send.assert_not_awaited()


async def test_server_nicht_verfuegbar_loest_einen_spaeteren_versuch_aus(bot: GGBot) -> None:
    world(bot)
    announcement_id, rev = await planned(bot)
    bot.get_guild = MagicMock(return_value=None)  # type: ignore[method-assign]

    with pytest.raises(LookupError):
        await AnnouncementsCog(bot)._job(job(announcement_id, rev))
    assert (await bot.services.announcements.get(GUILD_ID, announcement_id)).state == "scheduled"


@pytest.mark.parametrize(
    ("setup", "error"),
    [
        ("gone", "error.announce_channel_gone"),
        ("no_perms", "error.announce_perms"),
        ("no_embed_links", "error.announce_perms"),
        ("forbidden", "error.announce_perms"),
        ("http", "error.announce_discord"),
    ],
)
async def test_fehler_beim_senden_werden_gemerkt(bot: GGBot, setup: str, error: str) -> None:
    guild, channel = world(bot)
    announcement_id, rev = await planned(bot)
    response = SimpleNamespace(status=403, reason="x")
    if setup == "gone":
        guild.channels_by_id.pop(CH)
    elif setup == "no_perms":
        channel.permissions_for = MagicMock(return_value=discord.Permissions(view_channel=True))
    elif setup == "no_embed_links":
        channel.permissions_for = MagicMock(
            return_value=discord.Permissions(view_channel=True, send_messages=True)
        )
    elif setup == "forbidden":
        channel.send = AsyncMock(side_effect=discord.Forbidden(response, "x"))
    else:
        channel.send = AsyncMock(side_effect=discord.HTTPException(response, "x"))

    await AnnouncementsCog(bot)._job(job(announcement_id, rev))

    view = await bot.services.announcements.get(GUILD_ID, announcement_id)
    assert (view.state, view.error_key) == ("failed", error)


async def test_reiner_text_braucht_keine_einbettungsrechte(bot: GGBot) -> None:
    _, channel = world(bot)
    channel.permissions_for = MagicMock(
        return_value=discord.Permissions(view_channel=True, send_messages=True)
    )
    announcement_id, rev = await planned(bot, msg.Message(content="nur Text"))

    await AnnouncementsCog(bot)._job(job(announcement_id, rev))

    assert channel.send.await_args.kwargs["embeds"] == []


async def test_wurde_zwischenzeitlich_abgesagt_wird_die_kopie_zurueckgenommen(bot: GGBot) -> None:
    _, channel = world(bot)
    announcement_id, rev = await planned(bot)
    sent = SimpleNamespace(id=901, delete=AsyncMock())

    async def send_then_cancel(**_: object) -> SimpleNamespace:
        await bot.services.announcements.cancel(GUILD_ID, announcement_id)
        return sent

    channel.send = AsyncMock(side_effect=send_then_cancel)

    await AnnouncementsCog(bot)._job(job(announcement_id, rev))

    sent.delete.assert_awaited_once()
    assert (await bot.services.announcements.get(GUILD_ID, announcement_id)).state == "draft"


async def test_bearbeiten_passt_die_gesendete_nachricht_an(bot: GGBot) -> None:
    _, channel = world(bot)
    announcement_id, rev = await planned(bot)
    cog = AnnouncementsCog(bot)
    await cog._job(job(announcement_id, rev))

    await bot.services.announcements.update(
        GUILD_ID, announcement_id, "Test", msg.Message(content="Neu")
    )
    await cog._job(job(announcement_id, rev))

    channel.get_partial_message.assert_called_with(900)
    kwargs = channel.partial.edit.await_args.kwargs
    assert (kwargs["content"], kwargs["embeds"]) == ("Neu", [])  # Embed entfernt
    assert kwargs["allowed_mentions"].everyone is False
    view = await bot.services.announcements.get(GUILD_ID, announcement_id)
    assert (view.state, view.error_key) == ("sent", None)


@pytest.mark.parametrize(
    ("exception", "error"),
    [
        (
            discord.NotFound(SimpleNamespace(status=404, reason="x"), "x"),
            "error.announce_message_gone",
        ),
        (discord.Forbidden(SimpleNamespace(status=403, reason="x"), "x"), "error.announce_perms"),
        (
            discord.HTTPException(SimpleNamespace(status=500, reason="x"), "x"),
            "error.announce_discord",
        ),
    ],
)
async def test_fehler_beim_bearbeiten_bleiben_bei_der_gesendeten_ankuendigung(
    bot: GGBot, exception: Exception, error: str
) -> None:
    _, channel = world(bot)
    announcement_id, rev = await planned(bot)
    cog = AnnouncementsCog(bot)
    await cog._job(job(announcement_id, rev))
    channel.partial.edit = AsyncMock(side_effect=exception)

    await cog._job(job(announcement_id, rev))

    view = await bot.services.announcements.get(GUILD_ID, announcement_id)
    assert (view.state, view.error_key) == ("sent", error)
