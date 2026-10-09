"""Event-Log vom Discord-Ereignis bis in den Kanal, mit Fake-Objekten und echter DB/Redis."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import discord
import pytest
from redis.asyncio import Redis

from ggbot.bot.client import GGBot
from ggbot.bot.cogs import eventlog as eventlog_module
from ggbot.bot.cogs.eventlog import EventLogCog
from ggbot.bot.services import Services
from ggbot.core import events
from ggbot.core.config import AppConfig
from ggbot.core.db.engine import Database
from ggbot.core.settings.eventlog import EVENT_TYPES, LoggingSettings
from tests.bot.test_automod_cog import make_text_channel
from tests.bot.test_cog_flows import GUILD_ID, make_guild, make_member
from tests.fakes import FakeUser

CH = 70
LOG = 800  # Kanal für alles
NOW = datetime.now(UTC)


@pytest.fixture
async def bot(db: Database, redis: Redis) -> GGBot:
    config = AppConfig(enable_message_content_intent=True, enable_members_intent=True)
    return GGBot(Services.build(config, db, redis))


@pytest.fixture(autouse=True)
def no_audit_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(eventlog_module, "AUDIT_DELAY_SECONDS", 0)


def make_world(
    bot: GGBot, monkeypatch: pytest.MonkeyPatch, *, audit: list[Any] | None = None
) -> tuple[MagicMock, MagicMock, MagicMock]:
    """(Server, Log-Kanal, Quell-Kanal). `audit` = die Audit-Log-Einträge, die Discord liefert."""
    log_channel, source = make_text_channel(LOG), make_text_channel(CH)
    guild = make_guild()
    channels = {LOG: log_channel, CH: source}
    guild.get_channel = MagicMock(side_effect=channels.get)
    guild.member_count = 42
    guild.me.guild_permissions = discord.Permissions(view_audit_log=True)
    entries = list(audit or [])

    async def audit_logs(**kwargs: Any) -> AsyncIterator[Any]:
        for entry in entries:
            yield entry

    guild.audit_logs = MagicMock(side_effect=audit_logs)
    monkeypatch.setattr(bot, "get_guild", lambda gid: guild if gid == GUILD_ID else None)
    return guild, log_channel, source


async def enable(bot: GGBot, **channels: int) -> None:
    await bot.services.settings.save(GUILD_ID, LoggingSettings(channels=channels))
    bot.services.eventlog.invalidate(GUILD_ID)


async def flush(cog: EventLogCog) -> None:
    await asyncio.sleep(0)  # Listener haben ihre Einträge eingereiht
    if cog.sender._workers:
        await asyncio.gather(*cog.sender._workers.values())


def sent(channel: MagicMock) -> list[discord.Embed]:
    return [c.kwargs["embed"] for c in channel.send.await_args_list if "embed" in c.kwargs]


def fields(embed: discord.Embed) -> dict[str, str]:
    return {str(f.name): str(f.value) for f in embed.fields}


def audit_entry(target_id: int, user_id: int = 9, reason: str | None = "Grund") -> SimpleNamespace:
    return SimpleNamespace(
        target=SimpleNamespace(id=target_id), user_id=user_id, reason=reason, created_at=NOW
    )


def cached_message(content: str = "geheim", *, author_bot: bool = False) -> MagicMock:
    message = MagicMock(spec=discord.Message)
    message.id = 5
    message.channel = SimpleNamespace(id=CH)
    message.author = FakeUser(1, "alice", bot=author_bot)
    message.content = content
    message.attachments = []
    message.created_at = NOW
    return message


# --- Nachrichten ---


async def test_geloeschte_nachricht_mit_inhalt(bot: GGBot, monkeypatch: pytest.MonkeyPatch) -> None:
    _, log_channel, _ = make_world(bot, monkeypatch)
    await enable(bot, message_delete=LOG)
    cog: Any = EventLogCog(bot)
    payload = SimpleNamespace(
        guild_id=GUILD_ID,
        channel_id=CH,
        message_id=5,
        cached_message=cached_message("Passwort 123"),
    )
    await cog.on_raw_message_delete(payload)
    await flush(cog)
    (embed,) = sent(log_channel)
    assert embed.title == "Nachricht gelöscht"
    assert "Passwort 123" in fields(embed)["Inhalt"]
    assert "<@1>" in fields(embed)["Autor"]


async def test_geloeschte_nachricht_ohne_cache_sagt_das_ehrlich(
    bot: GGBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, log_channel, _ = make_world(bot, monkeypatch)
    await enable(bot, message_delete=LOG)
    cog: Any = EventLogCog(bot)
    await cog.on_raw_message_delete(
        SimpleNamespace(guild_id=GUILD_ID, channel_id=CH, message_id=5, cached_message=None)
    )
    await flush(cog)
    (embed,) = sent(log_channel)
    assert "nicht mehr bekannt" in (embed.description or "")


async def test_nichts_wird_protokolliert_wenn_ausgeschaltet_ignoriert_oder_bot(
    bot: GGBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, log_channel, _ = make_world(bot, monkeypatch)
    cog: Any = EventLogCog(bot)

    def payload(channel_id: int = CH, bot_author: bool = False) -> SimpleNamespace:
        return SimpleNamespace(
            guild_id=GUILD_ID,
            channel_id=channel_id,
            message_id=5,
            cached_message=cached_message(author_bot=bot_author),
        )

    await cog.on_raw_message_delete(payload())  # nichts eingestellt
    await bot.services.settings.save(
        GUILD_ID, LoggingSettings(channels={"message_delete": LOG}, ignored_channels=[CH])
    )
    bot.services.eventlog.invalidate(GUILD_ID)
    await cog.on_raw_message_delete(payload())  # ignorierter Kanal
    await cog.on_raw_message_delete(payload(channel_id=99, bot_author=True))  # Bot
    await cog.on_raw_message_delete(payload(channel_id=LOG))  # im Log-Kanal selbst
    await cog.on_raw_message_delete(
        SimpleNamespace(guild_id=None, channel_id=CH, message_id=5, cached_message=None)
    )  # Direktnachricht
    await flush(cog)
    assert log_channel.send.await_count == 0


async def test_bearbeitung_zeigt_vorher_und_nachher(
    bot: GGBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, log_channel, _ = make_world(bot, monkeypatch)
    await enable(bot, message_edit=LOG)
    cog: Any = EventLogCog(bot)
    after = cached_message("neu")
    after.jump_url = "https://discord.com/channels/1/2/3"
    payload = SimpleNamespace(
        guild_id=GUILD_ID,
        channel_id=CH,
        data={},
        cached_message=cached_message("alt"),
        message=after,
    )
    await cog.on_raw_message_edit(payload)
    await flush(cog)
    (embed,) = sent(log_channel)
    assert "alt" in fields(embed)["Vorher"]
    assert "neu" in fields(embed)["Nachher"]
    assert "discord.com/channels" in fields(embed)["Link"]


async def test_bearbeitung_ohne_textaenderung_oder_ohne_edit_zeitstempel_wird_uebersprungen(
    bot: GGBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, log_channel, _ = make_world(bot, monkeypatch)
    await enable(bot, message_edit=LOG)
    cog: Any = EventLogCog(bot)
    same = SimpleNamespace(
        guild_id=GUILD_ID,
        channel_id=CH,
        data={},
        cached_message=cached_message("x"),
        message=cached_message("x"),
    )
    await cog.on_raw_message_edit(same)  # Link-Vorschau
    preview = SimpleNamespace(
        guild_id=GUILD_ID, channel_id=CH, data={"edited_timestamp": None}, cached_message=None,
        message=cached_message("x"),
    )  # fmt: skip
    await cog.on_raw_message_edit(preview)  # unbekannter Vorzustand, keine echte Bearbeitung
    await flush(cog)
    assert log_channel.send.await_count == 0

    real = SimpleNamespace(
        guild_id=GUILD_ID, channel_id=CH, data={"edited_timestamp": "2026-05-01T12:00:00+00:00"},
        cached_message=None, message=cached_message("neu"),
    )  # fmt: skip
    after = real.message
    after.jump_url = "u"
    await cog.on_raw_message_edit(real)
    await flush(cog)
    (embed,) = sent(log_channel)
    assert fields(embed)["Vorher"] == "(nicht bekannt)"


async def test_massenloeschung_kommt_als_datei(bot: GGBot, monkeypatch: pytest.MonkeyPatch) -> None:
    _, log_channel, _ = make_world(bot, monkeypatch)
    await enable(bot, message_bulk=LOG)
    cog: Any = EventLogCog(bot)
    payload = SimpleNamespace(
        guild_id=GUILD_ID,
        channel_id=CH,
        message_ids={5, 6, 7},
        cached_messages=[cached_message("eins")],
    )
    await cog.on_raw_bulk_message_delete(payload)
    await flush(cog)
    call = log_channel.send.await_args
    assert call.kwargs["embed"].title == "Nachrichten massenhaft gelöscht"
    attachment = call.kwargs["file"]
    text = attachment.fp.read().decode()
    assert "3 Nachrichten, davon 1" in text
    assert "alice (1): eins" in text


# --- Mitglieder ---


async def test_beitritt(bot: GGBot, monkeypatch: pytest.MonkeyPatch) -> None:
    guild, log_channel, _ = make_world(bot, monkeypatch)
    await enable(bot, member_join=LOG)
    cog: Any = EventLogCog(bot)
    member = make_member(77, 1, guild)
    member.guild = guild
    member.bot = False
    member.created_at = NOW - timedelta(days=3)
    await cog.on_member_join(member)
    await flush(cog)
    (embed,) = sent(log_channel)
    assert embed.title == "Mitglied beigetreten"
    assert fields(embed)["Mitglieder jetzt"] == "42"


async def test_austritt_und_kick_mit_audit_log(bot: GGBot, monkeypatch: pytest.MonkeyPatch) -> None:
    guild, log_channel, _ = make_world(bot, monkeypatch, audit=[audit_entry(77, 9, "Spam")])
    await enable(bot, member_leave=LOG)
    cog: Any = EventLogCog(bot)
    member = make_member(77, 1, guild)
    member.joined_at = NOW - timedelta(days=30)
    member.roles = [FakeRole("@everyone"), FakeRole("Mod")]
    guild.get_member = MagicMock(return_value=member)
    payload = SimpleNamespace(guild_id=GUILD_ID, user=FakeUser(77, "gone"))
    await cog.on_raw_member_remove(payload)
    await flush(cog)
    (embed,) = sent(log_channel)
    assert embed.title == "Mitglied gekickt"
    assert fields(embed)["Durch"] == "<@9>"
    assert fields(embed)["Grund"] == "Spam"
    assert "Mod" in fields(embed)["Rollen"]


async def test_normaler_austritt_ohne_passenden_audit_eintrag(
    bot: GGBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    guild, log_channel, _ = make_world(
        bot, monkeypatch, audit=[audit_entry(12345)]
    )  # anderer Nutzer
    await enable(bot, member_leave=LOG)
    cog: Any = EventLogCog(bot)
    guild.get_member = MagicMock(return_value=None)
    await cog.on_raw_member_remove(SimpleNamespace(guild_id=GUILD_ID, user=FakeUser(77, "gone")))
    await flush(cog)
    (embed,) = sent(log_channel)
    assert embed.title == "Mitglied gegangen"
    assert "Durch" not in fields(embed)


async def test_audit_eintrag_ist_zu_alt_oder_recht_fehlt(
    bot: GGBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = audit_entry(77)
    old.created_at = NOW - timedelta(minutes=5)
    guild, log_channel, _ = make_world(bot, monkeypatch, audit=[old])
    await enable(bot, member_ban=LOG)
    cog: Any = EventLogCog(bot)
    await cog.on_member_ban(guild, FakeUser(77))
    await flush(cog)
    assert "Durch" not in fields(sent(log_channel)[0])

    guild.me.guild_permissions = discord.Permissions.none()
    guild.audit_logs.reset_mock()
    await cog.on_member_ban(guild, FakeUser(78))
    await flush(cog)
    guild.audit_logs.assert_not_called()  # ohne Recht gar nicht erst fragen
    assert len(sent(log_channel)) == 2


async def test_bann_und_entbannung_mit_ausfuehrendem(
    bot: GGBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    guild, log_channel, _ = make_world(bot, monkeypatch, audit=[audit_entry(77, 9, "Betrug")])
    await enable(bot, member_ban=LOG, member_unban=LOG)
    cog: Any = EventLogCog(bot)
    await cog.on_member_ban(guild, FakeUser(77))
    await cog.on_member_unban(guild, FakeUser(77))
    await flush(cog)
    titles = [e.title for e in sent(log_channel)]
    assert titles == ["Mitglied gebannt", "Mitglied entbannt"]
    assert fields(sent(log_channel)[0])["Grund"] == "Betrug"


async def test_mitgliedsaenderung_rollen_nick_timeout(
    bot: GGBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    guild, log_channel, _ = make_world(bot, monkeypatch, audit=[audit_entry(77, 9)])
    await enable(bot, member_update=LOG)
    cog: Any = EventLogCog(bot)
    mod, guest = FakeRole("Mod"), FakeRole("Gast")
    base = {"id": 77, "bot": False, "guild": guild, "nick": None, "timed_out_until": None}

    def member(**kw: Any) -> FakeMember:
        return FakeMember({**base, **kw})

    await cog.on_member_update(member(roles=[guest]), member(roles=[mod]))
    await cog.on_member_update(
        member(roles=[mod]),
        member(roles=[mod], nick="Neu", timed_out_until=NOW + timedelta(hours=1)),
    )
    await cog.on_member_update(member(roles=[mod]), member(roles=[mod]))  # nichts Relevantes
    await flush(cog)
    first, second = sent(log_channel)
    assert fields(first)["Rollen hinzugefügt"] == "Mod"
    assert fields(first)["Rollen entfernt"] == "Gast"
    assert fields(first)["Durch"] == "<@9>"
    assert fields(second)["Nickname"] == "keine → Neu"
    assert "Timeout bis" in fields(second)


class FakeRole:
    """Hashbar wie ein echtes `discord.Role` (die Mitgliedsänderung bildet Mengen daraus)."""

    def __init__(self, name: str) -> None:
        self.name = name


class FakeMember(FakeUser):
    def __init__(self, attrs: dict[str, Any]) -> None:
        super().__init__(attrs["id"], "member")
        self.__dict__.update(attrs)
        self.roles = attrs["roles"]


# --- Rollen und Kanäle ---


async def test_rollen_ereignisse(bot: GGBot, monkeypatch: pytest.MonkeyPatch) -> None:
    guild, log_channel, _ = make_world(bot, monkeypatch, audit=[audit_entry(7, 9)])
    await enable(bot, role_create=LOG, role_delete=LOG, role_update=LOG)
    cog: Any = EventLogCog(bot)

    def role(**kw: Any) -> MagicMock:
        values = {
            "id": 7, "name": "Mod", "guild": guild, "color": discord.Color(0),
            "permissions": discord.Permissions.none(), "hoist": False, "mentionable": False,
            "position": 1,
        }  # fmt: skip
        values.update(kw)
        mock = MagicMock(spec=discord.Role)
        for key, value in values.items():
            setattr(mock, key, value)
        return mock

    await cog.on_guild_role_create(role())
    await cog.on_guild_role_update(role(position=1), role(position=5))  # nur umsortiert
    await cog.on_guild_role_update(role(), role(name="Admin"))
    await cog.on_guild_role_delete(role())
    await flush(cog)
    titles = [e.title for e in sent(log_channel)]
    assert titles == ["Rolle erstellt", "Rolle geändert", "Rolle gelöscht"]
    assert "**Name:** Mod → Admin" in fields(sent(log_channel)[1])["Änderungen"]


async def test_kanal_ereignisse(bot: GGBot, monkeypatch: pytest.MonkeyPatch) -> None:
    guild, log_channel, _ = make_world(bot, monkeypatch, audit=[audit_entry(8, 9)])
    await enable(bot, channel_create=LOG, channel_delete=LOG, channel_update=LOG)
    cog: Any = EventLogCog(bot)

    def channel(**kw: Any) -> MagicMock:
        values = {
            "id": 8, "name": "news", "guild": guild, "topic": None, "slowmode_delay": 0,
            "nsfw": False, "category": None, "position": 1, "type": discord.ChannelType.text,
        }  # fmt: skip
        values.update(kw)
        mock = MagicMock(spec=discord.TextChannel)
        for key, value in values.items():
            setattr(mock, key, value)
        return mock

    await cog.on_guild_channel_create(channel())
    await cog.on_guild_channel_update(channel(position=1), channel(position=4))  # ignoriert
    await cog.on_guild_channel_update(channel(), channel(topic="Neu"))
    await cog.on_guild_channel_delete(channel())
    await flush(cog)
    assert [e.title for e in sent(log_channel)] == [
        "Kanal erstellt",
        "Kanal geändert",
        "Kanal gelöscht",
    ]
    assert fields(sent(log_channel)[0])["Typ"] == "text"


# --- Sprache und Einladungen ---


async def test_sprachwechsel(bot: GGBot, monkeypatch: pytest.MonkeyPatch) -> None:
    guild, log_channel, _ = make_world(bot, monkeypatch)
    await enable(bot, voice=LOG)
    cog: Any = EventLogCog(bot)
    member = FakeMember({"id": 1, "bot": False, "guild": guild, "roles": []})
    robot = FakeMember({"id": 2, "bot": True, "guild": guild, "roles": []})

    def state(channel_id: int | None) -> SimpleNamespace:
        return SimpleNamespace(
            channel=SimpleNamespace(id=channel_id, name="x") if channel_id else None
        )

    await cog.on_voice_state_update(member, state(None), state(5))
    await cog.on_voice_state_update(member, state(5), state(6))
    await cog.on_voice_state_update(member, state(6), state(None))
    await cog.on_voice_state_update(member, state(6), state(6))
    await cog.on_voice_state_update(robot, state(None), state(5))
    await flush(cog)
    texts = [e.description or "" for e in sent(log_channel)]
    assert len(texts) == 3
    assert "beigetreten" in texts[0]
    assert "wechselte" in texts[1]
    assert "verlassen" in texts[2]


async def test_einladungen(bot: GGBot, monkeypatch: pytest.MonkeyPatch) -> None:
    _, log_channel, _ = make_world(bot, monkeypatch)
    await enable(bot, invite_create=LOG, invite_delete=LOG)
    cog: Any = EventLogCog(bot)
    invite = SimpleNamespace(
        code="abc123", guild=SimpleNamespace(id=GUILD_ID), channel=SimpleNamespace(id=CH),
        inviter=FakeUser(1), max_uses=5, max_age=3600, temporary=False,
    )  # fmt: skip
    await cog.on_invite_create(invite)
    await cog.on_invite_delete(invite)
    await flush(cog)
    created, deleted = sent(log_channel)
    assert fields(created)["Code"] == "`abc123`"
    assert fields(created)["Max. Verwendungen"] == "5"
    assert deleted.title == "Einladung gelöscht"


# --- Querschnitt ---


async def test_jede_art_geht_in_ihren_eigenen_kanal(
    bot: GGBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    guild, log_channel, _ = make_world(bot, monkeypatch)
    other = make_text_channel(801)
    guild.get_channel = MagicMock(side_effect={LOG: log_channel, 801: other}.get)
    await enable(bot, voice=801, invite_create=LOG)
    cog: Any = EventLogCog(bot)
    member = FakeMember({"id": 1, "bot": False, "guild": guild, "roles": []})
    state = SimpleNamespace(channel=SimpleNamespace(id=5, name="x"))
    await cog.on_voice_state_update(member, SimpleNamespace(channel=None), state)
    await flush(cog)
    assert len(sent(other)) == 1
    assert sent(log_channel) == []


async def test_fehlender_log_kanal_bricht_nichts_ab(
    bot: GGBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    guild, log_channel, _ = make_world(bot, monkeypatch)
    await enable(bot, voice=999)  # Kanal gelöscht
    cog: Any = EventLogCog(bot)
    member = FakeMember({"id": 1, "bot": False, "guild": guild, "roles": []})
    state = SimpleNamespace(channel=SimpleNamespace(id=5, name="x"))
    await cog.on_voice_state_update(member, SimpleNamespace(channel=None), state)
    await flush(cog)
    assert log_channel.send.await_count == 0


async def test_einstellungs_event_leert_den_cache(
    bot: GGBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_world(bot, monkeypatch)
    await enable(bot, voice=LOG)
    assert await bot.services.eventlog.route(GUILD_ID, "voice") == LOG
    await bot.services.settings.save(GUILD_ID, LoggingSettings())
    cog: Any = EventLogCog(bot)
    await cog.on_ggbot_settings_changed(
        events.SettingsChanged(guild_id=GUILD_ID, module="moderation")
    )
    assert await bot.services.eventlog.route(GUILD_ID, "voice") == LOG  # fremdes Modul: unverändert
    await cog.on_ggbot_settings_changed(events.SettingsChanged(guild_id=GUILD_ID, module="logging"))
    assert await bot.services.eventlog.route(GUILD_ID, "voice") is None


def test_jede_ereignisart_wird_von_einem_listener_bedient() -> None:
    """Sonst liesse sich ein Kanal für eine Art wählen, die nie etwas schreibt."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(eventlog_module))
    dispatched = {
        node.args[1].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_dispatch"
        and isinstance(node.args[1], ast.Constant)
    }
    assert dispatched == set(EVENT_TYPES)
