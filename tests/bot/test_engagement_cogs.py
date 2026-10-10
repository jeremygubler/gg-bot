"""Eigene Befehle, Giveaways, Erinnerungen und Umfragen auf Discord-Ebene."""

import datetime as dt
import random
import re
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from redis.asyncio import Redis
from sqlalchemy import text

from ggbot.bot.client import GGBot
from ggbot.bot.cogs import customcmd as customcmd_module
from ggbot.bot.cogs import giveaway as giveaway_module
from ggbot.bot.cogs.customcmd import CustomCommandCog
from ggbot.bot.cogs.giveaway import BUTTON_TEMPLATE, GiveawayCog, JoinButton
from ggbot.bot.cogs.poll import PollCog
from ggbot.bot.cogs.reminder import ReminderCog
from ggbot.bot.services import Services
from ggbot.core.config import AppConfig
from ggbot.core.db.engine import Database
from ggbot.core.errors import UserError
from ggbot.core.redis import key
from tests.bot.test_cog_flows import sent_text
from tests.bot.test_panels import role
from tests.bot.ticket_fakes import GUILD_ID, click, make_world, member, text_channel
from tests.fakes import invoke

CH = 70
USER = 500
STAFF = 11


@pytest.fixture
async def bot(db: Database, redis: Redis) -> GGBot:
    return GGBot(Services.build(AppConfig(), db, redis))


def world(bot: GGBot, *roles: Any) -> tuple[MagicMock, MagicMock]:
    guild = make_world(bot, *roles)
    channel = text_channel(CH)
    guild.channels_by_id[CH] = channel
    guild.member_count = 99
    return guild, channel


def staff(guild: MagicMock) -> MagicMock:
    return member(guild, STAFF, manager=True)


# --- Eigene Befehle ---


class FixedRandom:
    def __init__(self, value: int = 0) -> None:
        self.value = value

    def randrange(self, n: int) -> int:
        return self.value % n


async def add_cmd(
    bot: GGBot, name: str = "regeln", response: str = "Hallo {name}", **kw: Any
) -> None:
    await bot.services.engagement.add_command(GUILD_ID, name, response, STAFF, **kw)


async def run_cmd(
    bot: GGBot, guild: MagicMock, who: MagicMock, name: str, args: str | None = None
) -> MagicMock:
    await bot.services.redis.delete(key("cd", "cc", f"{GUILD_ID}:{name}:{who.id}"))
    interaction = click(bot, guild, who)
    interaction.channel = text_channel(CH)
    cog = CustomCommandCog(bot)
    await invoke(cog.run, cog, interaction, name, args)
    return interaction


async def test_befehl_ausfuehren(bot: GGBot) -> None:
    guild, _ = world(bot)
    await add_cmd(bot, response="Hi {name} auf {server}, {members} Mitglieder, Aufruf {uses}")
    who = member(guild, USER)
    interaction = await run_cmd(bot, guild, who, "regeln")
    assert (
        sent_text(interaction.response.send_message)
        == "Hi User500 auf Testserver, 99 Mitglieder, Aufruf 1"
    )
    again = await run_cmd(bot, guild, who, "regeln")
    assert "Aufruf 2" in sent_text(again.response.send_message)  # der Zähler läuft mit
    kwargs = again.response.send_message.await_args.kwargs
    assert kwargs["ephemeral"] is False


async def test_argumente_pingen_niemanden(bot: GGBot) -> None:
    guild, _ = world(bot)
    await add_cmd(bot, response="Du sagst: {args}")
    interaction = await run_cmd(
        bot, guild, member(guild, USER), "regeln", "@everyone <@123> <@&456>"
    )
    assert sent_text(interaction.response.send_message) == "Du sagst: @everyone <@123> <@&456>"
    mentions = interaction.response.send_message.await_args.kwargs["allowed_mentions"]
    assert (mentions.everyone, mentions.users, mentions.roles) == (False, False, False)


