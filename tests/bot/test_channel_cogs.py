"""Vorschläge, Sticky und temporäre Sprachkanäle von der Eingabe bis zur Wirkung."""

import re
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from redis.asyncio import Redis

from ggbot.bot import suggestions as sug_ui
from ggbot.bot.client import GGBot
from ggbot.bot.cogs.sticky import REPOST_JOB, StickyCog
from ggbot.bot.cogs.suggestions import SuggestionsCog
from ggbot.bot.cogs.tempvoice import TempVoiceCog
from ggbot.bot.services import Services
from ggbot.core.config import AppConfig
from ggbot.core.db.engine import Database
from ggbot.core.errors import UserError
from ggbot.core.settings.suggestions import SuggestionSettings
from ggbot.core.settings.tempvoice import TempVoiceSettings
from tests.bot.test_cog_flows import sent_text
from tests.bot.ticket_fakes import GUILD_ID, click, make_world, member, text_channel
from tests.fakes import invoke

CH = 70
LOBBY = 80
ADMIN, USER, OTHER = 11, 500, 501


@pytest.fixture
async def bot(db: Database, redis: Redis) -> GGBot:
    return GGBot(Services.build(AppConfig(), db, redis))


def world(bot: GGBot) -> tuple[MagicMock, MagicMock]:
    guild = make_world(bot)
    channel = text_channel(CH)
    channel.send = AsyncMock(return_value=SimpleNamespace(id=900))
    partial = MagicMock()
    partial.edit = AsyncMock()
    partial.delete = AsyncMock()
    channel.get_partial_message = MagicMock(return_value=partial)
    channel.partial = partial
    channel.guild = guild
    guild.channels_by_id[CH] = channel
    guild.name = "Testserver"
    return guild, channel


# ======================= Vorschläge =======================


async def enable_suggestions(bot: GGBot, **kw: Any) -> None:
    data = {"enabled": True, "channel_id": CH} | kw
    await bot.services.settings.save(GUILD_ID, SuggestionSettings.model_validate(data))
    bot.services.suggestions.invalidate(GUILD_ID)


async def test_suggest_postet_embed_mit_knoepfen(bot: GGBot) -> None:
    cog = SuggestionsCog(bot)
    guild, channel = world(bot)
    await enable_suggestions(bot)

    await invoke(cog.suggest, cog, click(bot, guild, member(guild, USER)), "Mehr Emojis")

    kwargs = channel.send.await_args.kwargs
    assert kwargs["embed"].title == "Vorschlag #1"
    assert kwargs["embed"].description == "Mehr Emojis"
    assert [c.custom_id for c in kwargs["view"].children] == ["gg:sug:up:1", "gg:sug:down:1"]
    assert kwargs["allowed_mentions"].everyone is False
    stored = await bot.services.suggestions.get_by_number(GUILD_ID, 1)
    assert stored is not None
    assert (stored.channel_id, stored.message_id) == (CH, 900)


async def test_suggest_ist_ohne_einrichtung_aus(bot: GGBot) -> None:
    cog = SuggestionsCog(bot)
    guild, _ = world(bot)

    with pytest.raises(UserError) as off:
        await invoke(cog.suggest, cog, click(bot, guild, member(guild, USER)), "x")
    assert off.value.key == "error.suggest_off"


async def test_suggest_hat_eine_wartezeit(bot: GGBot) -> None:
    cog = SuggestionsCog(bot)
    guild, _ = world(bot)
    await enable_suggestions(bot)
    who = member(guild, USER)

    await invoke(cog.suggest, cog, click(bot, guild, who), "eins")
    with pytest.raises(UserError) as wait:
        await invoke(cog.suggest, cog, click(bot, guild, who), "zwei")
    assert wait.value.key == "error.cooldown"


