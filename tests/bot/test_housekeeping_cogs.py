"""Verifizierung und Auto-Löschen von der Eingabe bis zur Wirkung, mit Fake-Discord-Objekten."""

import asyncio
import datetime as dt
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from redis.asyncio import Redis

from ggbot.bot import verification as ui
from ggbot.bot.client import GGBot
from ggbot.bot.cogs import autodelete as autodelete_module
from ggbot.bot.cogs.autodelete import AutodeleteCog
from ggbot.bot.cogs.verification import VerificationCog
from ggbot.bot.services import Services
from ggbot.core.autodelete import rules as ad_rules
from ggbot.core.config import AppConfig
from ggbot.core.db.engine import Database
from ggbot.core.errors import UserError
from ggbot.core.settings.autodelete import AutodeleteSettings
from ggbot.core.settings.verification import VerificationSettings
from ggbot.core.verification import rules as v_rules
from tests.bot.test_cog_flows import sent_text
from tests.bot.test_panels import role
from tests.bot.ticket_fakes import GUILD_ID, click, make_world, member, text_channel
from tests.fakes import invoke

VERIFIED = 30
CH = 70
ADMIN = 11
USER = 500


@pytest.fixture
async def bot(db: Database, redis: Redis) -> GGBot:
    return GGBot(Services.build(AppConfig(), db, redis))


@pytest.fixture(autouse=True)
def no_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(autodelete_module, "SINGLE_PAUSE", 0)


def admin(guild: MagicMock, position: int = 5) -> MagicMock:
    who = member(guild, ADMIN, manager=True)
    who.top_role = SimpleNamespace(position=position)  # über der Verifizierungs-Rolle (2)
    return who


# ======================= Verifizierung =======================


def v_world(bot: GGBot, *, role_position: int = 2) -> tuple[MagicMock, MagicMock]:
    verified = role(VERIFIED, position=role_position)
    guild = make_world(bot, verified)
    channel = text_channel(CH)
    channel.send = AsyncMock(return_value=SimpleNamespace(id=900))
    partial = MagicMock()
    partial.delete = AsyncMock()
    channel.get_partial_message = MagicMock(return_value=partial)
    channel.partial = partial
    guild.get_channel = MagicMock(side_effect={CH: channel}.get)
    return guild, channel


async def configure(bot: GGBot, **kw: Any) -> None:
    data = {"enabled": True, "role_id": VERIFIED, "mode": "button"} | kw
    await bot.services.settings.save(GUILD_ID, VerificationSettings.model_validate(data))
    bot.services.verification.invalidate(GUILD_ID)


async def test_setup_postet_das_panel_und_schaltet_ein(bot: GGBot) -> None:
    cog = VerificationCog(bot)
    guild, channel = v_world(bot)
    interaction = click(bot, guild, admin(guild))

    await invoke(cog.setup, cog, interaction, channel, guild.get_role(VERIFIED), None, None, None)

    kwargs = channel.send.await_args.kwargs
    assert kwargs["embed"].title == "Verifizierung"
    assert kwargs["view"].children[0].custom_id == ui.START_ID
    assert kwargs["allowed_mentions"].roles is False
    saved = await bot.services.verification.settings(GUILD_ID)
    assert (saved.enabled, saved.role_id, saved.channel_id, saved.mode) == (
        True,
        VERIFIED,
        CH,
        "button",
    )
    assert saved.panel_message_id == 900
    assert "<#70>" in sent_text(interaction.followup.send)


async def test_setup_mit_eigenem_text_und_code_modus(bot: GGBot) -> None:
    cog = VerificationCog(bot)
    guild, channel = v_world(bot)

    await invoke(
        cog.setup, cog, click(bot, guild, admin(guild)), channel, guild.get_role(VERIFIED),
        SimpleNamespace(value="code"), "Willkommen", "Lies die Regeln",
    )  # fmt: skip

    embed = channel.send.await_args.kwargs["embed"]
    assert (embed.title, embed.description) == ("Willkommen", "Lies die Regeln")
    assert (await bot.services.verification.settings(GUILD_ID)).mode == "code"