async def test_kanal_und_benutzer_platzhalter(bot: GGBot) -> None:
    guild, _ = world(bot)
    await add_cmd(bot, response="{user} in {channel}")
    interaction = await run_cmd(bot, guild, member(guild, USER), "regeln")
    assert sent_text(interaction.response.send_message) == f"<@{USER}> in <#{CH}>"


async def test_zufall_nimmt_die_zufallsquelle(bot: GGBot, monkeypatch: pytest.MonkeyPatch) -> None:
    guild, _ = world(bot)
    await add_cmd(bot, response="{random:eins|zwei|drei}")
    for value, expected in ((0, "eins"), (1, "zwei"), (2, "drei")):
        monkeypatch.setattr(customcmd_module, "_RNG", FixedRandom(value))
        interaction = await run_cmd(bot, guild, member(guild, USER), "regeln")
        assert sent_text(interaction.response.send_message) == expected


async def test_nur_fuer_den_aufrufer_sichtbar(bot: GGBot) -> None:
    guild, _ = world(bot)
    await add_cmd(bot, ephemeral=True)
    interaction = await run_cmd(bot, guild, member(guild, USER), "regeln")
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True


async def test_rollenpflicht(bot: GGBot) -> None:
    guild, _ = world(bot)
    await add_cmd(bot, required_role_id=9)
    with pytest.raises(UserError) as exc:
        await run_cmd(bot, guild, member(guild, USER), "regeln")
    assert exc.value.key == "error.cc_role"
    assert exc.value.params["role"] == "<@&9>"
    await run_cmd(bot, guild, member(guild, USER + 1, 9), "regeln")  # mit Rolle geht es
    # Der abgewiesene Aufruf zählt nicht.
    assert (await bot.services.engagement.get_command(GUILD_ID, "regeln")).uses == 1


async def test_wartezeit_pro_mitglied(bot: GGBot) -> None:
    guild, _ = world(bot)
    await add_cmd(bot, cooldown_seconds=30)
    who = member(guild, USER)
    await run_cmd(bot, guild, who, "regeln")
    interaction = click(bot, guild, who)
    interaction.channel = text_channel(CH)
    cog = CustomCommandCog(bot)
    with pytest.raises(UserError) as exc:
        await invoke(cog.run, cog, interaction, "regeln", None)
    assert exc.value.key == "error.cooldown"
    await run_cmd(
        bot, guild, member(guild, USER + 1), "regeln"
    )  # andere Person ist nicht betroffen
    assert (await bot.services.engagement.get_command(GUILD_ID, "regeln")).uses == 2


async def test_ohne_wartezeit(bot: GGBot) -> None:
    guild, _ = world(bot)
    await add_cmd(bot, cooldown_seconds=0)
    who = member(guild, USER)
    for _ in range(3):
        interaction = click(bot, guild, who)
        interaction.channel = text_channel(CH)
        cog = CustomCommandCog(bot)
        await invoke(cog.run, cog, interaction, "regeln", None)
    assert (await bot.services.engagement.get_command(GUILD_ID, "regeln")).uses == 3


async def test_unbekannter_befehl_und_nur_mitglieder(bot: GGBot) -> None:
    guild, _ = world(bot)
    with pytest.raises(UserError) as exc:
        await run_cmd(bot, guild, member(guild, USER), "gibtsnicht")
    assert exc.value.key == "error.cc_unknown"
    await add_cmd(bot)
    interaction = click(bot, guild, MagicMock(spec=discord.User))
    cog = CustomCommandCog(bot)
    with pytest.raises(UserError) as exc2:
        await invoke(cog.run, cog, interaction, "regeln", None)
    assert exc2.value.key == "error.guild_only"


