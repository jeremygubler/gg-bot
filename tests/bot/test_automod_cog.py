"""Automod vom Ereignis bis zur Wirkung, mit Fake-Discord-Objekten und echter DB/Redis."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from redis.asyncio import Redis

from ggbot.bot.client import GGBot
from ggbot.bot.cogs import automod as automod_module
from ggbot.bot.cogs.automod import AutomodCog
from ggbot.bot.services import Services
from ggbot.core import events
from ggbot.core.config import AppConfig
from ggbot.core.db.engine import Database
from ggbot.core.settings.automod import AutomodSettings
from tests.bot.test_cog_flows import GUILD_ID, make_guild, not_found
from tests.fakes import BOT, FakePort

CH = 70
LOG_CH = 71
USER = 500


@pytest.fixture
async def bot(db: Database, redis: Redis) -> GGBot:
    config = AppConfig(enable_message_content_intent=True, enable_members_intent=True)
    return GGBot(Services.build(config, db, redis))


@pytest.fixture
def port(monkeypatch: pytest.MonkeyPatch) -> FakePort:
    fake = FakePort()
    monkeypatch.setattr(automod_module, "DiscordModerationPort", lambda client, guild: fake)
    monkeypatch.setattr(automod_module, "JOIN_PAUSE_SECONDS", 0)
    return fake


async def configure(bot: GGBot, **kwargs: object) -> None:
    await bot.services.settings.save(GUILD_ID, AutomodSettings.model_validate(kwargs))
    bot.services.automod.invalidate(GUILD_ID)


def on(**extra: object) -> dict[str, object]:
    return {"enabled": True, **extra}


def make_text_channel(channel_id: int = CH) -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    channel.mention = f"<#{channel_id}>"
    channel.send = AsyncMock()
    channel.delete_messages = AsyncMock()
    partials: dict[int, MagicMock] = {}

    def partial(message_id: int) -> MagicMock:
        if message_id not in partials:
            partials[message_id] = MagicMock()
            partials[message_id].delete = AsyncMock()
        return partials[message_id]

    channel.get_partial_message = MagicMock(side_effect=partial)
    channel.partials = partials
    return channel


def make_guild_with(*channels: MagicMock) -> MagicMock:
    guild = make_guild()
    by_id = {c.id: c for c in channels}
    guild.get_channel_or_thread = MagicMock(side_effect=by_id.get)
    guild.get_channel = MagicMock(side_effect=by_id.get)
    guild.system_channel = None
    return guild


def make_author(
    user_id: int = USER, *, manage_messages: bool = False, roles: tuple[int, ...] = ()
) -> MagicMock:
    author = MagicMock(spec=discord.Member)
    author.id = user_id
    author.bot = False
    author.mention = f"<@{user_id}>"
    author.roles = [SimpleNamespace(id=r) for r in roles]
    author.guild_permissions = discord.Permissions(manage_messages=manage_messages)
    return author


_counter = [10_000]


def make_message(
    guild: MagicMock, author: MagicMock, channel: MagicMock, content: str, **overrides: Any
) -> MagicMock:
    _counter[0] += 1
    message = MagicMock(spec=discord.Message)
    message.id = _counter[0]
    message.guild = guild
    message.author = author
    message.channel = channel
    message.content = content
    message.webhook_id = None
    message.type = discord.MessageType.default
    message.mentions = []
    message.role_mentions = []
    for name, value in overrides.items():
        setattr(message, name, value)
    return message


async def case_types(bot: GGBot) -> list[str]:
    types = []
    for number in range(1, 10):
        try:
            types.append((await bot.services.moderation.get_case(GUILD_ID, number)).type)
        except Exception:
            break
    return types


# --- Nachrichten ---


async def test_verstoss_loescht_verwarnt_benachrichtigt_und_zaehlt(
    bot: GGBot, port: FakePort
) -> None:
    channel, log_channel = make_text_channel(), make_text_channel(LOG_CH)
    guild = make_guild_with(channel, log_channel)
    await configure(
        bot,
        words=on(
            entries=[{"type": "word", "pattern": "boese"}], action={"delete": True, "warn": True}
        ),
        log_channel_id=LOG_CH,
    )
    cog = AutomodCog(bot)
    message = make_message(guild, make_author(), channel, "du bist boese")

    await cog.on_message(message)

    channel.partials[message.id].delete.assert_awaited_once()
    assert await case_types(bot) == ["warn"]
    case = await bot.services.moderation.get_case(GUILD_ID, 1)
    assert (case.target_id, case.moderator_id) == (USER, BOT)
    assert case.reason is not None
    assert "Wortfilter" in case.reason
    notice = channel.send.await_args
    assert "entfernt" in notice.args[0]
    assert notice.kwargs["delete_after"] == 8
    embed = log_channel.send.await_args.kwargs["embed"]
    assert any("boese" in str(f.value) for f in embed.fields)  # Auslöser im Mod-Log
    assert await bot.services.automod.stats(GUILD_ID, 7) == {"words": 1}


async def test_timeout_aktion_und_eskalation_gehen_ueber_den_moderationsdienst(
    bot: GGBot, port: FakePort
) -> None:
    channel = make_text_channel()
    guild = make_guild_with(channel)
    await configure(bot, invites=on(action={"delete": True, "timeout_seconds": 600}))
    cog = AutomodCog(bot)
    await cog.on_message(make_message(guild, make_author(), channel, "discord.gg/fremd"))
    assert await case_types(bot) == ["timeout"]
    call = next(c for c in port.calls if c[0] == "timeout")
    assert call[2] is not None


async def test_zweiter_verstoss_der_welle_wird_geloescht_aber_nicht_erneut_bestraft(
    bot: GGBot, port: FakePort
) -> None:
    channel = make_text_channel()
    guild = make_guild_with(channel)
    await configure(
        bot, words=on(entries=[{"type": "word", "pattern": "boese"}], action={"warn": True})
    )
    cog = AutomodCog(bot)
    author = make_author()
    first = make_message(guild, author, channel, "boese")
    second = make_message(guild, author, channel, "boese")
    await cog.on_message(first)
    await cog.on_message(second)
    assert channel.partials[first.id].delete.await_count == 1
    assert channel.partials[second.id].delete.await_count == 1
    assert await case_types(bot) == ["warn"]  # nur eine Verwarnung
    assert channel.send.await_count == 1  # und nur ein Hinweis
    assert await bot.services.automod.stats(GUILD_ID, 7) == {"words": 2}


async def test_spam_welle_wird_komplett_geloescht(bot: GGBot, port: FakePort) -> None:
    channel = make_text_channel()
    guild = make_guild_with(channel)
    await configure(bot, spam=on(messages=3, seconds=30))
    cog = AutomodCog(bot)
    author = make_author()
    messages = [make_message(guild, author, channel, f"m{i}") for i in range(3)]
    for message in messages:
        await cog.on_message(message)
    channel.delete_messages.assert_awaited_once()
    deleted = {o.id for o in channel.delete_messages.await_args.args[0]}
    assert deleted == {m.id for m in messages}


@pytest.mark.parametrize(
    "overrides",
    [
        {"webhook_id": 1},
        {"type": discord.MessageType.new_member},
        {"guild": None},
    ],
)
async def test_webhooks_systemnachrichten_und_dms_werden_ignoriert(
    bot: GGBot, port: FakePort, overrides: dict[str, Any]
) -> None:
    channel = make_text_channel()
    guild = make_guild_with(channel)
    await configure(bot, words=on(entries=[{"type": "word", "pattern": "boese"}]))
    message = make_message(guild, make_author(), channel, "boese")
    for name, value in overrides.items():  # nach dem Bauen, "guild" ist ein Parametername
        setattr(message, name, value)
    await AutomodCog(bot).on_message(message)
    assert channel.partials == {}


async def test_bots_und_moderatoren_werden_nicht_geprueft(bot: GGBot, port: FakePort) -> None:
    channel = make_text_channel()
    guild = make_guild_with(channel)
    await configure(bot, words=on(entries=[{"type": "word", "pattern": "boese"}]))
    cog = AutomodCog(bot)
    robot = make_author(1)
    robot.bot = True
    await cog.on_message(make_message(guild, robot, channel, "boese"))
    await cog.on_message(
        make_message(guild, make_author(2, manage_messages=True), channel, "boese")
    )
    assert channel.partials == {}


async def test_bearbeitung_wird_geprueft_aber_nur_bei_geaendertem_text(
    bot: GGBot, port: FakePort
) -> None:
    channel = make_text_channel()
    guild = make_guild_with(channel)
    await configure(bot, words=on(entries=[{"type": "word", "pattern": "boese"}]))
    cog = AutomodCog(bot)
    author = make_author()
    before = make_message(guild, author, channel, "nett")
    unchanged = make_message(guild, author, channel, "boese")
    unchanged_before = make_message(guild, author, channel, "boese")
    await cog.on_message_edit(unchanged_before, unchanged)  # nur Link-Vorschau o. ä.
    assert channel.partials == {}
    after = make_message(guild, author, channel, "boese")
    await cog.on_message_edit(before, after)
    channel.partials[after.id].delete.assert_awaited_once()


async def test_fehlendes_loeschrecht_wird_gemeldet_nicht_verschluckt(
    bot: GGBot, port: FakePort
) -> None:
    channel, log_channel = make_text_channel(), make_text_channel(LOG_CH)
    guild = make_guild_with(channel, log_channel)
    await configure(
        bot, words=on(entries=[{"type": "word", "pattern": "boese"}]), log_channel_id=LOG_CH
    )
    message = make_message(guild, make_author(), channel, "boese")
    forbidden = discord.Forbidden(cast(Any, SimpleNamespace(status=403, reason="x")), "x")
    channel.get_partial_message(message.id).delete = AsyncMock(side_effect=forbidden)

    await AutomodCog(bot).on_message(message)

    embed = log_channel.send.await_args.kwargs["embed"]
    problems = next(f for f in embed.fields if f.name == "Probleme")
    assert "löschen" in problems.value
    channel.send.assert_not_awaited()  # kein "entfernt"-Hinweis, wenn nichts entfernt wurde


async def test_bereits_geloeschte_nachricht_ist_kein_fehler(bot: GGBot, port: FakePort) -> None:
    channel = make_text_channel()
    guild = make_guild_with(channel)
    await configure(bot, words=on(entries=[{"type": "word", "pattern": "boese"}]))
    message = make_message(guild, make_author(), channel, "boese")
    channel.get_partial_message(message.id).delete = AsyncMock(side_effect=not_found)
    await AutomodCog(bot).on_message(message)  # wirft nicht
    assert await bot.services.automod.stats(GUILD_ID, 7) == {"words": 1}


async def test_fehlgeschlagene_verwarnung_wird_im_log_gemeldet(bot: GGBot, port: FakePort) -> None:
    channel, log_channel = make_text_channel(), make_text_channel(LOG_CH)
    guild = make_guild_with(channel, log_channel)
    await configure(
        bot,
        links=on(domains=["boese.com"], action={"delete": True, "timeout_seconds": 60}),
        log_channel_id=LOG_CH,
    )
    port.fail_on = {"timeout"}
    await AutomodCog(bot).on_message(
        make_message(guild, make_author(), channel, "https://boese.com")
    )
    embed = log_channel.send.await_args.kwargs["embed"]
    problems = next(f for f in embed.fields if f.name == "Probleme")
    assert "Discord hat das verweigert" in problems.value


async def test_ohne_log_kanal_und_ohne_sendrecht_bricht_nichts_ab(
    bot: GGBot, port: FakePort
) -> None:
    channel = make_text_channel()
    channel.send = AsyncMock(
        side_effect=discord.Forbidden(cast(Any, SimpleNamespace(status=403, reason="x")), "x")
    )
    guild = make_guild_with(channel)
    await configure(bot, words=on(entries=[{"type": "word", "pattern": "boese"}]))
    await AutomodCog(bot).on_message(make_message(guild, make_author(), channel, "boese"))


async def test_einstellungs_event_leert_den_cache(bot: GGBot, port: FakePort) -> None:
    channel = make_text_channel()
    guild = make_guild_with(channel)
    await configure(bot, words=on(entries=[{"type": "word", "pattern": "boese"}]))
    cog = AutomodCog(bot)
    await cog.on_message(make_message(guild, make_author(), channel, "boese"))
    await bot.services.settings.save(GUILD_ID, AutomodSettings())  # Dashboard schaltet alles ab
    await cog.on_ggbot_settings_changed(events.SettingsChanged(guild_id=GUILD_ID, module="automod"))
    channel.partials.clear()
    await cog.on_message(make_message(guild, make_author(), channel, "boese"))
    assert channel.partials == {}
    # Fremde Module lassen den Cache in Ruhe.
    await cog.on_ggbot_settings_changed(
        events.SettingsChanged(guild_id=GUILD_ID, module="moderation")
    )


# --- Beitritte ---


def make_joiner(user_id: int, *, age_days: float = 400, is_bot: bool = False) -> MagicMock:
    member = MagicMock(spec=discord.Member)
    member.id = user_id
    member.bot = is_bot
    member.created_at = datetime.now(UTC) - timedelta(days=age_days)
    return member


async def test_zu_junges_konto_wird_gekickt(bot: GGBot, port: FakePort) -> None:
    guild = make_guild_with()
    await configure(bot, accountage=on(min_days=7, action={"action": "kick"}))
    member = make_joiner(900, age_days=1)
    member.guild = guild
    await AutomodCog(bot).on_member_join(member)
    assert port.calls[-1][:2] == ("kick", 900) or any(
        c[0] == "kick" and c[1] == 900 for c in port.calls
    )
    case = await bot.services.moderation.get_case(GUILD_ID, 1)
    assert (case.type, case.moderator_id) == ("kick", BOT)
    assert "Konto zu neu" in (case.reason or "")


async def test_altes_konto_und_bots_bleiben_unbehelligt(bot: GGBot, port: FakePort) -> None:
    guild = make_guild_with()
    await configure(bot, accountage=on(min_days=7))
    for member in (make_joiner(901), make_joiner(902, age_days=0, is_bot=True)):
        member.guild = guild
        await AutomodCog(bot).on_member_join(member)
    assert await case_types(bot) == []


async def test_raid_trifft_die_ganze_welle_und_meldet_im_log_kanal(
    bot: GGBot, port: FakePort
) -> None:
    log_channel = make_text_channel(LOG_CH)
    guild = make_guild_with(log_channel)
    await configure(
        bot,
        raid=on(joins=3, seconds=30, action={"action": "ban"}, mode_minutes=5),
        log_channel_id=LOG_CH,
    )
    cog = AutomodCog(bot)
    for user_id in (801, 802, 803):
        member = make_joiner(user_id)
        member.guild = guild
        await cog.on_member_join(member)
    banned = {c[1] for c in port.calls if c[0] == "ban"}
    assert banned == {801, 802, 803}
    assert await case_types(bot) == ["ban", "ban", "ban"]
    alert = log_channel.send.await_args.args[0]
    assert "Raid erkannt" in alert
    assert "3" in alert

    # Nachzügler im Raid-Modus werden einzeln behandelt, ohne neue Warnung.
    late = make_joiner(804)
    late.guild = guild
    await cog.on_member_join(late)
    assert any(c[0] == "ban" and c[1] == 804 for c in port.calls)
    assert log_channel.send.await_count == 1


async def test_raid_meldet_wenn_einzelne_nicht_behandelt_werden_konnten(
    bot: GGBot, port: FakePort
) -> None:
    log_channel = make_text_channel(LOG_CH)
    guild = make_guild_with(log_channel)
    await configure(
        bot, raid=on(joins=2, seconds=30, action={"action": "kick"}), log_channel_id=LOG_CH
    )
    port.fail_on = {"kick"}
    cog = AutomodCog(bot)
    for user_id in (811, 812):
        member = make_joiner(user_id)
        member.guild = guild
        await cog.on_member_join(member)
    alert = log_channel.send.await_args.args[0]
    assert "2 Mitglieder konnten nicht behandelt werden" in alert


async def test_raid_timeout_aktion(bot: GGBot, port: FakePort) -> None:
    guild = make_guild_with()
    await configure(
        bot,
        raid=on(joins=2, seconds=30, action={"action": "timeout", "timeout_seconds": 3600}),
    )
    cog = AutomodCog(bot)
    for user_id in (821, 822):
        member = make_joiner(user_id)
        member.guild = guild
        await cog.on_member_join(member)
    assert {c[1] for c in port.calls if c[0] == "timeout"} == {821, 822}