async def test_suggest_prueft_text_vor_der_wartezeit(bot: GGBot) -> None:
    cog = SuggestionsCog(bot)
    guild, _ = world(bot)
    await enable_suggestions(bot)
    who = member(guild, USER)

    with pytest.raises(UserError):
        await invoke(cog.suggest, cog, click(bot, guild, who), "   ")
    await invoke(cog.suggest, cog, click(bot, guild, who), "gültig")  # kein Cooldown verbraucht


async def test_suggest_ohne_kanalrechte(bot: GGBot) -> None:
    cog = SuggestionsCog(bot)
    guild, channel = world(bot)
    channel.permissions_for = MagicMock(return_value=discord.Permissions(view_channel=True))
    await enable_suggestions(bot)

    with pytest.raises(UserError) as perms:
        await invoke(cog.suggest, cog, click(bot, guild, member(guild, USER)), "x")
    assert perms.value.key == "error.suggest_channel_perms"
    channel.send.assert_not_awaited()


async def make_suggestion(bot: GGBot, guild: MagicMock) -> None:
    cog = SuggestionsCog(bot)
    await enable_suggestions(bot, dm_author=True)
    await invoke(cog.suggest, cog, click(bot, guild, member(guild, USER)), "Idee")


async def test_knopf_stimmt_ab_und_aktualisiert_das_embed(bot: GGBot) -> None:
    guild, _ = world(bot)
    await make_suggestion(bot, guild)
    voter = member(guild, OTHER)
    button = sug_ui.VoteButton("up", 1)
    interaction = click(bot, guild, voter)

    await button.callback(interaction)

    embed = interaction.response.edit_message.await_args.kwargs["embed"]
    assert "👍 1" in embed.fields[1].value
    # Zweiter Klick nimmt die Stimme zurück.
    again = click(bot, guild, voter)
    await button.callback(again)
    assert "👍 0" in again.response.edit_message.await_args.kwargs["embed"].fields[1].value


async def test_autor_darf_nicht_fuer_sich_stimmen(bot: GGBot) -> None:
    guild, _ = world(bot)
    await make_suggestion(bot, guild)

    with pytest.raises(UserError) as own:
        await sug_ui.VoteButton("up", 1).callback(click(bot, guild, member(guild, USER)))
    assert own.value.key == "error.suggest_self_vote"


async def test_knopf_wird_aus_der_custom_id_gebaut() -> None:
    match = re.fullmatch(sug_ui.VOTE_TEMPLATE, "gg:sug:down:42")
    assert match is not None
    item = await sug_ui.VoteButton.from_custom_id(MagicMock(), MagicMock(), match)
    assert (item.direction, item.suggestion_id) == ("down", 42)


async def test_annehmen_bearbeitet_die_nachricht_und_schreibt_dem_autor(bot: GGBot) -> None:
    cog = SuggestionsCog(bot)
    guild, channel = world(bot)
    await make_suggestion(bot, guild)
    admin = member(guild, ADMIN, manager=True)

    await invoke(cog.approve, cog, click(bot, guild, admin), 1, "Gute Idee")

    edit = channel.partial.edit.await_args.kwargs
    assert edit["view"] is None  # keine Knöpfe mehr
    assert edit["embed"].colour == discord.Colour.green()
    assert edit["embed"].fields[-1].value == "Gute Idee"
    dm = guild.members_by_id[USER].send.await_args
    assert "angenommen" in dm.args[0]
    assert "Gute Idee" in dm.args[0]


async def test_ablehnen_ohne_dm_wenn_ausgeschaltet(bot: GGBot) -> None:
    cog = SuggestionsCog(bot)
    guild, channel = world(bot)
    await make_suggestion(bot, guild)
    await enable_suggestions(bot, dm_author=False)

    await invoke(cog.deny, cog, click(bot, guild, member(guild, ADMIN, manager=True)), 1, None)

    assert channel.partial.edit.await_args.kwargs["embed"].colour == discord.Colour.red()
    guild.members_by_id[USER].send.assert_not_awaited()