async def test_befehle_verwalten(bot: GGBot) -> None:
    guild, _ = world(bot, role(9, position=2))
    cog = CustomCommandCog(bot)

    def ia() -> MagicMock:
        return click(bot, guild, staff(guild))

    await invoke(
        cog.add, cog, ia(), "Hallo", "Zeile eins\\nZeile zwei", True, guild.get_role(9), 12
    )
    c = await bot.services.engagement.get_command(GUILD_ID, "hallo")
    assert (c.response, c.ephemeral, c.required_role_id, c.cooldown_seconds) == (
        "Zeile eins\nZeile zwei",
        True,
        9,
        12,
    )
    await invoke(cog.edit, cog, ia(), "hallo", "neu", None, None, False, None)
    assert (await bot.services.engagement.get_command(GUILD_ID, "hallo")).response == "neu"
    await invoke(cog.edit, cog, ia(), "hallo", None, None, None, True, None)
    assert (await bot.services.engagement.get_command(GUILD_ID, "hallo")).required_role_id is None
    with pytest.raises(UserError):
        await invoke(
            cog.edit, cog, ia(), "hallo", None, None, None, False, None
        )  # nichts zu ändern

    listing = ia()
    await invoke(cog.list_, cog, listing)
    assert "hallo" in sent_text(listing.response.send_message)
    info = ia()
    await invoke(cog.info, cog, info, "hallo")
    assert "neu" in sent_text(info.response.send_message)
    assert info.response.send_message.await_args.kwargs["allowed_mentions"].roles is False
    await invoke(cog.remove, cog, ia(), "HALLO")
    empty = ia()
    await invoke(cog.list_, cog, empty)
    assert "/customcmd add" in sent_text(empty.response.send_message)


async def test_info_kann_den_codeblock_nicht_verlassen(bot: GGBot) -> None:
    guild, _ = world(bot)
    cog = CustomCommandCog(bot)
    await add_cmd(bot, "x", "```\nAusbruch @everyone```")
    info = click(bot, guild, staff(guild))
    await invoke(cog.info, cog, info, "x")
    shown = sent_text(info.response.send_message)
    assert shown.count("```") == 2  # nur die eigenen Zäune
    assert info.response.send_message.await_args.kwargs["allowed_mentions"].everyone is False


async def test_autovervollstaendigung(bot: GGBot) -> None:
    guild, _ = world(bot)
    await add_cmd(bot, "regeln")
    await add_cmd(bot, "faq")
    await bot.services.engagement.add_command(GUILD_ID + 1, "fremd", "x", STAFF)
    cog = CustomCommandCog(bot)
    interaction = click(bot, guild, staff(guild))
    names: Any = getattr(cog, "_names")  # noqa: B009
    assert [c.value for c in await names(interaction, "re")] == ["regeln"]
    assert sorted(c.value for c in await names(interaction, "")) == ["faq", "regeln"]
    interaction.guild_id = None
    assert await names(interaction, "") == []


# --- Giveaways ---


def seeded(monkeypatch: pytest.MonkeyPatch, seed: int = 3) -> None:
    monkeypatch.setattr(giveaway_module, "_RNG", random.Random(seed))  # noqa: S311


async def start_giveaway(bot: GGBot, guild: MagicMock, channel: MagicMock, **kw: Any) -> int:
    cog = GiveawayCog(bot)
    channel.send = AsyncMock(return_value=SimpleNamespace(id=900))
    interaction = click(bot, guild, staff(guild))
    args = {"prize": "Preis", "duration": "1h", "winners": 1, "channel": channel, "role": None} | kw
    await invoke(cog.start, cog, interaction, *args.values())
    return (await bot.services.engagement.active_giveaways(GUILD_ID))[-1].id


async def test_giveaway_starten(bot: GGBot) -> None:
    guild, channel = world(bot, role(9, position=2))
    giveaway_id = await start_giveaway(bot, guild, channel, role=guild.get_role(9), winners=2)
    kwargs = channel.send.await_args.kwargs
    assert kwargs["embed"].title == "🎉 Preis"
    assert [f.value for f in kwargs["embed"].fields] == ["<@&9>"]
    [button] = kwargs["view"].children
    assert button.custom_id == f"gg:gw:{giveaway_id}"
    assert kwargs["allowed_mentions"].users is False
    view = await bot.services.engagement.get_giveaway(GUILD_ID, giveaway_id)
    assert (view.message_id, view.winner_count, view.required_role_id) == (900, 2, 9)
    async with bot.services.db.session() as session:
        assert (
            await session.execute(text("SELECT kind FROM scheduled_jobs"))
        ).scalar() == "giveaway.end"