async def test_erneutes_setup_ersetzt_das_alte_panel(bot: GGBot) -> None:
    cog = VerificationCog(bot)
    guild, channel = v_world(bot)
    await invoke(
        cog.setup,
        cog,
        click(bot, guild, admin(guild)),
        channel,
        guild.get_role(VERIFIED),
        None,
        None,
        None,
    )
    channel.send = AsyncMock(return_value=SimpleNamespace(id=901))

    await invoke(
        cog.setup,
        cog,
        click(bot, guild, admin(guild)),
        channel,
        guild.get_role(VERIFIED),
        None,
        None,
        None,
    )

    channel.get_partial_message.assert_called_with(900)
    channel.partial.delete.assert_awaited_once()
    assert (await bot.services.verification.settings(GUILD_ID)).panel_message_id == 901


async def test_setup_verlangt_dass_der_akteur_die_rolle_weitergeben_darf(bot: GGBot) -> None:
    """Sonst wäre ein Panel der Weg, sich oder anderen eine höhere Rolle zu verschaffen."""
    cog = VerificationCog(bot)
    guild, channel = v_world(bot)

    with pytest.raises(UserError) as error:
        await invoke(
            cog.setup, cog, click(bot, guild, admin(guild, position=2)), channel,
            guild.get_role(VERIFIED), None, None, None,
        )  # fmt: skip

    assert error.value.key == "error.hierarchy_actor"
    channel.send.assert_not_awaited()


@pytest.mark.parametrize("case", ["role_too_high", "managed", "channel_perms"])
async def test_setup_lehnt_unmoegliches_ab(bot: GGBot, case: str) -> None:
    cog = VerificationCog(bot)
    guild, channel = v_world(bot, role_position=10 if case == "role_too_high" else 2)
    the_role = guild.get_role(VERIFIED)
    if case == "managed":
        the_role.managed = True
    if case == "channel_perms":
        channel.permissions_for = MagicMock(return_value=discord.Permissions(view_channel=True))

    with pytest.raises(UserError) as error:
        await invoke(
            cog.setup, cog, click(bot, guild, admin(guild)), channel, the_role, None, None, None
        )

    assert error.value.key in {"error.role_unassignable", "error.missing_bot_permissions"}
    channel.send.assert_not_awaited()
    assert (await bot.services.verification.settings(GUILD_ID)).enabled is False


async def test_off_und_status(bot: GGBot) -> None:
    cog = VerificationCog(bot)
    guild, _ = v_world(bot)
    await configure(bot, channel_id=CH)
    interaction = click(bot, guild, admin(guild))

    await invoke(cog.status, cog, interaction)
    assert "button" in sent_text(interaction.response.send_message)
    await invoke(cog.off, cog, click(bot, guild, admin(guild)))

    assert (await bot.services.verification.settings(GUILD_ID)).enabled is False


async def start(bot: GGBot, guild: MagicMock, user: MagicMock) -> MagicMock:
    interaction = click(bot, guild, user)
    await ui.StartButton().callback(interaction)
    return interaction


async def test_knopf_modus_gibt_die_rolle(bot: GGBot) -> None:
    guild, _ = v_world(bot)
    await configure(bot)
    user = member(guild, USER)

    interaction = await start(bot, guild, user)

    user.add_roles.assert_awaited_once()
    assert user.add_roles.await_args.args[0].id == VERIFIED
    assert "verifiziert" in sent_text(interaction.response.send_message)


async def test_wer_die_rolle_schon_hat_bekommt_sie_nicht_nochmal(bot: GGBot) -> None:
    guild, _ = v_world(bot)
    await configure(bot)
    user = member(guild, USER, VERIFIED)

    interaction = await start(bot, guild, user)

    user.add_roles.assert_not_awaited()
    assert "bereits" in sent_text(interaction.response.send_message)


async def test_ausgeschaltete_verifizierung_gibt_nichts(bot: GGBot) -> None:
    guild, _ = v_world(bot)
    await configure(bot, enabled=False, role_id=None)
    user = member(guild, USER)

    with pytest.raises(UserError) as error:
        await start(bot, guild, user)

    assert error.value.key == "error.verify_off"
    user.add_roles.assert_not_awaited()


@pytest.mark.parametrize("case", ["deleted", "too_high", "forbidden"])
async def test_rolle_nicht_vergebbar_ist_ein_fehler_des_servers(bot: GGBot, case: str) -> None:
    guild, _ = v_world(bot, role_position=10 if case == "too_high" else 2)
    await configure(bot, role_id=999 if case == "deleted" else VERIFIED)
    user = member(guild, USER)
    if case == "forbidden":
        user.add_roles = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "no"))

    with pytest.raises(UserError) as error:
        await start(bot, guild, user)

    assert error.value.key == "error.verify_role_unavailable"