async def test_dm_zu_ist_kein_fehler(bot: GGBot) -> None:
    cog = SuggestionsCog(bot)
    guild, _ = world(bot)
    await make_suggestion(bot, guild)
    guild.members_by_id[USER].send = AsyncMock(
        side_effect=discord.Forbidden(SimpleNamespace(status=403, reason="x"), "x")
    )

    await invoke(cog.approve, cog, click(bot, guild, member(guild, ADMIN, manager=True)), 1, None)


async def test_zweite_entscheidung_wird_abgelehnt(bot: GGBot) -> None:
    cog = SuggestionsCog(bot)
    guild, channel = world(bot)
    await make_suggestion(bot, guild)
    admin = member(guild, ADMIN, manager=True)
    await invoke(cog.approve, cog, click(bot, guild, admin), 1, None)

    with pytest.raises(UserError) as twice:
        await invoke(cog.deny, cog, click(bot, guild, admin), 1, None)
    assert twice.value.key == "error.suggest_decided"
    assert channel.partial.edit.await_count == 1


async def test_setup_und_off(bot: GGBot) -> None:
    cog = SuggestionsCog(bot)
    guild, channel = world(bot)
    admin = member(guild, ADMIN, manager=True)

    await invoke(cog.setup, cog, click(bot, guild, admin), channel)
    saved = await bot.services.suggestions.settings(GUILD_ID)
    assert (saved.enabled, saved.channel_id) == (True, CH)

    await invoke(cog.off, cog, click(bot, guild, admin))
    assert (await bot.services.suggestions.settings(GUILD_ID)).enabled is False


# ======================= Sticky =======================


def message_in(guild: MagicMock, channel: MagicMock, author_id: int) -> MagicMock:
    message = MagicMock(spec=discord.Message)
    message.guild = guild
    message.channel = channel
    message.author = SimpleNamespace(id=author_id)
    return message


async def test_sticky_set_postet_sofort_und_merkt_die_nachricht(bot: GGBot) -> None:
    cog = StickyCog(bot)
    guild, channel = world(bot)
    admin = member(guild, ADMIN, manager=True)

    await invoke(cog.set_, cog, click(bot, guild, admin), channel, "Regeln beachten")

    assert channel.send.await_args.kwargs["embed"].description == "Regeln beachten"
    stored = await bot.services.sticky.get(CH)
    assert stored is not None
    assert stored.last_message_id == 900


async def test_neue_nachricht_plant_genau_einen_neupost(bot: GGBot) -> None:
    cog = StickyCog(bot)
    guild, channel = world(bot)
    await bot.services.sticky.set(GUILD_ID, CH, "Regeln", ADMIN)

    for _ in range(5):
        await cog.on_message(message_in(guild, channel, USER))

    async with bot.services.db.session() as session:
        from sqlalchemy import func, select

        from ggbot.core.db.models import ScheduledJob

        count = await session.scalar(
            select(func.count()).select_from(ScheduledJob).where(ScheduledJob.kind == REPOST_JOB)
        )
    assert count == 1


async def test_eigene_nachricht_und_fremde_kanaele_loesen_nichts_aus(bot: GGBot) -> None:
    cog = StickyCog(bot)
    guild, channel = world(bot)
    other = text_channel(CH + 1)
    await bot.services.sticky.set(GUILD_ID, CH, "Regeln", ADMIN)
    assert bot.user is not None

    await cog.on_message(message_in(guild, channel, bot.user.id))  # der Bot selbst
    await cog.on_message(message_in(guild, other, USER))  # Kanal ohne Sticky

    assert await bot.services.sticky.claim_repost(CH) is True  # nichts hat die Sperre gesetzt