async def test_giveaway_start_fehler(bot: GGBot) -> None:
    guild, channel = world(bot)
    cog = GiveawayCog(bot)
    channel.permissions_for = MagicMock(return_value=discord.Permissions(view_channel=True))
    with pytest.raises(UserError) as exc:
        await invoke(cog.start, cog, click(bot, guild, staff(guild)), "P", "1h", 1, channel, None)
    assert exc.value.key == "error.missing_bot_permissions"
    channel.permissions_for = MagicMock(return_value=discord.Permissions.all())
    with pytest.raises(UserError) as exc:
        await invoke(
            cog.start,
            cog,
            click(bot, guild, staff(guild)),
            "P",
            "1h",
            1,
            channel,
            guild.default_role,
        )
    assert exc.value.key == "error.notify_role_everyone"
    for duration in ("1x", "5", ""):
        with pytest.raises(UserError):
            await invoke(
                cog.start, cog, click(bot, guild, staff(guild)), "P", duration, 1, channel, None
            )
    assert await bot.services.engagement.active_giveaways(GUILD_ID) == []


async def click_join(bot: GGBot, guild: MagicMock, who: MagicMock, giveaway_id: int) -> MagicMock:
    interaction = click(bot, guild, who)
    await JoinButton(giveaway_id).callback(interaction)
    return interaction


async def test_teilnahme_knopf(bot: GGBot) -> None:
    guild, channel = world(bot, role(9, position=2))
    giveaway_id = await start_giveaway(bot, guild, channel, role=guild.get_role(9))
    outsider = member(guild, USER)
    with pytest.raises(UserError) as exc:
        await click_join(bot, guild, outsider, giveaway_id)
    assert exc.value.key == "error.gw_role"
    insider = member(guild, USER + 1, 9)
    first = await click_join(bot, guild, insider, giveaway_id)
    assert "1" in sent_text(first.response.send_message)
    assert first.response.send_message.await_args.kwargs["ephemeral"] is True
    await click_join(bot, guild, insider, giveaway_id)  # zurückziehen
    robot = member(guild, USER + 2, 9)
    robot.bot = True
    with pytest.raises(UserError) as exc:
        await click_join(bot, guild, robot, giveaway_id)
    assert exc.value.key == "error.gw_bots"
    _, entrants = await bot.services.engagement.end_giveaway(GUILD_ID, giveaway_id)
    assert entrants == []


async def test_knopf_wiederherstellung(bot: GGBot) -> None:
    match = re.fullmatch(BUTTON_TEMPLATE, "gg:gw:42")
    assert match is not None
    button = await JoinButton.from_custom_id(MagicMock(), MagicMock(), match)
    assert button.giveaway_id == 42
    assert not re.fullmatch(BUTTON_TEMPLATE, "gg:gw:x")
    await bot.add_cog(GiveawayCog(bot))
    assert BUTTON_TEMPLATE in {p.pattern for p in bot._connection._view_store._dynamic_items}


async def enter_all(bot: GGBot, giveaway_id: int, users: list[int]) -> None:
    for u in users:
        await bot.services.engagement.toggle_entry(GUILD_ID, giveaway_id, u)


