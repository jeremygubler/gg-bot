"""Willkommen, Abschied, DM und Autorollen mit Fake-Discord-Objekten und echter DB/Redis."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from redis.asyncio import Redis

from ggbot.bot.client import GGBot
from ggbot.bot.cogs import welcome as welcome_module
from ggbot.bot.cogs.welcome import WelcomeCog
from ggbot.bot.services import Services
from ggbot.core.config import AppConfig
from ggbot.core.db.engine import Database
from ggbot.core.errors import UserError
from ggbot.core.events import SettingsChanged
from ggbot.core.i18n import translator
from ggbot.core.redis import key
from ggbot.core.services.welcome import MAX_MESSAGES_PER_MINUTE
from ggbot.core.settings.welcome import WelcomeSettings
from tests.bot.test_automod_cog import make_text_channel
from tests.bot.test_cog_flows import GUILD_ID, make_guild, make_interaction, make_member, sent_text
from tests.bot.test_panels import role, role_ids, user_with
from tests.fakes import invoke

CH = 70
LOG = 71
USER = 500


@pytest.fixture(autouse=True)
def no_settle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(welcome_module, "SETTLE_SECONDS", 0)


@pytest.fixture
async def bot(db: Database, redis: Redis) -> GGBot:
    return GGBot(Services.build(AppConfig(), db, redis))


async def configure(bot: GGBot, **kwargs: object) -> None:
    await bot.services.settings.save(GUILD_ID, WelcomeSettings.model_validate(kwargs))
    bot.services.welcome.invalidate(GUILD_ID)


def channel(channel_id: int = CH) -> MagicMock:
    mock = make_text_channel(channel_id)
    mock.guild = SimpleNamespace(id=GUILD_ID)
    mock.send = AsyncMock()
    return mock


def world(*roles: MagicMock, channels: tuple[MagicMock, ...] | None = None) -> MagicMock:
    guild = make_guild(bot_position=10)
    chans = {c.id: c for c in (channels if channels is not None else (channel(),))}
    guild.get_channel = MagicMock(side_effect=chans.get)
    guild.name = "Testserver"
    guild.member_count = 1234
    table = {r.id: r for r in roles}
    guild.get_role = MagicMock(side_effect=table.get)
    return guild


def newcomer(
    guild: MagicMock, *, bot: bool = False, pending: bool = False, name: str = "Alice"
) -> MagicMock:
    member = user_with(guild)
    member.bot = bot
    member.pending = pending
    member.display_name = name
    member.send = AsyncMock()
    member.display_avatar = MagicMock()
    member.display_avatar.replace = MagicMock(return_value=member.display_avatar)
    member.display_avatar.read = AsyncMock(return_value=b"")  # kein gültiges Bild: Platzhalter
    guild.get_member = MagicMock(return_value=member)
    return member


def sent(chan: MagicMock) -> tuple[str, dict[str, Any]]:
    call = chan.send.await_args
    assert call is not None, "es wurde nichts gesendet"
    return call.args[0], call.kwargs


# --- Beitritt ---


async def test_willkommen_mit_karte(bot: GGBot) -> None:
    chan = channel()
    guild = world(channels=(chan,))
    member = newcomer(guild)
    await configure(bot, join_enabled=True, join_channel_id=CH)
    await WelcomeCog(bot).on_member_join(member)
    text, kwargs = sent(chan)
    assert "<@500>" in text
    assert "Testserver" in text
    assert "1234" in text
    assert kwargs["file"].filename == "welcome.png"
    assert kwargs["file"].fp.read(8) == b"\x89PNG\r\n\x1a\n"
    mentions = kwargs["allowed_mentions"]
    assert (mentions.everyone, mentions.roles, mentions.users) == (False, False, [member])


async def test_eigene_vorlage_ohne_karte(bot: GGBot) -> None:
    chan = channel()
    guild = world(channels=(chan,))
    member = newcomer(guild, name="Bob")
    await configure(
        bot,
        join_enabled=True,
        join_channel_id=CH,
        join_message="Hi {name} ({count})",
        join_card=False,
    )
    await WelcomeCog(bot).on_member_join(member)
    text, kwargs = sent(chan)
    assert text == "Hi Bob (1234)"
    assert kwargs["file"] is discord.utils.MISSING


async def test_nickname_kann_nicht_alle_anpingen(bot: GGBot) -> None:
    chan = channel()
    guild = world(channels=(chan,))
    member = newcomer(guild, name="@everyone")
    await configure(
        bot, join_enabled=True, join_channel_id=CH, join_message="Hallo {name}", join_card=False
    )
    await WelcomeCog(bot).on_member_join(member)
    _, kwargs = sent(chan)
    assert kwargs["allowed_mentions"].everyone is False
    assert kwargs["allowed_mentions"].roles is False


async def test_ausgeschaltet_passiert_nichts(bot: GGBot) -> None:
    chan = channel()
    guild = world(channels=(chan,))
    member = newcomer(guild)
    await WelcomeCog(bot).on_member_join(member)
    chan.send.assert_not_awaited()
    member.send.assert_not_awaited()
    member.add_roles.assert_not_awaited()


async def test_dm_und_geschlossene_dm(bot: GGBot) -> None:
    guild = world()
    member = newcomer(guild)
    await configure(bot, dm_enabled=True, dm_message="Hallo {name} auf {server}")
    cog = WelcomeCog(bot)
    await cog.on_member_join(member)
    member.send.assert_awaited_once()
    assert member.send.await_args.args[0] == "Hallo Alice auf Testserver"
    assert member.send.await_args.kwargs["allowed_mentions"].users is False

    other = newcomer(guild)
    other.id = USER + 1
    other.send = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "closed"))
    await cog.on_member_join(other)  # geschlossene DMs sind kein Fehler


async def test_autorollen_fuer_menschen_und_bots(bot: GGBot) -> None:
    guild = world(role(1), role(2), role(3))
    await configure(bot, autoroles=[1, 2], bot_autoroles=[3])
    cog = WelcomeCog(bot)
    human = newcomer(guild)
    await cog.on_member_join(human)
    assert role_ids(human) == {1, 2}
    robot = newcomer(guild, bot=True)
    robot.id = USER + 1
    await cog.on_member_join(robot)
    assert role_ids(robot) == {3}


async def test_autorolle_ueber_dem_bot_wird_uebersprungen(bot: GGBot) -> None:
    guild = world(role(1), role(2, position=10))
    member = newcomer(guild)
    await configure(bot, autoroles=[1, 2])
    await WelcomeCog(bot).on_member_join(member)
    assert role_ids(member) == {1}


async def test_autorolle_ohne_rechte_wirft_nicht_und_stoppt_nichts(bot: GGBot) -> None:
    chan = channel()
    guild = world(role(1), channels=(chan,))
    member = newcomer(guild)
    member.add_roles = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "x"))
    await configure(bot, autoroles=[1], join_enabled=True, join_channel_id=CH, join_card=False)
    await WelcomeCog(bot).on_member_join(member)
    chan.send.assert_awaited_once()  # die Begrüssung kommt trotzdem


async def test_bots_werden_nicht_begruesst(bot: GGBot) -> None:
    chan = channel()
    guild = world(channels=(chan,))
    robot = newcomer(guild, bot=True)
    await configure(bot, join_enabled=True, join_channel_id=CH, dm_enabled=True)
    await WelcomeCog(bot).on_member_join(robot)
    chan.send.assert_not_awaited()
    robot.send.assert_not_awaited()


# --- Mitglieder-Screening ---


async def test_screening_wartet_bis_zum_bestehen(bot: GGBot) -> None:
    chan = channel()
    guild = world(role(1), channels=(chan,))
    member = newcomer(guild, pending=True)
    await configure(
        bot, join_enabled=True, join_channel_id=CH, autoroles=[1], dm_enabled=True, join_card=False
    )
    cog = WelcomeCog(bot)
    await cog.on_member_join(member)
    chan.send.assert_not_awaited()
    member.add_roles.assert_not_awaited()
    member.send.assert_not_awaited()

    passed = newcomer(guild, pending=False)
    passed.id = member.id
    await cog.on_member_update(member, passed)  # Screening bestanden
    chan.send.assert_awaited_once()
    assert role_ids(passed) == {1}
    passed.send.assert_awaited_once()


async def test_andere_aenderungen_loesen_nichts_aus(bot: GGBot) -> None:
    chan = channel()
    guild = world(channels=(chan,))
    before, after = newcomer(guild), newcomer(guild)
    await configure(bot, join_enabled=True, join_channel_id=CH)
    await WelcomeCog(bot).on_member_update(before, after)  # nicht pending → nicht pending
    chan.send.assert_not_awaited()
    before.pending = True
    after.pending = True
    await WelcomeCog(bot).on_member_update(before, after)  # weiterhin wartend
    chan.send.assert_not_awaited()
    before.pending = False
    await WelcomeCog(bot).on_member_update(before, after)  # umgekehrt: wird wieder wartend
    chan.send.assert_not_awaited()


async def test_beitritt_und_screening_begruessen_nur_einmal(bot: GGBot) -> None:
    chan = channel()
    guild = world(channels=(chan,))
    member = newcomer(guild)
    await configure(bot, join_enabled=True, join_channel_id=CH, join_card=False)
    cog = WelcomeCog(bot)
    await cog.on_member_join(member)
    before = newcomer(guild, pending=True)
    before.id = member.id
    await cog.on_member_update(before, member)
    await cog.on_member_join(member)  # doppeltes Gateway-Ereignis
    assert chan.send.await_count == 1


# --- Raid, Drosselung, Sofort-Austritt ---


async def test_im_raid_modus_still(bot: GGBot) -> None:
    chan = channel()
    guild = world(role(1), channels=(chan,))
    member = newcomer(guild)
    await configure(bot, join_enabled=True, join_channel_id=CH, autoroles=[1], dm_enabled=True)
    await bot.services.redis.set(key("automod", "raid", GUILD_ID), "1", ex=60)
    assert await bot.services.automod.raid_active(GUILD_ID)
    await WelcomeCog(bot).on_member_join(member)
    chan.send.assert_not_awaited()
    member.send.assert_not_awaited()
    member.add_roles.assert_not_awaited()


async def test_wer_sofort_geht_wird_nicht_begruesst(bot: GGBot) -> None:
    chan = channel()
    guild = world(role(1), channels=(chan,))
    member = newcomer(guild)
    guild.get_member = MagicMock(return_value=None)  # nach der Wartezeit nicht mehr da
    await configure(bot, join_enabled=True, join_channel_id=CH, autoroles=[1], dm_enabled=True)
    await WelcomeCog(bot).on_member_join(member)
    chan.send.assert_not_awaited()
    member.add_roles.assert_not_awaited()
    member.send.assert_not_awaited()


async def test_ansturm_wird_gedrosselt(bot: GGBot) -> None:
    chan = channel()
    guild = world(role(1), channels=(chan,))
    await configure(bot, join_enabled=True, join_channel_id=CH, join_card=False, autoroles=[1])
    cog = WelcomeCog(bot)
    members = []
    for i in range(MAX_MESSAGES_PER_MINUTE + 5):
        member = newcomer(guild)
        member.id = 10_000 + i
        members.append(member)
        await cog.on_member_join(member)
    assert chan.send.await_count == MAX_MESSAGES_PER_MINUTE
    # Die Rolle bekommen trotzdem alle: gedrosselt wird nur die Nachricht.
    assert all(role_ids(m) == {1} for m in members)


async def test_fehlender_oder_gesperrter_kanal_stoppt_nicht(bot: GGBot) -> None:
    guild = world(channels=())  # der Kanal wurde gelöscht
    member = newcomer(guild)
    await configure(bot, join_enabled=True, join_channel_id=CH, dm_enabled=True)
    await WelcomeCog(bot).on_member_join(member)
    member.send.assert_awaited_once()  # die DM kommt trotzdem

    chan = channel()
    chan.send = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "x"))
    guild2 = world(channels=(chan,))
    member2 = newcomer(guild2)
    member2.id = USER + 9
    await WelcomeCog(bot).on_member_join(member2)  # kein Absturz


# --- Abschied ---


def leave_event(user: Any, guild_id: int = GUILD_ID) -> Any:
    return SimpleNamespace(guild_id=guild_id, user=user)


async def test_abschied(bot: GGBot) -> None:
    chan = channel()
    guild = world(channels=(chan,))
    bot.get_guild = MagicMock(return_value=guild)  # type: ignore[method-assign]
    await configure(
        bot,
        leave_enabled=True,
        leave_channel_id=CH,
        leave_message="Tschüss {name} von {server} ({count})",
    )
    user = SimpleNamespace(id=USER, bot=False, display_name="@everyone", pending=False)
    await WelcomeCog(bot).on_raw_member_remove(leave_event(user))
    text, kwargs = sent(chan)
    assert text == "Tschüss @everyone von Testserver (1234)"
    assert kwargs["allowed_mentions"].everyone is False
    assert kwargs["allowed_mentions"].users is False  # niemand wird gepingt


async def test_abschied_standardtext_je_sprache(bot: GGBot) -> None:
    chan = channel()
    guild = world(channels=(chan,))
    bot.get_guild = MagicMock(return_value=guild)  # type: ignore[method-assign]
    await configure(bot, leave_enabled=True, leave_channel_id=CH)
    await WelcomeCog(bot).on_raw_member_remove(
        leave_event(SimpleNamespace(id=USER, bot=False, display_name="Eve"))
    )
    locale = await bot.services.guilds.get_locale(GUILD_ID)
    expected = translator.t(locale, "welcome.default_leave").format(name="Eve", server="Testserver")
    assert sent(chan)[0] == expected


@pytest.mark.parametrize("case", ["bot", "pending", "raid", "off", "unknown_guild", "twice"])
async def test_kein_abschied_in_sonderfaellen(bot: GGBot, case: str) -> None:
    chan = channel()
    guild = world(channels=(chan,))
    bot.get_guild = MagicMock(return_value=None if case == "unknown_guild" else guild)  # type: ignore[method-assign]
    if case != "off":
        await configure(bot, leave_enabled=True, leave_channel_id=CH)
    if case == "raid":
        await bot.services.redis.set(key("automod", "raid", GUILD_ID), "1", ex=60)
    user: Any
    if case == "pending":
        user = MagicMock(spec=discord.Member)  # nur ein echtes Mitglied kennt "pending"
        user.id, user.bot, user.display_name, user.pending = USER, False, "X", True
    else:
        user = SimpleNamespace(id=USER, bot=case == "bot", display_name="X")
    cog = WelcomeCog(bot)
    if case == "twice":
        await cog.on_raw_member_remove(leave_event(user))
        chan.send.reset_mock()
    await cog.on_raw_member_remove(leave_event(user))
    chan.send.assert_not_awaited()


# --- Einstellungen ---


async def test_settings_changed_leert_den_cache(bot: GGBot) -> None:
    cog = WelcomeCog(bot)
    assert (await bot.services.welcome.settings(GUILD_ID)).join_enabled is False
    await bot.services.settings.save(
        GUILD_ID, WelcomeSettings(join_enabled=True, join_channel_id=CH)
    )
    await cog.on_ggbot_settings_changed(SettingsChanged(guild_id=GUILD_ID, module="welcome"))
    assert (await bot.services.welcome.settings(GUILD_ID)).join_enabled is True
    await bot.services.settings.save(GUILD_ID, WelcomeSettings())
    await cog.on_ggbot_settings_changed(SettingsChanged(guild_id=GUILD_ID, module="leveling"))
    assert (await bot.services.welcome.settings(GUILD_ID)).join_enabled is True  # anderes Modul


# --- Befehle ---


def staff(guild: MagicMock, position: int = 8) -> MagicMock:
    member = make_member(11, position, guild)
    return member


async def test_befehle_schalten_ein_und_aus(bot: GGBot) -> None:
    cog = WelcomeCog(bot)
    chan = channel()
    guild = world(channels=(chan,))

    def ia() -> MagicMock:
        return make_interaction(bot, guild, staff(guild))

    await invoke(cog.join_set, cog, ia(), chan, "Hi {user}", False)
    s = await bot.services.welcome.settings(GUILD_ID)
    assert (s.join_enabled, s.join_channel_id, s.join_message, s.join_card) == (
        True,
        CH,
        "Hi {user}",
        False,
    )

    await invoke(cog.join_set, cog, ia(), chan, None, None)  # ohne Angaben bleibt der Text
    s = await bot.services.welcome.settings(GUILD_ID)
    assert (s.join_message, s.join_card) == ("Hi {user}", False)

    await invoke(cog.join_off, cog, ia())
    assert (await bot.services.welcome.settings(GUILD_ID)).join_enabled is False

    await invoke(cog.leave_set, cog, ia(), chan, "Bye {name}")
    await invoke(cog.dm_set, cog, ia(), "Hallo {name}")
    s = await bot.services.welcome.settings(GUILD_ID)
    assert (s.leave_enabled, s.leave_channel_id, s.leave_message) == (True, CH, "Bye {name}")
    assert (s.dm_enabled, s.dm_message) == (True, "Hallo {name}")
    await invoke(cog.leave_off, cog, ia())
    await invoke(cog.dm_off, cog, ia())
    s = await bot.services.welcome.settings(GUILD_ID)
    assert (s.leave_enabled, s.dm_enabled) == (False, False)
    assert s.leave_message == "Bye {name}"  # der Text bleibt für später erhalten


@pytest.mark.parametrize(
    ("command", "args"),
    [
        ("join_set", ("Hi {level}",)),
        ("join_set", ("Hi {user.__class__}",)),
        ("leave_set", ("{user} ging",)),
        ("dm_set", ("{count}",)),
    ],
)
async def test_ungueltige_vorlage_wird_abgelehnt_und_nichts_gespeichert(
    bot: GGBot, command: str, args: tuple[str, ...]
) -> None:
    cog = WelcomeCog(bot)
    chan = channel()
    guild = world(channels=(chan,))
    interaction = make_interaction(bot, guild, staff(guild))
    call = [chan, *args] if command != "dm_set" else [*args]
    with pytest.raises(UserError) as exc:
        await invoke(getattr(cog, command), cog, interaction, *call)
    assert exc.value.key == "error.welcome_invalid"
    assert await bot.services.welcome.settings(GUILD_ID) == WelcomeSettings()
    interaction.response.send_message.assert_not_awaited()


async def test_autorolle_hinzufuegen_entfernen(bot: GGBot) -> None:
    cog = WelcomeCog(bot)
    guild = world(
        role(1, position=3),
        role(2, position=3),
        role(3, position=9),
        role(4, position=1, managed=True),
    )

    def ia() -> MagicMock:
        return make_interaction(bot, guild, staff(guild, 8))

    await invoke(cog.autorole_add, cog, ia(), guild.get_role(1), False)
    await invoke(cog.autorole_add, cog, ia(), guild.get_role(2), True)
    await invoke(cog.autorole_add, cog, ia(), guild.get_role(1), False)  # doppelt: bleibt einmal
    s = await bot.services.welcome.settings(GUILD_ID)
    assert (s.autoroles, s.bot_autoroles) == ([1], [2])

    for role_id, error_key in ((3, "error.hierarchy_actor"), (4, "error.role_unassignable")):
        with pytest.raises(UserError) as exc:
            await invoke(cog.autorole_add, cog, ia(), guild.get_role(role_id), False)
        assert exc.value.key == error_key

    await invoke(cog.autorole_remove, cog, ia(), guild.get_role(1))
    await invoke(cog.autorole_remove, cog, ia(), guild.get_role(2))
    s = await bot.services.welcome.settings(GUILD_ID)
    assert (s.autoroles, s.bot_autoroles) == ([], [])
    with pytest.raises(UserError) as exc:
        await invoke(cog.autorole_remove, cog, ia(), guild.get_role(1))
    assert exc.value.key == "error.autorole_unknown"


async def test_autorollen_grenze(bot: GGBot) -> None:
    cog = WelcomeCog(bot)
    roles = [role(i, position=2) for i in range(1, 13)]
    guild = world(*roles)

    def ia() -> MagicMock:
        return make_interaction(bot, guild, staff(guild, 8))

    for r in roles[:10]:
        await invoke(cog.autorole_add, cog, ia(), r, False)
    with pytest.raises(UserError) as exc:
        await invoke(cog.autorole_add, cog, ia(), roles[10], False)
    assert exc.value.key == "error.welcome_invalid"
    assert len((await bot.services.welcome.settings(GUILD_ID)).autoroles) == 10


async def test_karte_einstellen(bot: GGBot) -> None:
    cog = WelcomeCog(bot)
    guild = world()

    def ia() -> MagicMock:
        return make_interaction(bot, guild, staff(guild))

    await invoke(cog.card, cog, ia(), "#ff8800", "112233", SimpleNamespace(value="stripes"))
    card = (await bot.services.welcome.settings(GUILD_ID)).card
    assert (card.accent, card.background, card.template) == (0xFF8800, 0x112233, "stripes")
    await invoke(cog.card, cog, ia(), None, "000000", None)  # nur ändern, was angegeben ist
    card = (await bot.services.welcome.settings(GUILD_ID)).card
    assert (card.accent, card.background, card.template) == (0xFF8800, 0, "stripes")
    for call in ((None, None, None), ("rot", None, None)):
        with pytest.raises(UserError):
            await invoke(cog.card, cog, ia(), *call)


async def test_vorschau_sendet_nichts_an_den_kanal(bot: GGBot) -> None:
    cog = WelcomeCog(bot)
    chan = channel()
    guild = world(channels=(chan,))
    member = newcomer(guild)
    await configure(bot, join_enabled=True, join_channel_id=CH, join_message="Hi {user}")
    interaction = make_interaction(bot, guild, member)
    await invoke(cog.preview, cog, interaction)
    chan.send.assert_not_awaited()
    kwargs = interaction.followup.send.await_args.kwargs
    assert kwargs["ephemeral"] is True
    assert kwargs["file"].filename == "welcome.png"
    assert interaction.followup.send.await_args.args[0] == "Hi <@500>"
    assert kwargs["allowed_mentions"].users is False  # die Vorschau pingt niemanden


async def test_status(bot: GGBot) -> None:
    cog = WelcomeCog(bot)
    guild = world()
    await configure(bot, join_enabled=True, join_channel_id=CH, autoroles=[1, 2], dm_enabled=True)
    interaction = make_interaction(bot, guild, staff(guild))
    await invoke(cog.status, cog, interaction)
    text = sent_text(interaction.response.send_message)
    assert f"<#{CH}>" in text
    assert "<@&1>" in text and "<@&2>" in text  # noqa: PT018
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True


async def test_servereinstellungen_sind_getrennt(bot: GGBot) -> None:
    cog = WelcomeCog(bot)
    chan = channel()
    guild = world(channels=(chan,))
    await invoke(cog.dm_set, cog, make_interaction(bot, guild, staff(guild)), "Hi")
    assert (await bot.services.welcome.settings(GUILD_ID + 1)).dm_enabled is False
    assert (await bot.services.welcome.settings(GUILD_ID)).dm_enabled is True
    _ = make_guild  # Hilfsimport bleibt für künftige Tests