async def test_neupost_loescht_die_alte_kopie(bot: GGBot) -> None:
    cog = StickyCog(bot)
    _, channel = world(bot)
    await bot.services.sticky.set(GUILD_ID, CH, "Regeln", ADMIN)
    await bot.services.sticky.set_last(CH, 123)

    await cog.repost(channel)

    channel.get_partial_message.assert_called_with(123)
    channel.partial.delete.assert_awaited_once()
    stored = await bot.services.sticky.get(CH)
    assert stored is not None
    assert stored.last_message_id == 900


async def test_neupost_nach_entfernen_tut_nichts(bot: GGBot) -> None:
    cog = StickyCog(bot)
    _, channel = world(bot)

    await cog.repost(channel)

    channel.send.assert_not_awaited()


async def test_neupost_ohne_rechte_postet_nicht(bot: GGBot) -> None:
    cog = StickyCog(bot)
    _, channel = world(bot)
    await bot.services.sticky.set(GUILD_ID, CH, "Regeln", ADMIN)
    channel.permissions_for = MagicMock(return_value=discord.Permissions(view_channel=True))

    await cog.repost(channel)

    channel.send.assert_not_awaited()


async def test_sticky_entfernen_loescht_die_kopie(bot: GGBot) -> None:
    cog = StickyCog(bot)
    guild, channel = world(bot)
    admin = member(guild, ADMIN, manager=True)
    await bot.services.sticky.set(GUILD_ID, CH, "Regeln", ADMIN)
    await bot.services.sticky.set_last(CH, 123)

    await invoke(cog.remove, cog, click(bot, guild, admin), channel)

    channel.partial.delete.assert_awaited_once()
    assert await bot.services.sticky.get(CH) is None


async def test_sticky_set_ohne_rechte_wird_abgelehnt(bot: GGBot) -> None:
    cog = StickyCog(bot)
    guild, channel = world(bot)
    channel.permissions_for = MagicMock(return_value=discord.Permissions(view_channel=True))

    with pytest.raises(UserError) as perms:
        await invoke(cog.set_, cog, click(bot, guild, member(guild, ADMIN)), channel, "x")
    assert perms.value.key == "error.sticky_perms"
    assert await bot.services.sticky.get(CH) is None


async def test_sticky_liste(bot: GGBot) -> None:
    cog = StickyCog(bot)
    guild, _ = world(bot)
    interaction = click(bot, guild, member(guild, ADMIN))
    await invoke(cog.list_, cog, interaction)
    assert "keine" in sent_text(interaction.response.send_message).lower()

    await bot.services.sticky.set(GUILD_ID, CH, "Regeln", ADMIN)
    listing = click(bot, guild, member(guild, ADMIN))
    await invoke(cog.list_, cog, listing)
    assert f"<#{CH}>" in sent_text(listing.response.send_message)


# ======================= Temporäre Sprachkanäle =======================


def voice_world(bot: GGBot) -> tuple[MagicMock, MagicMock, list[MagicMock]]:
    guild = make_world(bot)
    guild.me.guild_permissions = discord.Permissions(manage_channels=True, move_members=True)
    lobby = MagicMock(spec=discord.VoiceChannel)
    lobby.id = LOBBY
    lobby.members = []
    lobby.category = None
    guild.channels_by_id[LOBBY] = lobby
    made: list[MagicMock] = []

    async def create(name: str, **kwargs: Any) -> MagicMock:
        channel = MagicMock(spec=discord.VoiceChannel)
        channel.id = 9000 + len(made)
        channel.name = name
        channel.members = []
        channel.create_kwargs = kwargs
        channel.delete = AsyncMock()
        channel.edit = AsyncMock()
        channel.set_permissions = AsyncMock()
        channel.guild = guild
        guild.channels_by_id[channel.id] = channel
        made.append(channel)
        return channel

    guild.create_voice_channel = AsyncMock(side_effect=create)
    guild.voice_made = made
    return guild, lobby, made