async def test_ziehung(bot: GGBot, monkeypatch: pytest.MonkeyPatch) -> None:
    seeded(monkeypatch)
    guild, channel = world(bot)
    members = {u: member(guild, u) for u in range(1, 9)}
    guild.get_member = MagicMock(side_effect=members.get)
    giveaway_id = await start_giveaway(bot, guild, channel, winners=3)
    await enter_all(bot, giveaway_id, list(members))
    message = MagicMock()
    message.edit = AsyncMock()
    channel.fetch_message = AsyncMock(return_value=message)
    channel.send = AsyncMock()
    winners = await GiveawayCog(bot).finish(guild, giveaway_id)
    assert len(set(winners)) == 3
    assert set(winners) <= set(members)
    view = await bot.services.engagement.get_giveaway(GUILD_ID, giveaway_id)
    assert (view.ended, set(view.winners)) == (True, set(winners))
    edit = message.edit.await_args.kwargs
    assert edit["view"].children[0].item.disabled is True  # niemand kann mehr klicken
    assert "Gewinner" in edit["embed"].description
    announce = channel.send.await_args
    assert all(f"<@{w}>" in announce.args[0] for w in winners)
    mentions = announce.kwargs["allowed_mentions"]
    assert sorted(u.id for u in mentions.users) == sorted(winners)
    assert (mentions.everyone, mentions.roles) == (False, False)
    with pytest.raises(UserError) as exc:
        await GiveawayCog(bot).finish(guild, giveaway_id)
    assert exc.value.key == "error.gw_over"


