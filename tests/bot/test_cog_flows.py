"""Die Befehle von Anfang bis Ende, mit Fake-Discord-Objekten und echter Datenbank."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from redis.asyncio import Redis

from ggbot.bot.client import GGBot
from ggbot.bot.cogs import channel_tools
from ggbot.bot.cogs.channel_tools import ChannelToolsCog
from ggbot.bot.cogs.moderation import ModerationCog
from ggbot.bot.services import Services
from ggbot.core.config import AppConfig
from ggbot.core.db.engine import Database
from ggbot.core.errors import UserError
from ggbot.core.settings.schema import EscalationRule, ModerationSettings
from tests.fakes import BOT, FakePort, invoke

GUILD_ID = 7
ACTOR_ID = 11
TARGET_ID = 22


@pytest.fixture
async def bot(db: Database, redis: Redis) -> GGBot:
    return GGBot(Services.build(AppConfig(), db, redis))


def make_member(user_id: int, position: int, guild: Any = None) -> MagicMock:
    member = MagicMock(spec=discord.Member)
    member.id = user_id
    member.mention = f"<@{user_id}>"
    member.top_role = SimpleNamespace(position=position)
    member.guild = guild or SimpleNamespace(owner_id=1)
    return member


def make_guild(bot_position: int = 10) -> MagicMock:
    guild = MagicMock(spec=discord.Guild)
    guild.id = GUILD_ID
    guild.owner_id = 1
    guild.me = make_member(BOT, bot_position)
    guild.default_role = MagicMock(spec=discord.Role)
    members: dict[int, MagicMock] = {}
    guild.members_by_id = members
    guild.get_member = MagicMock(side_effect=members.get)
    # Wer nicht beigetreten ist (siehe join), gibt es für Discord nicht.
    guild.fetch_member = AsyncMock(side_effect=not_found)
    return guild


def not_found(*_: object) -> None:
    raise discord.NotFound(cast(Any, SimpleNamespace(status=404, reason="x")), "x")


def join(guild: MagicMock, *members: MagicMock) -> None:
    for member in members:
        guild.members_by_id[member.id] = member


def make_interaction(
    bot: GGBot, guild: MagicMock, actor: MagicMock, channel: Any = None
) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.client = bot
    interaction.guild = guild
    interaction.guild_id = guild.id
    interaction.user = actor
    interaction.channel = channel
    interaction.app_permissions = discord.Permissions.all()
    interaction.permissions = discord.Permissions.all()
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


def sent_text(mock: Any) -> str:
    call = mock.await_args
    assert call is not None, "es wurde nichts gesendet"
    return str(call.args[0] if call.args else call.kwargs)


# --- Moderation ---


@pytest.fixture
async def mod_cog(bot: GGBot, monkeypatch: pytest.MonkeyPatch) -> tuple[ModerationCog, FakePort]:
    cog = ModerationCog(bot)
    port = FakePort()
    monkeypatch.setattr(cog, "_port", lambda guild: port)
    return cog, port


async def test_warn_von_anfang_bis_ende(
    bot: GGBot, mod_cog: tuple[ModerationCog, FakePort]
) -> None:
    cog, _ = mod_cog
    guild = make_guild()
    actor, target = make_member(ACTOR_ID, 5), make_member(TARGET_ID, 2)
    join(guild, actor, target)
    interaction = make_interaction(bot, guild, actor)

    await invoke(cog.warn, cog, interaction, target, "Spam")

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)
    text = sent_text(interaction.followup.send)
    assert "Case #1" in text
    assert f"<@{TARGET_ID}>" in text
    assert (await bot.services.moderation.get_case(GUILD_ID, 1)).reason == "Spam"


async def test_hierarchie_verletzung_stoppt_vor_jeder_aktion(
    bot: GGBot, mod_cog: tuple[ModerationCog, FakePort]
) -> None:
    cog, port = mod_cog
    guild = make_guild()
    boss = make_member(TARGET_ID, 8)
    low = make_member(ACTOR_ID, 3)
    join(guild, boss, low)
    interaction = make_interaction(bot, guild, low)

    with pytest.raises(UserError) as exc:
        await invoke(cog.kick, cog, interaction, boss, None)

    assert exc.value.key == "error.hierarchy_actor"
    assert port.calls == []
    interaction.response.defer.assert_not_awaited()
    with pytest.raises(UserError):
        await bot.services.moderation.get_case(GUILD_ID, 1)


async def test_bot_unter_dem_ziel_wird_abgelehnt(
    bot: GGBot, mod_cog: tuple[ModerationCog, FakePort]
) -> None:
    cog, _ = mod_cog
    guild = make_guild(bot_position=4)
    actor, target = make_member(ACTOR_ID, 9), make_member(TARGET_ID, 5)
    join(guild, actor, target)
    interaction = make_interaction(bot, guild, actor)
    with pytest.raises(UserError) as exc:
        await invoke(cog.kick, cog, interaction, target, None)
    assert exc.value.key == "error.hierarchy_bot"


async def test_ban_per_id_fuer_nicht_mitglied_braucht_keine_hierarchie(
    bot: GGBot, mod_cog: tuple[ModerationCog, FakePort], monkeypatch: pytest.MonkeyPatch
) -> None:
    cog, port = mod_cog
    guild = make_guild()
    guild.fetch_member = AsyncMock(
        side_effect=discord.NotFound(cast(Any, SimpleNamespace(status=404, reason="x")), "x")
    )
    user = MagicMock(spec=discord.User)
    user.id = TARGET_ID
    user.mention = f"<@{TARGET_ID}>"
    interaction = make_interaction(bot, guild, make_member(ACTOR_ID, 5))

    await invoke(cog.ban, cog, interaction, user, "Betrug", 2)

    assert ("ban", TARGET_ID, f"{ACTOR_ID}: Betrug", 2 * 86400) in port.calls


async def test_sich_selbst_oder_den_bot_bannen_ist_nicht_erlaubt(
    bot: GGBot, mod_cog: tuple[ModerationCog, FakePort]
) -> None:
    cog, port = mod_cog
    guild = make_guild()
    guild.fetch_member = AsyncMock(
        side_effect=discord.NotFound(cast(Any, SimpleNamespace(status=404, reason="x")), "x")
    )
    interaction = make_interaction(bot, guild, make_member(ACTOR_ID, 5))
    for user_id, key in ((ACTOR_ID, "error.hierarchy_self"), (BOT, "error.mod_target_bot")):
        user = MagicMock(spec=discord.User)
        user.id = user_id
        with pytest.raises(UserError) as exc:
            await invoke(cog.ban, cog, interaction, user, None, 0)
        assert exc.value.key == key
    assert port.calls == []


async def test_fehlende_bot_rechte_werden_benannt_bevor_etwas_passiert(
    bot: GGBot, mod_cog: tuple[ModerationCog, FakePort]
) -> None:
    cog, port = mod_cog
    guild = make_guild()
    actor, target = make_member(ACTOR_ID, 5), make_member(TARGET_ID, 2)
    join(guild, actor, target)
    interaction = make_interaction(bot, guild, actor)
    interaction.app_permissions = discord.Permissions(send_messages=True)
    with pytest.raises(UserError) as exc:
        await invoke(cog.kick, cog, interaction, target, None)
    assert exc.value.key == "error.missing_bot_permissions"
    assert "Kick Members" in exc.value.params["permissions"]
    assert port.calls == []


async def test_ungueltige_dauer_wird_vor_dem_defer_gemeldet(
    bot: GGBot, mod_cog: tuple[ModerationCog, FakePort]
) -> None:
    cog, _ = mod_cog
    guild = make_guild()
    actor, target = make_member(ACTOR_ID, 5), make_member(TARGET_ID, 2)
    join(guild, actor, target)
    interaction = make_interaction(bot, guild, actor)
    with pytest.raises(UserError) as exc:
        await invoke(cog.timeout, cog, interaction, target, "bald", None)
    assert exc.value.key == "error.invalid_duration"
    interaction.response.defer.assert_not_awaited()  # Fehler bleibt ephemer


async def test_antwort_meldet_fehlgeschlagene_dm_und_eskalation(
    bot: GGBot, mod_cog: tuple[ModerationCog, FakePort]
) -> None:
    cog, port = mod_cog
    port.dm_ok = False
    await bot.services.settings.save(
        GUILD_ID, ModerationSettings(escalation=[EscalationRule(warns=1, action="kick")])
    )
    guild = make_guild()
    actor, target = make_member(ACTOR_ID, 5), make_member(TARGET_ID, 2)
    join(guild, actor, target)
    interaction = make_interaction(bot, guild, actor)
    await invoke(cog.warn, cog, interaction, target, None)
    text = sent_text(interaction.followup.send)
    assert "Direktnachricht" in text  # Standardsprache Deutsch
    assert "Eskalation" in text
    assert "Kick" in text


async def test_antwort_meldet_fehlgeschlagene_eskalation(
    bot: GGBot, mod_cog: tuple[ModerationCog, FakePort]
) -> None:
    cog, port = mod_cog
    port.fail_on = {"kick"}
    await bot.services.settings.save(
        GUILD_ID, ModerationSettings(escalation=[EscalationRule(warns=1, action="kick")])
    )
    guild = make_guild()
    actor, target = make_member(ACTOR_ID, 5), make_member(TARGET_ID, 2)
    join(guild, actor, target)
    interaction = make_interaction(bot, guild, actor)
    await invoke(cog.warn, cog, interaction, target, None)
    assert "fehlgeschlagen" in sent_text(interaction.followup.send)


# --- Lock / Unlock ---


def make_text_channel(initial: discord.PermissionOverwrite) -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 555
    channel.mention = "<#555>"
    state = {"overwrite": initial}
    channel.overwrites_for = MagicMock(side_effect=lambda role: _copy(state["overwrite"]))

    async def set_permissions(role: object, *, overwrite: Any, reason: str | None = None) -> None:
        state["overwrite"] = overwrite if overwrite is not None else discord.PermissionOverwrite()

    channel.set_permissions = AsyncMock(side_effect=set_permissions)
    channel.permissions_for = MagicMock(return_value=discord.Permissions.all())
    channel.state = state
    return channel


def _copy(overwrite: discord.PermissionOverwrite) -> discord.PermissionOverwrite:
    return discord.PermissionOverwrite(**dict(iter(overwrite)))


@pytest.mark.parametrize("previous", [None, True])
async def test_unlock_stellt_den_vorherigen_zustand_exakt_wieder_her(
    bot: GGBot, previous: bool | None
) -> None:
    cog = ChannelToolsCog(bot)
    guild = make_guild()
    channel = make_text_channel(discord.PermissionOverwrite(send_messages=previous))
    interaction = make_interaction(bot, guild, make_member(ACTOR_ID, 5), channel)

    await invoke(cog.lock, cog, interaction, channel)
    assert channel.state["overwrite"].send_messages is False
    assert "gesperrt" in sent_text(interaction.response.send_message)

    await invoke(cog.unlock, cog, interaction, channel)
    assert channel.state["overwrite"].send_messages is previous
    # Bei "geerbt" bleibt kein leerer Overwrite zurück, sondern er wird entfernt.
    last = channel.set_permissions.await_args.kwargs["overwrite"]
    assert (last is None) == (previous is None)


async def test_lock_zweimal_und_unlock_ohne_lock(bot: GGBot) -> None:
    cog = ChannelToolsCog(bot)
    channel = make_text_channel(discord.PermissionOverwrite())
    interaction = make_interaction(bot, make_guild(), make_member(ACTOR_ID, 5), channel)

    with pytest.raises(UserError) as exc:
        await invoke(cog.unlock, cog, interaction, channel)
    assert exc.value.key == "error.lock_not_locked"

    await invoke(cog.lock, cog, interaction, channel)
    with pytest.raises(UserError) as exc:
        await invoke(cog.lock, cog, interaction, channel)
    assert exc.value.key == "error.lock_already"


async def test_von_hand_gesperrter_kanal_zaehlt_als_bereits_gesperrt(bot: GGBot) -> None:
    cog = ChannelToolsCog(bot)
    channel = make_text_channel(discord.PermissionOverwrite(send_messages=False))
    interaction = make_interaction(bot, make_guild(), make_member(ACTOR_ID, 5), channel)
    with pytest.raises(UserError) as exc:
        await invoke(cog.lock, cog, interaction, channel)
    assert exc.value.key == "error.lock_already"
    channel.set_permissions.assert_not_awaited()


async def test_lock_ohne_discord_recht_hinterlaesst_keine_sperre(bot: GGBot) -> None:
    cog = ChannelToolsCog(bot)
    channel = make_text_channel(discord.PermissionOverwrite())
    forbidden = discord.Forbidden(cast(Any, SimpleNamespace(status=403, reason="x")), "x")
    channel.set_permissions = AsyncMock(side_effect=forbidden)
    interaction = make_interaction(bot, make_guild(), make_member(ACTOR_ID, 5), channel)

    with pytest.raises(UserError) as exc:
        await invoke(cog.lock, cog, interaction, channel)
    assert exc.value.key == "error.mod_forbidden"
    with pytest.raises(UserError) as exc:  # kein Phantom-Eintrag
        await bot.services.channel_locks.previous_state(GUILD_ID, channel.id)
    assert exc.value.key == "error.lock_not_locked"


async def test_lock_prueft_rechte_im_zielkanal(bot: GGBot) -> None:
    cog = ChannelToolsCog(bot)
    channel = make_text_channel(discord.PermissionOverwrite())
    channel.permissions_for = MagicMock(return_value=discord.Permissions(send_messages=True))
    interaction = make_interaction(bot, make_guild(), make_member(ACTOR_ID, 5), channel)
    with pytest.raises(UserError) as exc:
        await invoke(cog.lock, cog, interaction, channel)
    assert exc.value.key == "error.missing_bot_permissions"


# --- Slowmode ---


async def test_slowmode_setzt_und_schaltet_ab(bot: GGBot) -> None:
    cog = ChannelToolsCog(bot)
    channel = make_text_channel(discord.PermissionOverwrite())
    channel.edit = AsyncMock()
    interaction = make_interaction(bot, make_guild(), make_member(ACTOR_ID, 5), channel)

    await invoke(cog.slowmode, cog, interaction, 30, channel)
    assert channel.edit.await_args.kwargs["slowmode_delay"] == 30
    assert "30" in sent_text(interaction.response.send_message)

    await invoke(cog.slowmode, cog, interaction, 0, channel)
    assert channel.edit.await_args.kwargs["slowmode_delay"] == 0
    assert "aus" in sent_text(interaction.response.send_message)


async def test_slowmode_in_nicht_unterstuetztem_kanal(bot: GGBot) -> None:
    cog = ChannelToolsCog(bot)
    interaction = make_interaction(
        bot, make_guild(), make_member(ACTOR_ID, 5), MagicMock(spec=discord.DMChannel)
    )
    with pytest.raises(UserError) as exc:
        await invoke(cog.slowmode, cog, interaction, 5, None)
    assert exc.value.key == "error.not_text_channel"


# --- Purge ---


def make_message(
    message_id: int, *, author: int = 1, age: timedelta = timedelta(minutes=1), **extra: Any
) -> MagicMock:
    message = MagicMock(spec=discord.Message)
    message.id = message_id
    message.author = SimpleNamespace(id=author, bot=False)
    message.content = extra.get("content", "")
    message.attachments = []
    message.embeds = []
    message.pinned = extra.get("pinned", False)
    message.created_at = datetime.now(UTC) - age
    return message


def purge_channel(messages: list[MagicMock]) -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)

    async def history(**kwargs: Any) -> AsyncIterator[MagicMock]:
        for message in messages:
            yield message

    channel.history = MagicMock(side_effect=lambda **kw: history(**kw))
    channel.delete_messages = AsyncMock()
    return channel


@pytest.fixture(autouse=True)
def no_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(channel_tools, "CHUNK_PAUSE_SECONDS", 0)


def deleted_ids(channel: MagicMock) -> list[int]:
    return [o.id for call in channel.delete_messages.await_args_list for o in call.args[0]]


async def test_purge_loescht_die_juengsten_und_ueberspringt_angeheftete(bot: GGBot) -> None:
    cog = ChannelToolsCog(bot)
    messages = [make_message(i, pinned=(i == 2)) for i in range(1, 8)]
    channel = purge_channel(messages)
    interaction = make_interaction(bot, make_guild(), make_member(ACTOR_ID, 5), channel)

    await invoke(cog.purge, cog, interaction, 3, None, None, False, False, False, False)

    assert deleted_ids(channel) == [1, 3, 4]
    assert "3" in sent_text(interaction.followup.send)


async def test_purge_teilt_in_bloecke_zu_hundert(bot: GGBot) -> None:
    cog = ChannelToolsCog(bot)
    channel = purge_channel([make_message(i) for i in range(1, 251)])
    interaction = make_interaction(bot, make_guild(), make_member(ACTOR_ID, 5), channel)

    await invoke(cog.purge, cog, interaction, 250, None, None, False, False, False, False)

    assert [len(c.args[0]) for c in channel.delete_messages.await_args_list] == [100, 100, 50]


async def test_purge_nach_autor_filtert(bot: GGBot) -> None:
    cog = ChannelToolsCog(bot)
    messages = [make_message(1, author=5), make_message(2, author=6), make_message(3, author=5)]
    channel = purge_channel(messages)
    interaction = make_interaction(bot, make_guild(), make_member(ACTOR_ID, 5), channel)
    author = MagicMock(spec=discord.User)
    author.id = 5

    await invoke(cog.purge, cog, interaction, 10, author, None, False, False, False, False)

    assert deleted_ids(channel) == [1, 3]


async def test_purge_ohne_treffer_meldet_das(bot: GGBot) -> None:
    cog = ChannelToolsCog(bot)
    channel = purge_channel([make_message(1, age=timedelta(days=20))])
    interaction = make_interaction(bot, make_guild(), make_member(ACTOR_ID, 5), channel)

    await invoke(cog.purge, cog, interaction, 5, None, None, False, False, False, False)

    channel.delete_messages.assert_not_awaited()
    assert "14 Tagen" in sent_text(interaction.followup.send)


async def test_purge_text_und_linkfilter_brauchen_den_content_intent(bot: GGBot) -> None:
    cog = ChannelToolsCog(bot)
    assert not bot.intents.message_content
    channel = purge_channel([make_message(1)])
    interaction = make_interaction(bot, make_guild(), make_member(ACTOR_ID, 5), channel)

    for kwargs in ({"contains": "spam", "links": False}, {"contains": None, "links": True}):
        with pytest.raises(UserError) as exc:
            await invoke(
                cog.purge,
                cog,
                interaction,
                5,
                None,
                kwargs["contains"],
                False,
                False,
                kwargs["links"],
                False,
            )
        assert exc.value.key == "error.purge_needs_content"
    channel.history.assert_not_called()


async def test_purge_zweimal_gleichzeitig_im_selben_server_wird_abgelehnt(bot: GGBot) -> None:
    cog = ChannelToolsCog(bot)
    channel = purge_channel([make_message(1)])
    interaction = make_interaction(bot, make_guild(), make_member(ACTOR_ID, 5), channel)

    with bot.services.bulk_gate.acquire(GUILD_ID), pytest.raises(UserError) as exc:
        await invoke(cog.purge, cog, interaction, 5, None, None, False, False, False, False)
    assert exc.value.key == "error.bulk_busy"
    # Nach dem Fehler ist der Server wieder frei.
    await invoke(cog.purge, cog, interaction, 5, None, None, False, False, False, False)


async def test_purge_gate_wird_auch_nach_discord_fehler_freigegeben(bot: GGBot) -> None:
    cog = ChannelToolsCog(bot)
    channel = purge_channel([make_message(1)])
    channel.delete_messages = AsyncMock(side_effect=RuntimeError("boom"))
    interaction = make_interaction(bot, make_guild(), make_member(ACTOR_ID, 5), channel)
    with pytest.raises(RuntimeError):
        await invoke(cog.purge, cog, interaction, 5, None, None, False, False, False, False)
    channel.delete_messages = AsyncMock()
    await invoke(cog.purge, cog, interaction, 5, None, None, False, False, False, False)
