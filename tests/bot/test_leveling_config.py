import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import app_commands
from redis.asyncio import Redis

from ggbot.bot.client import GGBot
from ggbot.bot.cogs.leveling_config import LevelingConfigCog
from ggbot.bot.services import Services
from ggbot.core.config import AppConfig
from ggbot.core.db.engine import Database
from ggbot.core.errors import UserError
from ggbot.core.leveling.importer import MAX_BYTES
from ggbot.core.leveling.xp import total_xp_for_level
from ggbot.core.settings.leveling import LevelingSettings
from tests.bot.test_cog_flows import GUILD_ID, make_interaction, make_member, sent_text
from tests.bot.test_leveling_cog import author_with, role, world
from tests.fakes import invoke


def choice(value: str) -> app_commands.Choice[str]:
    return app_commands.Choice(name=value, value=value)


@pytest.fixture
async def bot(db: Database, redis: Redis) -> GGBot:
    return GGBot(Services.build(AppConfig(), db, redis))


def setup(
    bot: GGBot, roles: dict[int, MagicMock] | None = None
) -> tuple[LevelingConfigCog, MagicMock, MagicMock]:
    guild, _ = world(bot, roles=roles)
    interaction = make_interaction(bot, guild, make_member(1, 5))
    interaction.guild_id = GUILD_ID
    return LevelingConfigCog(bot), interaction, guild


async def load(bot: GGBot) -> LevelingSettings:
    return await bot.services.settings.get(GUILD_ID, LevelingSettings)


def text_channel(channel_id: int) -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = channel_id
    channel.mention = f"<#{channel_id}>"
    return channel


# --- Grundeinstellungen ---


async def test_ein_aus_und_zahlenwerte(bot: GGBot) -> None:
    cog, interaction, _ = setup(bot)
    await invoke(cog.enable, cog, interaction)
    await invoke(cog.xp, cog, interaction, 20, 30)
    await invoke(cog.cooldown, cog, interaction, 45)
    await invoke(cog.multiplier, cog, interaction, 1.5)
    s = await load(bot)
    assert (s.enabled, s.xp_min, s.xp_max, s.cooldown_seconds, s.multiplier) == (
        True,
        20,
        30,
        45,
        1.5,
    )
    await invoke(cog.disable, cog, interaction)
    assert (await load(bot)).enabled is False


async def test_xp_bereich_wird_geprueft_und_nichts_ungueltiges_gespeichert(bot: GGBot) -> None:
    cog, interaction, _ = setup(bot)
    with pytest.raises(UserError) as exc:
        await invoke(cog.xp, cog, interaction, 30, 20)
    assert exc.value.key == "error.leveling_invalid"
    assert (await load(bot)).xp_min == 15


async def test_voice_aendert_nur_angegebene_felder(bot: GGBot) -> None:
    cog, interaction, _ = setup(bot)
    await invoke(cog.voice, cog, interaction, True, 5, 8, False)
    v = (await load(bot)).voice
    assert (v.enabled, v.xp_min, v.xp_max, v.requires_company) == (True, 5, 8, False)
    await invoke(cog.voice, cog, interaction, False, None, None, None)
    v = (await load(bot)).voice
    assert (v.enabled, v.xp_min, v.xp_max, v.requires_company) == (False, 5, 8, False)
    with pytest.raises(UserError):  # min über max
        await invoke(cog.voice, cog, interaction, True, 100, None, None)


async def test_ankuendigung_und_nachricht(bot: GGBot) -> None:
    cog, interaction, _ = setup(bot)
    with pytest.raises(UserError) as exc:
        await invoke(cog.announce, cog, interaction, choice("channel"), None)
    assert exc.value.key == "error.leveling_announce_channel_needed"
    await invoke(cog.announce, cog, interaction, choice("channel"), text_channel(88))
    s = await load(bot)
    assert (s.announce, s.announce_channel_id) == ("channel", 88)
    await invoke(cog.announce, cog, interaction, choice("dm"), None)
    s = await load(bot)
    assert (s.announce, s.announce_channel_id) == ("dm", None)

    await invoke(cog.message, cog, interaction, "GG {user}, Level {level}!")
    assert (await load(bot)).announce_message == "GG {user}, Level {level}!"
    with pytest.raises(UserError) as exc:
        await invoke(cog.message, cog, interaction, "{secret}")
    assert exc.value.key == "error.leveling_template_invalid"
    await invoke(cog.message, cog, interaction, "")
    assert (await load(bot)).announce_message is None