async def test_ungueltige_teilnehmer_gewinnen_nicht(
    bot: GGBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    guild, channel = world(bot, role(9, position=2))
    giveaway_id = await start_giveaway(bot, guild, channel, winners=5, role=guild.get_role(9))
    gone = SimpleNamespace()  # nicht mehr auf dem Server
    robot = member(guild, 2, 9)
    robot.bot = True
    lost_role = member(guild, 3)  # hat die Rolle inzwischen verloren
    good = [member(guild, 4, 9), member(guild, 5, 9)]
    table = {2: robot, 3: lost_role, 4: good[0], 5: good[1]}
    guild.get_member = MagicMock(side_effect=table.get)
    guild.fetch_member = AsyncMock(side_effect=discord.NotFound(MagicMock(status=404), "weg"))
    await enter_all(bot, giveaway_id, [1, 2, 3, 4, 5])
    del gone
    channel.send = AsyncMock()
    channel.fetch_message = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
    winners = await GiveawayCog(bot).finish(guild, giveaway_id)
    assert sorted(winners) == [4, 5]  # 1 weg, 2 Bot, 3 ohne Rolle


async def test_mitglied_nicht_im_cache_wird_nachgefragt(bot: GGBot) -> None:
    guild, channel = world(bot)
    giveaway_id = await start_giveaway(bot, guild, channel)
    guild.get_member = MagicMock(return_value=None)  # ohne Members-Intent
    guild.fetch_member = AsyncMock(return_value=member(guild, 1))
    await enter_all(bot, giveaway_id, [1])
    channel.send = AsyncMock()
    channel.fetch_message = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
    assert await GiveawayCog(bot).finish(guild, giveaway_id) == [1]
    guild.fetch_member.assert_awaited_once_with(1)


async def test_niemand_nahm_teil(bot: GGBot) -> None:
    guild, channel = world(bot)
    giveaway_id = await start_giveaway(bot, guild, channel)
    channel.send = AsyncMock()
    channel.fetch_message = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
    assert await GiveawayCog(bot).finish(guild, giveaway_id) == []
    assert "Preis" in channel.send.await_args.args[0]
    assert (await bot.services.engagement.get_giveaway(GUILD_ID, giveaway_id)).winners == ()


async def test_job_beendet_und_toleriert_doppelten_lauf(bot: GGBot) -> None:
    guild, channel = world(bot)
    giveaway_id = await start_giveaway(bot, guild, channel)
    cog = GiveawayCog(bot)
    channel.send = AsyncMock()
    channel.fetch_message = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
    job: Any = SimpleNamespace(guild_id=GUILD_ID, payload={"giveaway_id": giveaway_id})
    await cog._job(job)
    await cog._job(job)  # kein Fehler: schon beendet
    assert (await bot.services.engagement.get_giveaway(GUILD_ID, giveaway_id)).ended
    assert channel.send.await_count == 1  # nur eine Ankündigung
    bot.get_guild = MagicMock(return_value=None)  # type: ignore[method-assign]
    with pytest.raises(LookupError):
        await cog._job(job)


async def test_reroll(bot: GGBot, monkeypatch: pytest.MonkeyPatch) -> None:
    seeded(monkeypatch)
    guild, channel = world(bot)
    members = {u: member(guild, u) for u in range(1, 5)}
    guild.get_member = MagicMock(side_effect=members.get)
    giveaway_id = await start_giveaway(bot, guild, channel, winners=2)
    await enter_all(bot, giveaway_id, list(members))
    cog = GiveawayCog(bot)
    channel.send = AsyncMock()
    channel.fetch_message = AsyncMock(return_value=MagicMock(edit=AsyncMock()))
    with pytest.raises(UserError) as exc:
        await invoke(cog.reroll, cog, click(bot, guild, staff(guild)), giveaway_id, 1)  # läuft noch
    assert exc.value.key == "error.gw_running"
    first = await cog.finish(guild, giveaway_id)
    channel.send.reset_mock()
    await invoke(cog.reroll, cog, click(bot, guild, staff(guild)), giveaway_id, 2)
    view = await bot.services.engagement.get_giveaway(GUILD_ID, giveaway_id)
    assert len(view.winners) == 4
    assert set(view.winners) == set(members)  # die zwei übrigen kamen dazu
    assert not set(view.winners[2:]) & set(first)  # nie die alten Gewinner
    mentions = channel.send.await_args.kwargs["allowed_mentions"]
    assert {u.id for u in mentions.users} == set(view.winners[2:])
    interaction = click(bot, guild, staff(guild))
    await invoke(cog.reroll, cog, interaction, giveaway_id, 1)
    assert (
        "niemand" in sent_text(interaction.followup.send).lower()
        or "nobody" in sent_text(interaction.followup.send).lower()
    )


async def test_abbrechen_und_liste(bot: GGBot) -> None:
    guild, channel = world(bot)
    giveaway_id = await start_giveaway(bot, guild, channel)
    cog = GiveawayCog(bot)
    listing = click(bot, guild, staff(guild))
    await invoke(cog.list_, cog, listing)
    assert "Preis" in sent_text(listing.response.send_message)
    partial = MagicMock()
    partial.delete = AsyncMock()
    channel.get_partial_message = MagicMock(return_value=partial)
    await invoke(cog.cancel, cog, click(bot, guild, staff(guild)), giveaway_id)
    partial.delete.assert_awaited_once()
    assert await bot.services.engagement.active_giveaways(GUILD_ID) == []
    empty = click(bot, guild, staff(guild))
    await invoke(cog.list_, cog, empty)
    assert (
        "Keine" in sent_text(empty.response.send_message)
        or "no running" in sent_text(empty.response.send_message).lower()
        or "laufen keine" in sent_text(empty.response.send_message).lower()
    )
    with pytest.raises(UserError):
        await invoke(cog.cancel, cog, click(bot, guild, staff(guild)), giveaway_id)


async def test_fremdes_giveaway(bot: GGBot) -> None:
    guild, _channel = world(bot)
    other = await bot.services.engagement.create_giveaway(
        GUILD_ID + 1,
        channel_id=1,
        prize="x",
        winner_count=1,
        host_id=1,
        duration=dt.timedelta(hours=1),
    )
    cog = GiveawayCog(bot)
    for call in (
        invoke(cog.end, cog, click(bot, guild, staff(guild)), other.id),
        invoke(cog.cancel, cog, click(bot, guild, staff(guild)), other.id),
        invoke(cog.reroll, cog, click(bot, guild, staff(guild)), other.id, 1),
    ):
        with pytest.raises(UserError) as exc:
            await call
        assert exc.value.key == "error.gw_unknown"


# --- Erinnerungen ---


async def test_erinnerung_setzen_listen_abbrechen(bot: GGBot) -> None:
    guild, _ = world(bot)
    cog = ReminderCog(bot)
    who = member(guild, USER)
    interaction = click(bot, guild, who)
    interaction.channel_id = CH
    await invoke(cog.me, cog, interaction, "2h", "Müll rausbringen")
    [reminder] = await bot.services.engagement.pending_reminders(GUILD_ID, USER)
    assert (reminder.channel_id, reminder.text) == (CH, "Müll rausbringen")
    assert f"#{reminder.id}" in sent_text(interaction.response.send_message)
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True
    listing = click(bot, guild, who)
    await invoke(cog.list_, cog, listing)
    assert "Müll" in sent_text(listing.response.send_message)
    other = click(bot, guild, member(guild, USER + 1))
    await invoke(cog.list_, cog, other)
    assert (
        "keine" in sent_text(other.response.send_message).lower()
        or "no open" in sent_text(other.response.send_message).lower()
    )
    with pytest.raises(UserError):
        await invoke(cog.cancel, cog, click(bot, guild, member(guild, USER + 1)), reminder.id)
    await invoke(cog.cancel, cog, click(bot, guild, who), reminder.id)
    assert await bot.services.engagement.pending_reminders(GUILD_ID, USER) == []


@pytest.mark.parametrize("when", ["", "2", "morgen", "30s", "400d"])
async def test_ungueltige_dauer(bot: GGBot, when: str) -> None:
    guild, _ = world(bot)
    cog = ReminderCog(bot)
    with pytest.raises(UserError):
        await invoke(cog.me, cog, click(bot, guild, member(guild, USER)), when, "x")
    assert await bot.services.engagement.pending_reminders(GUILD_ID, USER) == []


async def fire(bot: GGBot, reminder_id: int) -> None:
    job: Any = SimpleNamespace(guild_id=GUILD_ID, payload={"reminder_id": reminder_id})
    await ReminderCog(bot)._job(job)


async def make_reminder(bot: GGBot, channel_id: int | None = CH) -> int:
    return (
        await bot.services.engagement.create_reminder(
            GUILD_ID, USER, channel_id, "Hallo @everyone", dt.timedelta(hours=1)
        )
    ).id


async def test_erinnerung_im_kanal_pingt_nur_den_ersteller(bot: GGBot) -> None:
    _, channel = world(bot)
    channel.send = AsyncMock()
    await fire(bot, await make_reminder(bot))
    call = channel.send.await_args
    assert f"<@{USER}>" in call.args[0]
    assert "Hallo @everyone" in call.args[0]
    mentions = call.kwargs["allowed_mentions"]
    assert [u.id for u in mentions.users] == [USER]
    assert (mentions.everyone, mentions.roles) == (False, False)


async def test_erinnerung_faellt_auf_dm_zurueck(bot: GGBot) -> None:
    _, channel = world(bot)
    channel.send = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "x"))
    user = MagicMock()
    user.send = AsyncMock()
    bot.get_user = MagicMock(return_value=user)  # type: ignore[method-assign]
    await fire(bot, await make_reminder(bot))
    user.send.assert_awaited_once()
    assert "Hallo" in user.send.await_args.args[0]
    assert user.send.await_args.kwargs["allowed_mentions"].users is False