async def test_code_modus_sendet_bild_und_eingabeknopf(bot: GGBot, redis: Redis) -> None:
    guild, _ = v_world(bot)
    await configure(bot, mode="code")
    user = member(guild, USER)

    interaction = await start(bot, guild, user)

    kwargs = interaction.response.send_message.await_args.kwargs
    assert kwargs["ephemeral"] is True
    assert kwargs["file"].filename == "code.png"
    assert kwargs["view"].children[0].custom_id == ui.ENTER_ID
    code = await redis.get(f"gg:verify:code:{GUILD_ID}:{USER}")
    assert code is not None
    assert len(code) == v_rules.CODE_LENGTH
    sent = str(interaction.response.send_message.await_args)
    assert str(code) not in sent  # der Code steht nie im Text
    user.add_roles.assert_not_awaited()  # erst die Aufgabe


async def test_neues_bild_erst_nach_der_pause(bot: GGBot) -> None:
    guild, _ = v_world(bot)
    await configure(bot, mode="code")
    user = member(guild, USER)
    await start(bot, guild, user)

    with pytest.raises(UserError) as error:
        await start(bot, guild, user)

    assert error.value.key == "error.cooldown"


async def submit(bot: GGBot, guild: MagicMock, user: MagicMock, answer: str) -> MagicMock:
    interaction = click(bot, guild, user)
    modal = ui.CodeModal("de")
    modal.answer._value = answer
    await modal.on_submit(interaction)
    return interaction


async def test_richtige_antwort_gibt_die_rolle(bot: GGBot) -> None:
    guild, _ = v_world(bot)
    await configure(bot, mode="code")
    user = member(guild, USER)
    await bot.services.verification.begin(GUILD_ID, USER, "AB2CD")

    interaction = await submit(bot, guild, user, "ab2cd")

    user.add_roles.assert_awaited_once()
    assert "verifiziert" in sent_text(interaction.response.send_message)


async def test_falsche_antworten_sperren_nach_dem_letzten_versuch(bot: GGBot) -> None:
    guild, _ = v_world(bot)
    await configure(bot, mode="code")
    user = member(guild, USER)
    await bot.services.verification.begin(GUILD_ID, USER, "AB2CD")

    first = await submit(bot, guild, user, "xxxxx")
    assert f"{v_rules.MAX_ATTEMPTS - 1}" in sent_text(first.response.send_message)
    await submit(bot, guild, user, "xxxxx")
    last = await submit(bot, guild, user, "xxxxx")

    assert "Minuten" in sent_text(last.response.send_message)
    user.add_roles.assert_not_awaited()
    with pytest.raises(UserError):  # gesperrt: kein neues Bild
        await start(bot, guild, user)


async def test_abgelaufene_aufgabe(bot: GGBot) -> None:
    guild, _ = v_world(bot)
    await configure(bot, mode="code")
    user = member(guild, USER)

    interaction = await submit(bot, guild, user, "AB2CD")

    assert "abgelaufen" in sent_text(interaction.response.send_message)
    user.add_roles.assert_not_awaited()


async def test_antwort_nach_dem_ausschalten_gibt_keine_rolle(bot: GGBot) -> None:
    guild, _ = v_world(bot)
    await configure(bot, mode="code")
    user = member(guild, USER)
    await bot.services.verification.begin(GUILD_ID, USER, "AB2CD")
    await configure(bot, mode="code", enabled=False, role_id=None)

    with pytest.raises(UserError) as error:
        await submit(bot, guild, user, "AB2CD")

    assert error.value.key == "error.verify_off"
    user.add_roles.assert_not_awaited()


async def test_eingabeknopf_oeffnet_das_fenster(bot: GGBot) -> None:
    guild, _ = v_world(bot)
    interaction = click(bot, guild, member(guild, USER))

    await ui.EnterButton().callback(interaction)

    modal = interaction.response.send_modal.await_args.args[0]
    assert isinstance(modal, ui.CodeModal)


async def test_knoepfe_sind_dauerhaft_registriert(bot: GGBot) -> None:
    """Nach einem Neustart müssen alte Panels weiter laufen: dafür die dynamischen Elemente."""
    cog = VerificationCog(bot)
    registered = bot._connection._view_store._dynamic_items

    await cog.cog_load()
    assert {ui.StartButton, ui.EnterButton} <= set(registered.values())

    await cog.cog_unload()
    assert not {ui.StartButton, ui.EnterButton} & set(registered.values())


# ======================= Auto-Löschen =======================