# --- Ausnahmen und Faktoren ---


async def test_ohne_xp_und_faktoren(bot: GGBot) -> None:
    cog, interaction, _ = setup(bot)
    channel = text_channel(55)
    a_role = role(66)
    await invoke(cog.noxp_channel_add, cog, interaction, channel)
    await invoke(cog.noxp_channel_add, cog, interaction, channel)  # doppelt
    await invoke(cog.noxp_role_add, cog, interaction, a_role)
    s = await load(bot)
    assert (s.no_xp_channels, s.no_xp_roles) == ([55], [66])
    await invoke(cog.noxp_channel_remove, cog, interaction, channel)
    await invoke(cog.noxp_role_remove, cog, interaction, a_role)
    s = await load(bot)
    assert (s.no_xp_channels, s.no_xp_roles) == ([], [])

    await invoke(cog.boost_channel_set, cog, interaction, channel, 2.0)
    await invoke(cog.boost_role_set, cog, interaction, a_role, 1.5)
    await invoke(cog.boost_role_set, cog, interaction, a_role, 3.0)  # ersetzt
    s = await load(bot)
    assert (s.channel_multipliers, s.role_multipliers) == ({55: 2.0}, {66: 3.0})
    await invoke(cog.boost_channel_remove, cog, interaction, channel)
    await invoke(cog.boost_role_remove, cog, interaction, a_role)
    await invoke(cog.boost_role_remove, cog, interaction, a_role)  # schon weg: kein Fehler
    s = await load(bot)
    assert (s.channel_multipliers, s.role_multipliers) == ({}, {})


# --- Belohnungen ---


async def test_belohnungen_anlegen_ersetzen_entfernen_auflisten(bot: GGBot) -> None:
    ok, other = role(900, position=3), role(901, position=4)
    cog, interaction, _ = setup(bot, roles={900: ok, 901: other})
    await invoke(cog.reward_add, cog, interaction, 5, ok)
    await invoke(cog.reward_add, cog, interaction, 10, other)
    await invoke(cog.reward_add, cog, interaction, 5, other)  # Level 5 neu belegt
    assert (await load(bot)).rewards == {5: 901, 10: 901}
    await invoke(cog.reward_mode, cog, interaction, choice("replace"))
    assert (await load(bot)).reward_mode == "replace"
    await invoke(cog.reward_remove, cog, interaction, 10)
    assert (await load(bot)).rewards == {5: 901}
    with pytest.raises(UserError) as exc:
        await invoke(cog.reward_remove, cog, interaction, 10)
    assert exc.value.key == "error.leveling_reward_not_found"


async def test_unvergebbare_rolle_wird_sofort_abgelehnt(bot: GGBot) -> None:
    cog, interaction, _ = setup(bot)
    for bad in (role(900, position=10), role(901, position=2, managed=True)):
        with pytest.raises(UserError) as exc:
            await invoke(cog.reward_add, cog, interaction, 5, bad)
        assert exc.value.key == "error.leveling_role_not_assignable"
    assert (await load(bot)).rewards == {}


async def test_belohnungsliste_warnt_bei_geloeschten_und_blockierten_rollen(bot: GGBot) -> None:
    fine, blocked = role(900, position=3), role(901, position=10)
    cog, interaction, _ = setup(bot, roles={900: fine, 901: blocked})
    await bot.services.settings.save(GUILD_ID, LevelingSettings(rewards={1: 900, 2: 901, 3: 999}))
    await invoke(cog.reward_list, cog, interaction)
    lines = sent_text(interaction.response.send_message).splitlines()
    assert lines[1] == "Level 1 → <@&900>"
    assert lines[2].startswith("⚠️ Level 2")
    assert "meine Rolle muss darüber stehen" in lines[2]
    assert lines[3].startswith("⚠️ Level 3")
    assert "existiert nicht mehr" in lines[3]