async def enable_voice(bot: GGBot, **kw: Any) -> None:
    data = {"enabled": True, "lobby_channel_id": LOBBY} | kw
    await bot.services.settings.save(GUILD_ID, TempVoiceSettings.model_validate(data))
    bot.services.tempvoice.invalidate(GUILD_ID)


def state(channel: MagicMock | None) -> SimpleNamespace:
    return SimpleNamespace(channel=channel)


def voice_member(guild: MagicMock, user_id: int) -> MagicMock:
    who = member(guild, user_id)
    who.move_to = AsyncMock()
    return who


async def test_lobby_betreten_legt_kanal_an_und_verschiebt(bot: GGBot) -> None:
    cog = TempVoiceCog(bot)
    guild, lobby, made = voice_world(bot)
    await enable_voice(bot, name_template="{user}s Raum", user_limit=4)
    who = voice_member(guild, USER)

    await cog.on_voice_state_update(who, state(None), state(lobby))  # type: ignore[arg-type]

    assert len(made) == 1
    assert made[0].name == "User500s Raum"
    assert made[0].create_kwargs["user_limit"] == 4
    overwrite = made[0].create_kwargs["overwrites"][who]
    assert overwrite.connect is True
    who.move_to.assert_awaited_once_with(made[0], reason="Temporärer Sprachkanal")
    assert await bot.services.tempvoice.owner(made[0].id) == USER


async def test_anderer_kanal_oder_ausgeschaltet_legt_nichts_an(bot: GGBot) -> None:
    cog = TempVoiceCog(bot)
    guild, lobby, made = voice_world(bot)
    other = MagicMock(spec=discord.VoiceChannel)
    other.id = 81
    other.members = []
    who = voice_member(guild, USER)

    await cog.on_voice_state_update(who, state(None), state(lobby))  # type: ignore[arg-type]  # aus
    await enable_voice(bot)
    await cog.on_voice_state_update(who, state(None), state(other))  # type: ignore[arg-type]
    await cog.on_voice_state_update(who, state(lobby), state(lobby))  # type: ignore[arg-type]

    assert made == []


async def test_lobby_wechsel_loest_keine_kanalflut_aus(bot: GGBot) -> None:
    cog = TempVoiceCog(bot)
    guild, lobby, made = voice_world(bot)
    await enable_voice(bot)
    who = voice_member(guild, USER)

    for _ in range(4):
        await cog.on_voice_state_update(who, state(None), state(lobby))  # type: ignore[arg-type]

    assert len(made) == 1


async def test_obergrenze_gleichzeitiger_kanaele(bot: GGBot) -> None:
    cog = TempVoiceCog(bot)
    guild, lobby, made = voice_world(bot)
    await enable_voice(bot, max_channels=1)

    await cog.on_voice_state_update(voice_member(guild, USER), state(None), state(lobby))  # type: ignore[arg-type]
    await cog.on_voice_state_update(voice_member(guild, OTHER), state(None), state(lobby))  # type: ignore[arg-type]

    assert len(made) == 1


async def test_ohne_bot_rechte_wird_nichts_angelegt(bot: GGBot) -> None:
    cog = TempVoiceCog(bot)
    guild, lobby, made = voice_world(bot)
    guild.me.guild_permissions = discord.Permissions.none()
    await enable_voice(bot)

    await cog.on_voice_state_update(voice_member(guild, USER), state(None), state(lobby))  # type: ignore[arg-type]

    assert made == []


async def test_leerer_kanal_wird_geloescht_besetzter_nicht(bot: GGBot) -> None:
    cog = TempVoiceCog(bot)
    guild, lobby, made = voice_world(bot)
    await enable_voice(bot)
    who = voice_member(guild, USER)
    await cog.on_voice_state_update(who, state(None), state(lobby))  # type: ignore[arg-type]
    channel = made[0]

    channel.members = [SimpleNamespace(id=OTHER)]
    await cog.on_voice_state_update(who, state(channel), state(None))  # type: ignore[arg-type]
    channel.delete.assert_not_awaited()

    channel.members = []
    await cog.on_voice_state_update(who, state(channel), state(None))  # type: ignore[arg-type]
    channel.delete.assert_awaited_once()
    assert await bot.services.tempvoice.owner(channel.id) is None