NOW = dt.datetime.now(dt.UTC)


def fake_message(mid: int, age: dt.timedelta, *, pinned: bool = False) -> SimpleNamespace:
    return SimpleNamespace(id=mid, created_at=NOW - age, pinned=pinned)


def ad_world(bot: GGBot, messages: list[SimpleNamespace]) -> tuple[MagicMock, MagicMock]:
    guild = make_world(bot)
    channel = text_channel(CH)
    channel.history_calls = []
    stored = list(messages)

    def history(limit: int | None = None, before: Any = None, **_: Any) -> AsyncIterator[Any]:
        channel.history_calls.append(before)

        async def generate() -> AsyncIterator[Any]:
            cutoff = discord.utils.snowflake_time(before.id) if before else None
            for message in stored[: limit or len(stored)]:
                if cutoff is None or message.created_at <= cutoff:
                    yield message

        return generate()

    channel.history = history
    channel.delete_messages = AsyncMock()
    partial = MagicMock()
    partial.delete = AsyncMock()
    channel.get_partial_message = MagicMock(return_value=partial)
    channel.partial = partial
    guild.get_channel = MagicMock(side_effect={CH: channel}.get)
    guild.channels = [channel]
    bot.get_guild = MagicMock(return_value=guild)  # type: ignore[method-assign]
    return guild, channel


async def ad_configure(bot: GGBot, *rules_: dict[str, Any], enabled: bool = True) -> None:
    await bot.services.settings.save(
        GUILD_ID, AutodeleteSettings.model_validate({"enabled": enabled, "rules": list(rules_)})
    )
    bot.services.autodelete.invalidate(GUILD_ID)


def rule(minutes: int = 60, channel: int = CH, **kw: Any) -> dict[str, Any]:
    return {"channel_id": str(channel), "max_age_minutes": minutes, **kw}


def deleted_in_bulk(channel: MagicMock) -> list[int]:
    return [o.id for call in channel.delete_messages.await_args_list for o in call.args[0]]


async def test_loescht_alte_nachrichten_gesammelt_und_laesst_junge_stehen(bot: GGBot) -> None:
    guild, channel = ad_world(
        bot,
        [
            fake_message(3, dt.timedelta(minutes=5)),
            fake_message(2, dt.timedelta(hours=2)),
            fake_message(1, dt.timedelta(days=2)),
        ],
    )
    await ad_configure(bot, rule(60))

    deleted = await AutodeleteCog(bot).run_guild(guild)

    assert deleted == 2
    assert sorted(deleted_in_bulk(channel)) == [1, 2]
    channel.partial.delete.assert_not_awaited()


async def test_die_suche_beginnt_am_stichtag_nicht_bei_jetzt(bot: GGBot) -> None:
    guild, channel = ad_world(bot, [])
    await ad_configure(bot, rule(120))
    started = dt.datetime.now(dt.UTC)

    await AutodeleteCog(bot).run_guild(guild)

    finished = dt.datetime.now(dt.UTC)
    boundary = discord.utils.snowflake_time(channel.history_calls[0].id)
    one_second = dt.timedelta(seconds=1)  # Snowflakes zählen Millisekunden
    assert started - dt.timedelta(minutes=120) - one_second <= boundary
    assert boundary <= finished - dt.timedelta(minutes=120) + one_second


async def test_angeheftete_bleiben_nur_wenn_die_regel_es_will(bot: GGBot) -> None:
    messages = [
        fake_message(1, dt.timedelta(hours=3), pinned=True),
        fake_message(2, dt.timedelta(hours=3)),
    ]
    guild, channel = ad_world(bot, messages)
    await ad_configure(bot, rule(60, keep_pinned=True))
    await AutodeleteCog(bot).run_guild(guild)
    assert deleted_in_bulk(channel) == [2]

    guild, channel = ad_world(bot, messages)
    await bot.services.redis.flushdb()
    await ad_configure(bot, rule(60, keep_pinned=False))
    await AutodeleteCog(bot).run_guild(guild)
    assert sorted(deleted_in_bulk(channel)) == [1, 2]