async def test_erinnerung_ohne_kanal_geht_per_dm(bot: GGBot) -> None:
    world(bot)
    user = MagicMock()
    user.send = AsyncMock()
    bot.get_user = MagicMock(return_value=user)  # type: ignore[method-assign]
    await fire(bot, await make_reminder(bot, channel_id=None))
    user.send.assert_awaited_once()


async def test_unzustellbare_erinnerung_wirft_nicht(bot: GGBot) -> None:
    _, channel = world(bot)
    channel.send = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "x"))
    user = MagicMock()
    user.send = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "dm zu"))
    bot.get_user = MagicMock(return_value=user)  # type: ignore[method-assign]
    reminder_id = await make_reminder(bot)
    await fire(bot, reminder_id)  # kein Fehler, sonst liefe der Scheduler endlos wiederholend
    assert await bot.services.engagement.fire_reminder(GUILD_ID, reminder_id) is None


async def test_abgebrochene_und_doppelte_erinnerung(bot: GGBot) -> None:
    _, channel = world(bot)
    channel.send = AsyncMock()
    reminder_id = await make_reminder(bot)
    await fire(bot, reminder_id)
    await fire(bot, reminder_id)  # zweiter Lauf desselben Jobs
    assert channel.send.await_count == 1
    cancelled = await make_reminder(bot)
    await bot.services.engagement.cancel_reminder(GUILD_ID, USER, cancelled)
    await fire(bot, cancelled)
    assert channel.send.await_count == 1