async def test_normaler_kanal_wird_nie_geloescht(bot: GGBot) -> None:
    cog = TempVoiceCog(bot)
    guild, _, _ = voice_world(bot)
    plain = MagicMock(spec=discord.VoiceChannel)
    plain.id = 55
    plain.members = []
    plain.delete = AsyncMock()

    await cog.on_voice_state_update(voice_member(guild, USER), state(plain), state(None))  # type: ignore[arg-type]

    plain.delete.assert_not_awaited()


async def test_fehlgeschlagenes_verschieben_raeumt_den_kanal_wieder_ab(bot: GGBot) -> None:
    cog = TempVoiceCog(bot)
    guild, lobby, made = voice_world(bot)
    await enable_voice(bot)
    who = voice_member(guild, USER)
    who.move_to = AsyncMock(
        side_effect=discord.HTTPException(SimpleNamespace(status=400, reason="x"), "x")
    )

    await cog.on_voice_state_update(who, state(None), state(lobby))  # type: ignore[arg-type]

    made[0].delete.assert_awaited_once()
    assert await bot.services.tempvoice.owner(made[0].id) is None


async def test_neustart_raeumt_verwaiste_eintraege_auf(bot: GGBot) -> None:
    cog = TempVoiceCog(bot)
    guild, _, _ = voice_world(bot)
    gone = MagicMock(spec=discord.VoiceChannel)
    gone.id = 9990
    empty = MagicMock(spec=discord.VoiceChannel)
    empty.id = 9991
    empty.members = []
    empty.delete = AsyncMock()
    busy = MagicMock(spec=discord.VoiceChannel)
    busy.id = 9992
    busy.members = [SimpleNamespace(id=1)]
    busy.delete = AsyncMock()
    guild.channels_by_id.update({9991: empty, 9992: busy})
    for cid in (9990, 9991, 9992):
        await bot.services.tempvoice.register(GUILD_ID, cid, USER)

    await cog.reconcile(guild)

    assert await bot.services.tempvoice.channel_ids(GUILD_ID) == [9992]
    empty.delete.assert_awaited_once()
    busy.delete.assert_not_awaited()


async def in_own_channel(bot: GGBot, user_id: int = USER) -> tuple[MagicMock, MagicMock, MagicMock]:
    guild, lobby, made = voice_world(bot)
    await enable_voice(bot)
    who = voice_member(guild, user_id)
    await TempVoiceCog(bot).on_voice_state_update(who, state(None), state(lobby))  # type: ignore[arg-type]
    channel = made[0]
    who.voice = SimpleNamespace(channel=channel)
    return guild, channel, who


async def test_besitzer_aendert_name_limit_und_sperre(bot: GGBot) -> None:
    cog = TempVoiceCog(bot)
    guild, channel, who = await in_own_channel(bot)

    await invoke(cog.name, cog, click(bot, guild, who), "  Zocken\nHier ")
    await invoke(cog.limit, cog, click(bot, guild, who), 3)
    await invoke(cog.lock, cog, click(bot, guild, who))
    await invoke(cog.unlock, cog, click(bot, guild, who))

    assert channel.edit.await_args_list[0].kwargs["name"] == "Zocken Hier"
    assert channel.edit.await_args_list[1].kwargs["user_limit"] == 3
    calls = channel.set_permissions.await_args_list
    assert calls[0].kwargs["connect"] is False
    assert calls[1].kwargs["connect"] is None