async def test_panel_nachrichten_sind_geschuetzt(bot: GGBot) -> None:
    guild, channel = ad_world(
        bot,
        [fake_message(100, dt.timedelta(days=1)), fake_message(200, dt.timedelta(days=1)),
         fake_message(300, dt.timedelta(days=1)), fake_message(400, dt.timedelta(days=1))],
    )  # fmt: skip
    panel = await bot.services.rolepanels.create(GUILD_ID, kind="button", mode="normal", title="R")
    await bot.services.rolepanels.set_message(GUILD_ID, panel.id, CH, 100)
    ticket = await bot.services.tickets.create_panel(GUILD_ID, title="T")
    await bot.services.tickets.set_panel_message(GUILD_ID, ticket.id, CH, 200)
    await bot.services.settings.save(
        GUILD_ID, VerificationSettings(enabled=True, role_id=5, panel_message_id=300)
    )
    bot.services.verification.invalidate(GUILD_ID)
    await ad_configure(bot, rule(60, keep_pinned=False))

    await AutodeleteCog(bot).run_guild(guild)

    assert deleted_in_bulk(channel) == [400]


async def test_ausgeschaltet_oder_ohne_regeln_wird_nichts_angefasst(bot: GGBot) -> None:
    guild, channel = ad_world(bot, [fake_message(1, dt.timedelta(days=1))])
    cog = AutodeleteCog(bot)

    await ad_configure(bot, rule(60), enabled=False)
    assert await cog.run_guild(guild) == 0
    await ad_configure(bot, enabled=True)
    assert await cog.run_guild(guild) == 0

    assert channel.history_calls == []
    channel.delete_messages.assert_not_awaited()


async def test_fehlender_kanal_und_fehlende_rechte_ueberspringen_nur_diese_regel(
    bot: GGBot,
) -> None:
    guild, channel = ad_world(bot, [fake_message(1, dt.timedelta(days=1))])
    other = text_channel(71)
    other.permissions_for = MagicMock(return_value=discord.Permissions(view_channel=True))
    other.history_calls = []
    guild.get_channel = MagicMock(side_effect={CH: channel, 71: other}.get)
    await ad_configure(bot, rule(60, channel=999), rule(60, channel=71), rule(60, channel=CH))

    deleted = await AutodeleteCog(bot).run_guild(guild)

    assert deleted == 1
    assert deleted_in_bulk(channel) == [1]
    other.history.assert_not_called() if hasattr(other.history, "assert_not_called") else None


async def test_ein_zweiter_lauf_im_fenster_loescht_nichts_doppelt(bot: GGBot) -> None:
    guild, channel = ad_world(bot, [fake_message(1, dt.timedelta(days=1))])
    await ad_configure(bot, rule(60))
    cog = AutodeleteCog(bot)

    await cog.run_guild(guild)
    second = await cog.run_guild(guild)

    assert second == 0
    assert len(channel.history_calls) == 1


async def test_sehr_alte_nachrichten_werden_einzeln_geloescht(bot: GGBot) -> None:
    guild, channel = ad_world(
        bot, [fake_message(1, dt.timedelta(days=30)), fake_message(2, dt.timedelta(days=20))]
    )
    await ad_configure(bot, rule(60))

    deleted = await AutodeleteCog(bot).run_guild(guild)

    assert deleted == 2
    channel.delete_messages.assert_not_awaited()
    assert [c.args[0] for c in channel.get_partial_message.call_args_list] == [1, 2]
    assert channel.partial.delete.await_count == 2


async def test_bereits_geloeschte_einzelnachricht_ist_kein_fehler(bot: GGBot) -> None:
    guild, channel = ad_world(bot, [fake_message(1, dt.timedelta(days=30))])
    channel.partial.delete = AsyncMock(side_effect=discord.NotFound(MagicMock(status=404), "weg"))
    await ad_configure(bot, rule(60))

    assert await AutodeleteCog(bot).run_guild(guild) == 1


async def test_sammellöschung_in_paketen_zu_hundert_und_mit_deckel(bot: GGBot) -> None:
    messages = [fake_message(i, dt.timedelta(hours=2)) for i in range(1, 301)]
    guild, channel = ad_world(bot, messages)
    await ad_configure(bot, rule(60))

    deleted = await AutodeleteCog(bot).run_guild(guild)

    assert deleted == ad_rules.MAX_BULK_PER_RUN
    assert [len(c.args[0]) for c in channel.delete_messages.await_args_list] == [100, 100]


async def test_forbidden_beendet_den_kanal_aber_nicht_den_lauf(bot: GGBot) -> None:
    guild, channel = ad_world(bot, [fake_message(1, dt.timedelta(hours=2))])
    channel.delete_messages = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "no"))
    second = text_channel(71)
    second.history = channel.history
    second.delete_messages = AsyncMock()
    guild.get_channel = MagicMock(side_effect={CH: channel, 71: second}.get)
    await ad_configure(bot, rule(60, channel=CH), rule(60, channel=71))

    await AutodeleteCog(bot).run_guild(guild)  # darf nicht werfen

    second.delete_messages.assert_awaited_once()