async def test_erinnerung_server_nicht_verfuegbar(bot: GGBot) -> None:
    world(bot)
    reminder_id = await make_reminder(bot)
    bot.get_guild = MagicMock(return_value=None)  # type: ignore[method-assign]
    with pytest.raises(LookupError):
        await fire(bot, reminder_id)
    # Der Scheduler versucht es erneut; die Erinnerung ist noch offen.
    assert len(await bot.services.engagement.pending_reminders(GUILD_ID, USER)) == 1


# --- Umfragen ---


async def test_umfrage_erstellen(bot: GGBot) -> None:
    guild, _ = world(bot)
    cog = PollCog(bot)
    interaction = click(bot, guild, staff(guild))
    await invoke(cog.create, cog, interaction, " Wohin? ", "Berge; Meer ;See", "2d", True)
    poll = interaction.response.send_message.await_args.kwargs["poll"]
    assert poll.question == "Wohin?"
    assert [a.text for a in poll.answers] == ["Berge", "Meer", "See"]
    assert poll.duration == dt.timedelta(hours=48)
    assert poll.multiple is True


@pytest.mark.parametrize(
    ("question", "options", "duration"),
    [
        ("", "a;b", "1h"),
        ("F?", "nur eine", "1h"),
        ("F?", "a;a", "1h"),
        ("F?", "a;b", "99d"),
        ("F?", "a;b", "xx"),
    ],
)
async def test_ungueltige_umfrage(bot: GGBot, question: str, options: str, duration: str) -> None:
    guild, _ = world(bot)
    cog = PollCog(bot)
    interaction = click(bot, guild, staff(guild))
    with pytest.raises(UserError):
        await invoke(cog.create, cog, interaction, question, options, duration, False)
    interaction.response.send_message.assert_not_awaited()


async def test_umfrage_beenden(bot: GGBot) -> None:
    guild, channel = world(bot)
    cog = PollCog(bot)
    bot._connection.user = SimpleNamespace(id=999)  # type: ignore[assignment]
    poll = MagicMock()
    poll.end = AsyncMock()
    ours = SimpleNamespace(author=SimpleNamespace(id=999), poll=poll)
    foreign = SimpleNamespace(author=SimpleNamespace(id=1), poll=poll)
    no_poll = SimpleNamespace(author=SimpleNamespace(id=999), poll=None)
    messages = {1: ours, 2: foreign, 3: no_poll}

    async def fetch(message_id: int) -> Any:
        if message_id not in messages:
            raise discord.NotFound(MagicMock(status=404), "x")
        return messages[message_id]

    channel.fetch_message = AsyncMock(side_effect=fetch)
    interaction = click(bot, guild, staff(guild), channel)
    interaction.channel = channel
    await invoke(cog.end, cog, interaction, "1")
    poll.end.assert_awaited_once()
    for bad in ("2", "3", "9", "abc"):
        with pytest.raises(UserError) as exc:
            await invoke(cog.end, cog, click(bot, guild, staff(guild), channel), bad)
        assert exc.value.key == "error.poll_not_found"
    poll.end.assert_awaited_once()  # fremde Umfragen und Nachrichten ohne Umfrage bleiben unberührt