async def test_fremder_darf_den_kanal_nicht_aendern(bot: GGBot) -> None:
    cog = TempVoiceCog(bot)
    guild, channel, _ = await in_own_channel(bot)
    stranger = voice_member(guild, OTHER)
    stranger.voice = SimpleNamespace(channel=channel)

    for command, args in ((cog.name, ("x",)), (cog.limit, (2,)), (cog.lock, ()), (cog.unlock, ())):
        with pytest.raises(UserError) as denied:
            await invoke(command, cog, click(bot, guild, stranger), *args)
        assert denied.value.key == "error.voice_not_owner"
    channel.edit.assert_not_awaited()
    channel.set_permissions.assert_not_awaited()


async def test_ohne_sprachkanal_gibt_es_nichts_zu_aendern(bot: GGBot) -> None:
    cog = TempVoiceCog(bot)
    guild, _, _ = voice_world(bot)
    who = voice_member(guild, USER)
    who.voice = None

    with pytest.raises(UserError) as nowhere:
        await invoke(cog.lock, cog, click(bot, guild, who))
    assert nowhere.value.key == "error.voice_not_in_channel"


async def test_umbenennen_hat_eine_wartezeit_und_leere_namen_scheitern(bot: GGBot) -> None:
    cog = TempVoiceCog(bot)
    guild, _, who = await in_own_channel(bot)

    with pytest.raises(UserError) as empty:
        await invoke(cog.name, cog, click(bot, guild, who), "\n\x07")
    assert empty.value.key == "error.voice_name_empty"
    await invoke(cog.name, cog, click(bot, guild, who), "Eins")
    with pytest.raises(UserError) as wait:
        await invoke(cog.name, cog, click(bot, guild, who), "Zwei")
    assert wait.value.key == "error.cooldown"


async def test_verweigerte_aenderung_wird_freundlich_gemeldet(bot: GGBot) -> None:
    cog = TempVoiceCog(bot)
    guild, channel, who = await in_own_channel(bot)
    channel.edit = AsyncMock(
        side_effect=discord.Forbidden(SimpleNamespace(status=403, reason="x"), "x")
    )

    with pytest.raises(UserError) as forbidden:
        await invoke(cog.limit, cog, click(bot, guild, who), 2)
    assert forbidden.value.key == "error.voice_forbidden"


async def test_tempvoice_setup_speichert_und_prueft(bot: GGBot) -> None:
    cog = TempVoiceCog(bot)
    guild, lobby, _ = voice_world(bot)
    admin = member(guild, ADMIN, manager=True)

    await invoke(cog.setup, cog, click(bot, guild, admin), lobby, None, "Raum {number}", 5)
    saved = await bot.services.tempvoice.settings(GUILD_ID)
    assert (saved.enabled, saved.lobby_channel_id, saved.name_template, saved.user_limit) == (
        True,
        LOBBY,
        "Raum {number}",
        5,
    )

    with pytest.raises(UserError) as bad:
        await invoke(cog.setup, cog, click(bot, guild, admin), lobby, None, "{nope}", None)
    assert bad.value.key == "error.tempvoice_invalid"

    guild.me.guild_permissions = discord.Permissions.none()
    with pytest.raises(UserError) as perms:
        await invoke(cog.setup, cog, click(bot, guild, admin), lobby, None, None, None)
    assert perms.value.key == "error.tempvoice_perms"


async def test_tempvoice_off_und_status(bot: GGBot) -> None:
    cog = TempVoiceCog(bot)
    guild, _, _ = voice_world(bot)
    admin = member(guild, ADMIN, manager=True)
    off = click(bot, guild, admin)
    await invoke(cog.status, cog, off)
    assert "aus" in sent_text(off.response.send_message)

    await enable_voice(bot)
    on = click(bot, guild, admin)
    await invoke(cog.status, cog, on)
    assert f"<#{LOBBY}>" in sent_text(on.response.send_message)

    await invoke(cog.off, cog, click(bot, guild, admin))
    assert (await bot.services.tempvoice.settings(GUILD_ID)).enabled is False