async def test_ein_kaputter_server_haelt_die_anderen_nicht_auf(bot: GGBot) -> None:
    cog = AutodeleteCog(bot)
    broken, healthy = MagicMock(), MagicMock()
    calls: list[Any] = []

    async def run_guild(guild: Any) -> int:
        calls.append(guild)
        if guild is broken:
            raise RuntimeError("kaputt")
        return 4

    cog.run_guild = run_guild  # type: ignore[method-assign]
    bot.guilds  # noqa: B018
    object.__setattr__(bot, "_connection", bot._connection)
    bot._connection._guilds = {1: broken, 2: healthy}

    assert await cog.run_all() == 4
    assert calls == [broken, healthy]


# --- Befehle ---


async def ad_command(
    bot: GGBot, name: str, *args: Any, locale_guild: MagicMock | None = None
) -> MagicMock:
    guild = locale_guild or make_world(bot)
    cog = AutodeleteCog(bot)
    interaction = click(bot, guild, admin(guild))
    await invoke(getattr(cog, name), cog, interaction, *args)
    return interaction


async def test_add_verlangt_eine_ausdrueckliche_bestaetigung(bot: GGBot) -> None:
    channel = text_channel(CH)

    with pytest.raises(UserError) as error:
        await ad_command(bot, "add", channel, "7d", True, False)

    assert error.value.key == "error.autodelete_confirm"
    assert (await bot.services.autodelete.settings(GUILD_ID)).rules == []


async def test_add_mit_bestaetigung_speichert_und_schaltet_ein(bot: GGBot) -> None:
    channel = text_channel(CH)

    interaction = await ad_command(bot, "add", channel, "2d12h", False, True)

    saved = await bot.services.autodelete.settings(GUILD_ID)
    assert saved.enabled is True
    assert (
        saved.rules[0].channel_id,
        saved.rules[0].max_age_minutes,
        saved.rules[0].keep_pinned,
    ) == (CH, 3600, False)
    assert "<#70>" in sent_text(interaction.response.send_message)


async def test_add_ersetzt_die_regel_desselben_kanals(bot: GGBot) -> None:
    channel = text_channel(CH)
    await ad_command(bot, "add", channel, "1d", True, True)

    await ad_command(bot, "add", channel, "2d", True, True)

    rules_ = (await bot.services.autodelete.settings(GUILD_ID)).rules
    assert [r.max_age_minutes for r in rules_] == [2 * 24 * 60]


@pytest.mark.parametrize("age", ["5m", "100d", "10", "abc", "0m"])
async def test_add_lehnt_unsinnige_alter_ab(bot: GGBot, age: str) -> None:
    with pytest.raises(UserError) as error:
        await ad_command(bot, "add", text_channel(CH), age, True, True)

    assert error.value.key in {"error.autodelete_age", "error.invalid_duration"}
    assert (await bot.services.autodelete.settings(GUILD_ID)).rules == []


async def test_add_ohne_rechte_im_kanal(bot: GGBot) -> None:
    channel = text_channel(CH)
    channel.permissions_for = MagicMock(return_value=discord.Permissions(view_channel=True))

    with pytest.raises(UserError) as error:
        await ad_command(bot, "add", channel, "1d", True, True)

    assert error.value.key == "error.missing_bot_permissions"


async def test_remove_list_on_off(bot: GGBot) -> None:
    channel = text_channel(CH)
    await ad_command(bot, "add", channel, "1d", True, True)

    listed = await ad_command(bot, "list_")
    assert "<#70>" in sent_text(listed.response.send_message)

    await ad_command(bot, "off")
    assert (await bot.services.autodelete.settings(GUILD_ID)).enabled is False
    await ad_command(bot, "on")
    assert (await bot.services.autodelete.settings(GUILD_ID)).enabled is True

    await ad_command(bot, "remove", channel)
    assert (await bot.services.autodelete.settings(GUILD_ID)).rules == []
    with pytest.raises(UserError) as none_left:
        await ad_command(bot, "remove", channel)
    assert none_left.value.key == "error.autodelete_no_rule"
    with pytest.raises(UserError) as no_rules:
        await ad_command(bot, "on")
    assert no_rules.value.key == "error.autodelete_no_rules"
    assert asyncio is not None