async def test_leere_belohnungsliste(bot: GGBot) -> None:
    cog, interaction, _ = setup(bot)
    await invoke(cog.reward_list, cog, interaction)
    assert "Noch keine" in sent_text(interaction.response.send_message)


# --- Sync ---


async def test_sync_vergibt_rollen_an_alle_mit_xp(
    bot: GGBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ggbot.bot.cogs import leveling_config

    monkeypatch.setattr(leveling_config, "SYNC_PAUSE_SECONDS", 0)
    r1, r5 = role(900, position=3), role(901, position=3)
    cog, interaction, guild = setup(bot, roles={900: r1, 901: r5})
    await bot.services.settings.save(
        GUILD_ID, LevelingSettings(enabled=True, rewards={1: 900, 5: 901}, reward_mode="stack")
    )
    bot.services.leveling.invalidate(GUILD_ID)
    low, high = author_with(), author_with()
    low.id, high.id = 1, 2
    await bot.services.leveling.give(GUILD_ID, 1, 150)  # Level 1
    await bot.services.leveling.give(GUILD_ID, 2, total_xp_for_level(5))  # Level 5
    await bot.services.leveling.give(GUILD_ID, 3, 500)  # nicht mehr auf dem Server
    members = {1: low, 2: high}
    guild.get_member = MagicMock(side_effect=members.get)

    await invoke(cog.sync, cog, interaction)

    assert [r.id for r in low.add_roles.await_args.args] == [900]
    assert sorted(r.id for r in high.add_roles.await_args.args) == [900, 901]
    reply = sent_text(interaction.followup.send)
    assert "3 Mitglieder geprüft" in reply
    assert "2 geändert" in reply
    assert "1 nicht auf dem Server gefunden" in reply


async def test_sync_ohne_belohnungen_und_gleichzeitig_zweimal(bot: GGBot) -> None:
    cog, interaction, _ = setup(bot)
    with pytest.raises(UserError) as exc:
        await invoke(cog.sync, cog, interaction)
    assert exc.value.key == "error.leveling_no_rewards"
    await bot.services.settings.save(GUILD_ID, LevelingSettings(rewards={1: 900}))
    bot.services.leveling.invalidate(GUILD_ID)
    with bot.services.bulk_gate.acquire(GUILD_ID), pytest.raises(UserError) as busy:
        await invoke(cog.sync, cog, interaction)
    assert busy.value.key == "error.bulk_busy"


# --- Verwaltung ---


async def test_xp_geben_und_level_setzen_passen_die_rollen_an(bot: GGBot) -> None:
    reward = role(900, position=3)
    cog, interaction, _ = setup(bot, roles={900: reward})
    await bot.services.settings.save(GUILD_ID, LevelingSettings(rewards={2: 900}))
    bot.services.leveling.invalidate(GUILD_ID)
    member = author_with()
    member.id = 77
    await invoke(cog.give, cog, interaction, member, 300)  # Level 2
    member.add_roles.assert_awaited_once()
    assert "Level 2" in sent_text(interaction.response.send_message)

    member.roles = [reward]
    await invoke(cog.set_level, cog, interaction, member, 0)
    member.remove_roles.assert_awaited_once()  # Level gesunken: Rolle weg
    assert (await bot.services.leveling.rank(GUILD_ID, 77)).progress.level == 0


async def test_xp_verwaltung_lehnt_bots_und_unsinn_ab(bot: GGBot) -> None:
    cog, interaction, _ = setup(bot)
    robot = author_with()
    robot.bot = True
    with pytest.raises(UserError) as exc:
        await invoke(cog.give, cog, interaction, robot, 10)
    assert exc.value.key == "error.leveling_bot"
    with pytest.raises(UserError):
        await invoke(cog.set_level, cog, interaction, robot, 3)


async def test_zuruecksetzen_braucht_das_bestaetigungswort(bot: GGBot) -> None:
    cog, interaction, _ = setup(bot)
    await bot.services.leveling.give(GUILD_ID, 1, 100)
    await bot.services.leveling.give(GUILD_ID, 2, 100)
    for wrong in ("", "reset", "Reset", "ja"):
        with pytest.raises(UserError) as exc:
            await invoke(cog.reset_all, cog, interaction, wrong)
        assert exc.value.key == "error.leveling_confirm"
    assert (await bot.services.leveling.leaderboard(GUILD_ID)).total == 2  # nichts gelöscht
    await invoke(cog.reset_all, cog, interaction, "RESET")
    assert (await bot.services.leveling.leaderboard(GUILD_ID)).total == 0

    user = SimpleNamespace(id=5, mention="<@5>")
    await bot.services.leveling.give(GUILD_ID, 5, 100)
    await invoke(cog.reset_user, cog, interaction, user)
    assert (await bot.services.leveling.rank(GUILD_ID, 5)).progress.xp == 0
    await invoke(cog.reset_user, cog, interaction, user)
    assert "hatte keine XP" in sent_text(interaction.response.send_message)


# --- Import ---


def attachment(content: bytes, *, size: int | None = None) -> MagicMock:
    mock = MagicMock(spec=discord.Attachment)
    mock.size = len(content) if size is None else size
    mock.read = AsyncMock(return_value=content)
    return mock


async def test_import_aus_mee6_datei(bot: GGBot) -> None:
    cog, interaction, _ = setup(bot)
    await bot.services.leveling.give(GUILD_ID, 111, 9999)
    data = {
        "players": [
            {"id": "111", "xp": 4675, "level": 10},
            {"id": "222", "xp": 500},
            {"id": "kaputt", "xp": 1},
        ]
    }
    await invoke(cog.import_levels, cog, interaction, attachment(json.dumps(data).encode()), None)
    reply = sent_text(interaction.followup.send)
    assert "1 neu" in reply
    assert "1 aktualisiert" in reply
    assert "1 Zeilen übersprungen" in reply
    xp = {r.user_id: r.xp for r in (await bot.services.leveling.leaderboard(GUILD_ID)).rows}
    assert xp == {111: 9999, 222: 500}  # Standard "max": der höhere Wert bleibt

    await invoke(
        cog.import_levels,
        cog,
        interaction,
        attachment(json.dumps(data).encode()),
        choice("replace"),
    )
    assert (await bot.services.leveling.rank(GUILD_ID, 111)).progress.xp == 4675


async def test_import_fehlerfaelle_aendern_nichts(bot: GGBot) -> None:
    cog, interaction, _ = setup(bot)
    await bot.services.leveling.give(GUILD_ID, 1, 100)
    with pytest.raises(UserError) as exc:
        await invoke(cog.import_levels, cog, interaction, attachment(b"{kaputt", size=10), None)
    assert exc.value.key == "error.import_unknown_format"
    with pytest.raises(UserError) as exc:
        await invoke(cog.import_levels, cog, interaction, attachment(b"", size=MAX_BYTES + 1), None)
    assert exc.value.key == "error.import_too_large"
    with pytest.raises(UserError) as exc:
        await invoke(cog.import_levels, cog, interaction, attachment(b"[]"), None)
    assert exc.value.key == "error.import_empty"
    assert (await bot.services.leveling.rank(GUILD_ID, 1)).progress.xp == 100


# --- Status ---


async def test_status(bot: GGBot) -> None:
    cog, interaction, _ = setup(bot)
    await invoke(cog.enable, cog, interaction)
    await invoke(cog.voice, cog, interaction, True, None, None, None)
    await invoke(cog.status, cog, interaction)
    embed = interaction.response.send_message.await_args.kwargs["embed"]
    assert "✅" in embed.description
    assert "Voice-XP: ✅" in embed.description
    # Ohne Members-Intent und mit Voice-XP weist der Status auf mögliche Lücken hin.
    assert any(f.name == "⚠️" for f in embed.fields)
