"""Tickets von der Schaltfläche bis zum gelöschten Kanal, mit Fake-Discord-Objekten."""

import re
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from redis.asyncio import Redis
from sqlalchemy import text

from ggbot.bot import tickets as tk
from ggbot.bot.client import GGBot
from ggbot.bot.cogs.tickets import TicketsCog
from ggbot.bot.services import Services
from ggbot.core.config import AppConfig
from ggbot.core.db.engine import Database
from ggbot.core.errors import UserError
from ggbot.core.redis import key
from ggbot.core.settings.tickets import TicketSettings
from tests.bot.test_cog_flows import sent_text
from tests.bot.ticket_fakes import (
    AGENT,
    CREATOR,
    GUILD_ID,
    LOG,
    PARENT,
    STRANGER,
    SUPPORT,
    click,
    fake_message,
    make_world,
    member,
    snowflake_at,
    text_channel,
)
from tests.fakes import invoke


@pytest.fixture
async def bot(db: Database, redis: Redis) -> GGBot:
    return GGBot(Services.build(AppConfig(), db, redis))


async def configure(bot: GGBot, **kwargs: object) -> None:
    data = {"close_delay_seconds": 5} | kwargs
    await bot.services.settings.save(GUILD_ID, TicketSettings.model_validate(data))
    bot.services.tickets.invalidate(GUILD_ID)


async def setup_category(bot: GGBot, **kw: Any) -> int:
    service = bot.services.tickets
    panel = await service.create_panel(GUILD_ID, title="Support")
    view = await service.add_category(
        GUILD_ID, panel.id, name="Hilfe", support_role_id=SUPPORT, **kw
    )
    return view.categories[0].id


async def reset_cooldown(bot: GGBot, user: int = CREATOR) -> None:
    await bot.services.redis.delete(key("cd", "ticketopen", f"{GUILD_ID}:{user}"))


async def open_one(
    bot: GGBot, guild: MagicMock, cat_id: int, user: MagicMock | None = None
) -> tuple[MagicMock, Any]:
    who = user or member(guild, CREATOR)
    await reset_cooldown(bot, who.id)
    interaction = click(bot, guild, who)
    await tk.handle_open_click(interaction, cat_id)
    channel = guild.created[-1]
    ticket = await bot.services.tickets.require_channel(GUILD_ID, channel.id)
    return channel, ticket


# --- Anlegen ---


async def test_ticket_anlegen(bot: GGBot) -> None:
    await configure(bot)
    guild = make_world(bot)
    cat_id = await setup_category(bot, parent_id=PARENT)
    who = member(guild, CREATOR)
    interaction = click(bot, guild, who)
    await tk.handle_open_click(interaction, cat_id)

    channel = guild.created[0]
    assert channel.name == "ticket-0001"
    kwargs = channel.create_kwargs
    assert kwargs["category"] is guild.channels_by_id[PARENT]
    assert "1" in kwargs["topic"]
    overwrites = kwargs["overwrites"]
    assert overwrites[guild.default_role].view_channel is False  # Unbeteiligte sehen nichts
    assert overwrites[who].view_channel is True
    assert overwrites[guild.get_role(SUPPORT)].view_channel is True
    assert overwrites[guild.me].manage_channels is True
    ticket = await bot.services.tickets.require_channel(GUILD_ID, channel.id)
    assert (ticket.creator_id, ticket.number, ticket.category_name) == (CREATOR, 1, "Hilfe")
    assert interaction.response.defer.await_args.kwargs["ephemeral"] is True
    assert channel.mention in sent_text(interaction.followup.send)
    assert interaction.followup.send.await_args.kwargs["ephemeral"] is True


async def test_willkommensnachricht(bot: GGBot) -> None:
    await configure(bot)
    guild = make_world(bot)
    cat_id = await setup_category(bot, welcome_message="Hi {user}, {category} #{number}")
    channel, _ = await open_one(bot, guild, cat_id)
    call = channel.send.await_args
    assert call.args[0] == f"<@{CREATOR}> <@&{SUPPORT}>"
    embed = call.kwargs["embed"]
    assert embed.description == "Hi <@500>, Hilfe #1"
    assert embed.title == "Ticket #1 · Hilfe"
    ids = [child.custom_id for child in call.kwargs["view"].children]
    assert ids == ["gg:tk:close", "gg:tk:claim"]
    mentions = call.kwargs["allowed_mentions"]
    assert mentions.everyone is False
    assert [r.id for r in mentions.roles] == [SUPPORT]


async def test_support_wird_nicht_erwaehnt_wenn_ausgeschaltet(bot: GGBot) -> None:
    await configure(bot, ping_support=False)
    guild = make_world(bot)
    channel, _ = await open_one(bot, guild, await setup_category(bot))
    call = channel.send.await_args
    assert f"<@&{SUPPORT}>" not in call.args[0]
    assert not call.kwargs["allowed_mentions"].roles


async def test_name_aus_vorlage(bot: GGBot) -> None:
    await configure(bot, name_template="{category}-{user}-{number}")
    guild = make_world(bot)
    channel, _ = await open_one(bot, guild, await setup_category(bot))
    assert channel.name == "hilfe-user500-0001"


async def test_formular_vor_dem_ticket(bot: GGBot) -> None:
    await configure(bot)
    guild = make_world(bot)
    cat_id = await setup_category(bot, questions=["Was ist los?", "Seit wann?"])
    who = member(guild, CREATOR)
    interaction = click(bot, guild, who)
    await tk.handle_open_click(interaction, cat_id)
    assert guild.created == []  # erst nach dem Absenden
    modal = interaction.response.send_modal.await_args.args[0]
    assert [i.label for i in modal.inputs] == ["Was ist los?", "Seit wann?"]
    assert all(i.max_length == 1000 for i in modal.inputs)

    modal.inputs[0]._value = "Kaputt"
    modal.inputs[1]._value = "Gestern"
    await reset_cooldown(bot)
    submit = click(bot, guild, who)
    await modal.on_submit(submit)
    channel = guild.created[0]
    ticket = await bot.services.tickets.require_channel(GUILD_ID, channel.id)
    assert ticket.answers == (("Was ist los?", "Kaputt"), ("Seit wann?", "Gestern"))
    fields = channel.send.await_args.kwargs["embed"].fields
    assert [(f.name, f.value) for f in fields] == [
        ("Was ist los?", "Kaputt"),
        ("Seit wann?", "Gestern"),
    ]


async def test_grenze_wird_vor_dem_formular_geprueft(bot: GGBot) -> None:
    await configure(bot, max_open_per_user=1)
    guild = make_world(bot)
    cat_id = await setup_category(bot, questions=["Frage?"])
    who = member(guild, CREATOR)
    await bot.services.tickets.register(
        GUILD_ID,
        number=(await bot.services.tickets.reserve(GUILD_ID, CREATOR, cat_id)).number,
        creator_id=CREATOR,
        channel_id=1,
        category=await bot.services.tickets.get_category(GUILD_ID, cat_id),
        answers=[],
    )
    interaction = click(bot, guild, who)
    with pytest.raises(UserError) as exc:
        await tk.handle_open_click(interaction, cat_id)
    assert exc.value.key == "error.ticket_limit"
    interaction.response.send_modal.assert_not_awaited()


async def test_limit_gilt_auch_beim_absenden(bot: GGBot) -> None:
    await configure(bot, max_open_per_user=1)
    guild = make_world(bot)
    cat_id = await setup_category(bot)
    await open_one(bot, guild, cat_id)
    await reset_cooldown(bot)
    with pytest.raises(UserError) as exc:
        await tk.open_ticket(click(bot, guild, member(guild, CREATOR)), cat_id, [])
    assert exc.value.key == "error.ticket_limit"
    assert len(guild.created) == 1


async def test_klick_cooldown(bot: GGBot) -> None:
    await configure(bot, max_open_per_user=5)
    guild = make_world(bot)
    cat_id = await setup_category(bot)
    await open_one(bot, guild, cat_id)
    with pytest.raises(UserError) as exc:
        await tk.open_ticket(click(bot, guild, member(guild, CREATOR)), cat_id, [])
    assert exc.value.key == "error.cooldown"
    assert len(guild.created) == 1


async def test_geloeschte_kategorie(bot: GGBot) -> None:
    await configure(bot)
    guild = make_world(bot)
    cat_id = await setup_category(bot)
    with pytest.raises(UserError) as exc:
        await tk.handle_open_click(click(bot, guild, member(guild, CREATOR)), cat_id + 99)
    assert exc.value.key == "error.tcat_gone"


@pytest.mark.parametrize(
    ("error", "key_"),
    [
        (discord.Forbidden(MagicMock(status=403), "x"), "error.ticket_create_forbidden"),
        (discord.HTTPException(MagicMock(status=400), "full"), "error.ticket_create_failed"),
    ],
)
async def test_kanal_kann_nicht_angelegt_werden(bot: GGBot, error: Exception, key_: str) -> None:
    await configure(bot)
    guild = make_world(bot)
    guild.create_text_channel = AsyncMock(side_effect=error)
    cat_id = await setup_category(bot)
    with pytest.raises(UserError) as exc:
        await tk.handle_open_click(click(bot, guild, member(guild, CREATOR)), cat_id)
    assert exc.value.key == key_
    assert await bot.services.tickets.open_tickets(GUILD_ID) == []


async def test_scheitert_der_eintrag_wird_der_kanal_wieder_geloescht(
    bot: GGBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    await configure(bot)
    guild = make_world(bot)
    cat_id = await setup_category(bot)
    monkeypatch.setattr(
        bot.services.tickets, "register", AsyncMock(side_effect=RuntimeError("db weg"))
    )
    with pytest.raises(RuntimeError):
        await tk.handle_open_click(click(bot, guild, member(guild, CREATOR)), cat_id)
    guild.created[0].delete.assert_awaited_once()  # kein Kanal ohne Ticket


async def test_nur_mitglieder_koennen_tickets_oeffnen(bot: GGBot) -> None:
    await configure(bot)
    guild = make_world(bot)
    cat_id = await setup_category(bot)
    interaction = click(bot, guild, MagicMock(spec=discord.User))
    with pytest.raises(UserError) as exc:
        await tk.handle_open_click(interaction, cat_id)
    assert exc.value.key == "error.guild_only"


# --- Übernehmen und Berechtigungen ---


async def test_uebernehmen_nur_durch_support(bot: GGBot) -> None:
    await configure(bot)
    guild = make_world(bot)
    channel, _ = await open_one(bot, guild, await setup_category(bot))
    stranger = member(guild, STRANGER)
    with pytest.raises(UserError) as exc:
        await tk.handle_claim(click(bot, guild, stranger, channel))
    assert exc.value.key == "error.ticket_support_only"
    creator = member(guild, CREATOR)
    with pytest.raises(UserError):
        await tk.handle_claim(click(bot, guild, creator, channel))  # Ersteller ist nicht Support

    agent = member(guild, AGENT, SUPPORT)
    interaction = click(bot, guild, agent, channel)
    await tk.handle_claim(interaction)
    assert (await bot.services.tickets.require_channel(GUILD_ID, channel.id)).claimed_by == AGENT
    assert f"<@{AGENT}>" in sent_text(interaction.response.send_message)
    assert interaction.response.send_message.await_args.kwargs["allowed_mentions"].users is False

    other = member(guild, AGENT + 1, SUPPORT)
    with pytest.raises(UserError) as exc:
        await tk.handle_claim(click(bot, guild, other, channel))
    assert exc.value.key == "error.ticket_claimed"


async def test_verwalter_darf_ohne_support_rolle(bot: GGBot) -> None:
    await configure(bot)
    guild = make_world(bot)
    channel, _ = await open_one(bot, guild, await setup_category(bot))
    boss = member(guild, AGENT, manager=True)
    await tk.handle_claim(click(bot, guild, boss, channel))
    assert (await bot.services.tickets.require_channel(GUILD_ID, channel.id)).claimed_by == AGENT


async def test_befehle_ausserhalb_eines_tickets(bot: GGBot) -> None:
    await configure(bot)
    guild = make_world(bot)
    other = text_channel(5555)
    with pytest.raises(UserError) as exc:
        await tk.handle_claim(click(bot, guild, member(guild, AGENT, SUPPORT), other))
    assert exc.value.key == "error.ticket_not_here"


async def test_ticket_eines_anderen_servers_gilt_nicht(bot: GGBot) -> None:
    await configure(bot)
    guild = make_world(bot)
    channel, _ = await open_one(bot, guild, await setup_category(bot))
    guild.id = GUILD_ID + 1
    agent = member(guild, AGENT, SUPPORT)
    with pytest.raises(UserError) as exc:
        await tk.handle_claim(click(bot, guild, agent, channel))
    assert exc.value.key == "error.ticket_not_here"


# --- Schliessen ---


async def test_schliessen_knopf_oeffnet_formular_nur_fuer_berechtigte(bot: GGBot) -> None:
    await configure(bot)
    guild = make_world(bot)
    channel, _ = await open_one(bot, guild, await setup_category(bot))
    with pytest.raises(UserError) as exc:
        await tk.handle_close_click(click(bot, guild, member(guild, STRANGER), channel))
    assert exc.value.key == "error.ticket_cannot_close"
    for uid, roles in ((CREATOR, ()), (AGENT, (SUPPORT,))):
        interaction = click(bot, guild, member(guild, uid, *roles), channel)
        await tk.handle_close_click(interaction)
        modal = interaction.response.send_modal.await_args.args[0]
        assert isinstance(modal, tk.CloseModal)
        assert modal.reason.required is False


async def test_ersteller_darf_nicht_schliessen_wenn_ausgeschaltet(bot: GGBot) -> None:
    await configure(bot, creators_may_close=False)
    guild = make_world(bot)
    channel, _ = await open_one(bot, guild, await setup_category(bot))
    with pytest.raises(UserError) as exc:
        await tk.handle_close_click(click(bot, guild, member(guild, CREATOR), channel))
    assert exc.value.key == "error.ticket_cannot_close"
    await tk.handle_close_click(click(bot, guild, member(guild, AGENT, SUPPORT), channel))


async def close_as(
    bot: GGBot, guild: MagicMock, channel: MagicMock, who: MagicMock, reason: str | None = None
) -> MagicMock:
    interaction = click(bot, guild, who, channel)
    await tk.close_from_interaction(interaction, reason)
    return interaction


async def test_schliessen_gesamter_ablauf(bot: GGBot) -> None:
    await configure(bot, log_channel_id=LOG, close_delay_seconds=7)
    guild = make_world(bot)
    cat_id = await setup_category(bot)
    channel, ticket = await open_one(bot, guild, cat_id)
    channel.messages.extend(
        [
            fake_message("Hallo <script>alert(1)</script>", author="Alice"),
            fake_message(
                "Antwort",
                author="Agent",
                attachments=[MagicMock(filename="a.png", url="https://cdn.example/a.png")],
            ),
        ]
    )
    agent = member(guild, AGENT, SUPPORT)
    creator = guild.members_by_id[CREATOR]
    creator_user = MagicMock()
    creator_user.send = AsyncMock()
    bot.get_user = MagicMock(return_value=creator_user)  # type: ignore[method-assign]
    before = datetime.now(UTC)

    interaction = await close_as(bot, guild, channel, agent, "erledigt")

    closed = await bot.services.tickets.by_channel(GUILD_ID, channel.id)
    assert closed is not None
    assert (closed.status, closed.closed_by, closed.close_reason) == ("closed", AGENT, "erledigt")
    _, html = await bot.services.tickets.transcript(GUILD_ID, ticket.number)
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html
    assert "Antwort" in html
    assert 'href="https://cdn.example/a.png"' in html

    # Hinweis im Kanal, ohne jemanden anzupingen
    notice = channel.send.await_args
    assert "7" in notice.args[0]
    assert notice.kwargs["allowed_mentions"].users is False
    # Protokollkanal bekommt Embed und Datei
    log_call = guild.log_channel.send.await_args
    assert log_call.kwargs["file"].filename == "ticket-0001.html"
    assert f"<@{AGENT}>" in [f.value for f in log_call.kwargs["embed"].fields]
    assert "erledigt" in [f.value for f in log_call.kwargs["embed"].fields]
    # DM an den Ersteller mit Transkript und fünf Bewertungsknöpfen
    dm = creator_user.send.await_args
    assert dm.kwargs["file"].filename == "ticket-0001.html"
    assert [c.custom_id for c in dm.kwargs["view"].children] == [
        f"gg:tkfb:{GUILD_ID}:{ticket.id}:{n}" for n in range(1, 6)
    ]
    assert interaction.followup.send.await_args.kwargs["ephemeral"] is True
    # Löschen des Kanals ist für später vorgemerkt
    async with bot.services.db.session() as session:
        row = (
            await session.execute(text("SELECT kind, run_at, payload FROM scheduled_jobs"))
        ).one()
    assert row.kind == tk.DELETE_JOB
    assert row.payload == {"channel_id": channel.id}
    assert before + timedelta(seconds=5) <= row.run_at <= datetime.now(UTC) + timedelta(seconds=9)
    channel.delete.assert_not_awaited()  # noch nicht: erst nach der Wartezeit
    _ = creator


async def test_dm_ohne_transkript_und_ohne_bewertung(bot: GGBot) -> None:
    await configure(bot, dm_transcript=False, feedback=False)
    guild = make_world(bot)
    channel, _ = await open_one(bot, guild, await setup_category(bot))
    user = MagicMock()
    user.send = AsyncMock()
    bot.get_user = MagicMock(return_value=user)  # type: ignore[method-assign]
    await close_as(bot, guild, channel, guild.members_by_id[CREATOR])
    user.send.assert_not_awaited()  # beides aus → keine DM


async def test_nur_bewertung(bot: GGBot) -> None:
    await configure(bot, dm_transcript=False, feedback=True)
    guild = make_world(bot)
    channel, _ = await open_one(bot, guild, await setup_category(bot))
    user = MagicMock()
    user.send = AsyncMock()
    bot.get_user = MagicMock(return_value=user)  # type: ignore[method-assign]
    await close_as(bot, guild, channel, guild.members_by_id[CREATOR])
    kwargs = user.send.await_args.kwargs
    assert kwargs["file"] is discord.utils.MISSING
    assert len(kwargs["view"].children) == 5


async def test_geschlossene_dms_und_fehlender_log_kanal_stoppen_nichts(bot: GGBot) -> None:
    await configure(bot, log_channel_id=LOG)
    guild = make_world(bot)
    channel, _ = await open_one(bot, guild, await setup_category(bot))
    guild.log_channel.send = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "x"))
    user = MagicMock()
    user.send = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "dm zu"))
    bot.get_user = MagicMock(return_value=user)  # type: ignore[method-assign]
    await close_as(bot, guild, channel, guild.members_by_id[CREATOR])
    closed = await bot.services.tickets.by_channel(GUILD_ID, channel.id)
    assert closed is not None
    assert (
        closed.status == "closed"
    )  # das Transkript ist gespeichert, egal was beim Verteilen schiefgeht


async def test_zweites_schliessen(bot: GGBot) -> None:
    await configure(bot)
    guild = make_world(bot)
    channel, _ = await open_one(bot, guild, await setup_category(bot))
    user = MagicMock()
    user.send = AsyncMock()
    bot.get_user = MagicMock(return_value=user)  # type: ignore[method-assign]
    creator = guild.members_by_id[CREATOR]
    await close_as(bot, guild, channel, creator)
    with pytest.raises(UserError) as exc:
        await close_as(bot, guild, channel, creator)
    assert exc.value.key == "error.ticket_closed"
    assert user.send.await_count == 1  # keine zweite DM, kein zweiter Job
    async with bot.services.db.session() as session:
        assert (await session.execute(text("SELECT count(*) FROM scheduled_jobs"))).scalar() == 1


async def test_fehler_beim_transkript_laesst_das_ticket_offen(
    bot: GGBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    await configure(bot)
    guild = make_world(bot)
    channel, _ = await open_one(bot, guild, await setup_category(bot))

    def boom(*args: Any, **kwargs: Any) -> str:
        raise RuntimeError("render kaputt")

    monkeypatch.setattr(tk, "render_transcript", boom)
    with pytest.raises(RuntimeError):
        await close_as(bot, guild, channel, guild.members_by_id[CREATOR])
    assert (await bot.services.tickets.require_channel(GUILD_ID, channel.id)).is_open
    channel.delete.assert_not_awaited()  # nie löschen, was nicht gesichert ist


async def test_historie_wird_gekuerzt(bot: GGBot, monkeypatch: pytest.MonkeyPatch) -> None:
    await configure(bot)
    guild = make_world(bot)
    channel, _ = await open_one(bot, guild, await setup_category(bot))
    monkeypatch.setattr(tk, "MAX_MESSAGES", 3)
    channel.messages.extend(fake_message(f"m{i}") for i in range(5))
    collected, truncated = await tk.collect_messages(channel)
    assert [m.content for m in collected] == ["m0", "m1", "m2"]
    assert truncated is True
    channel.messages.clear()
    channel.messages.extend(fake_message(f"m{i}") for i in range(3))
    collected, truncated = await tk.collect_messages(channel)
    assert (len(collected), truncated) == (3, False)


# --- Löschen des Kanals ---


async def test_loesch_job_loescht_nur_geschlossene_tickets(bot: GGBot) -> None:
    await configure(bot)
    guild = make_world(bot)
    cog = TicketsCog(bot)
    channel, _ = await open_one(bot, guild, await setup_category(bot))
    job = MagicMock(guild_id=GUILD_ID, payload={"channel_id": channel.id})
    await cog._delete_job(job)
    channel.delete.assert_not_awaited()  # offen → bleibt

    user = MagicMock()
    user.send = AsyncMock()
    bot.get_user = MagicMock(return_value=user)  # type: ignore[method-assign]
    await close_as(bot, guild, channel, guild.members_by_id[CREATOR])
    await cog._delete_job(job)
    channel.delete.assert_awaited_once()

    other = text_channel(9999)
    guild.channels_by_id[9999] = other
    await cog._delete_job(MagicMock(guild_id=GUILD_ID, payload={"channel_id": 9999}))
    other.delete.assert_not_awaited()  # kein Ticket → nie anfassen

    bot.get_guild = MagicMock(return_value=None)  # type: ignore[method-assign]
    with pytest.raises(LookupError):
        await cog._delete_job(job)  # der Scheduler versucht es später wieder


async def test_loesch_job_toleriert_bereits_geloeschten_kanal(bot: GGBot) -> None:
    await configure(bot)
    guild = make_world(bot)
    cog = TicketsCog(bot)
    channel, _ = await open_one(bot, guild, await setup_category(bot))
    user = MagicMock()
    user.send = AsyncMock()
    bot.get_user = MagicMock(return_value=user)  # type: ignore[method-assign]
    await close_as(bot, guild, channel, guild.members_by_id[CREATOR])
    channel.delete = AsyncMock(side_effect=discord.NotFound(MagicMock(status=404), "weg"))
    await cog._delete_job(MagicMock(guild_id=GUILD_ID, payload={"channel_id": channel.id}))


# --- Bewertung ---


async def test_bewertung_per_knopf(bot: GGBot) -> None:
    await configure(bot)
    guild = make_world(bot)
    channel, ticket = await open_one(bot, guild, await setup_category(bot))
    user = MagicMock()
    user.send = AsyncMock()
    bot.get_user = MagicMock(return_value=user)  # type: ignore[method-assign]
    await close_as(bot, guild, channel, guild.members_by_id[CREATOR])

    button = tk.FeedbackButton(GUILD_ID, ticket.id, 4)
    dm = click(bot, guild, guild.members_by_id[CREATOR])
    dm.guild = None
    await button.callback(dm)
    assert "★★★★" in dm.response.edit_message.await_args.kwargs["content"]
    assert dm.response.edit_message.await_args.kwargs["view"] is None
    assert await bot.services.tickets.feedback_summary(GUILD_ID) == (1, 4.0)

    with pytest.raises(UserError) as exc:
        await button.callback(click(bot, guild, guild.members_by_id[CREATOR]))
    assert exc.value.key == "error.ticket_rated"
    stranger = member(guild, STRANGER)
    with pytest.raises(UserError) as exc:
        await tk.FeedbackButton(GUILD_ID, ticket.id, 5).callback(click(bot, guild, stranger))
    assert exc.value.key == "error.ticket_unknown"


async def test_bewertung_wiederherstellung_aus_custom_id(bot: GGBot) -> None:
    match = re.fullmatch(tk.FEEDBACK_TEMPLATE, tk.feedback_id(7, 42, 3))
    assert match is not None
    restored = await tk.FeedbackButton.from_custom_id(MagicMock(), MagicMock(), match)
    assert (restored.guild_id, restored.ticket_id, restored.rating) == (7, 42, 3)
    for bad in ("gg:tkfb:1:2:0", "gg:tkfb:1:2:6", "gg:tkfb:1:2", "gg:tkfb:a:2:3"):
        assert re.fullmatch(tk.FEEDBACK_TEMPLATE, bad) is None


async def test_komponenten_ids_und_vorlagen(bot: GGBot) -> None:
    assert re.fullmatch(tk.OPEN_TEMPLATE, tk.open_id(12))
    assert re.fullmatch(tk.CONTROL_TEMPLATE, "gg:tk:close")
    assert re.fullmatch(tk.CONTROL_TEMPLATE, "gg:tk:claim")
    for bad in ("gg:tk:delete", "gg:tkopen:x", "gg:tk:close:1"):
        assert not re.fullmatch(tk.OPEN_TEMPLATE, bad)
        assert not re.fullmatch(tk.CONTROL_TEMPLATE, bad)
    restored = await tk.OpenButton.from_custom_id(
        MagicMock(),
        MagicMock(),
        re.fullmatch(tk.OPEN_TEMPLATE, tk.open_id(12)),
    )
    assert restored.category_id == 12
    ctl = await tk.TicketControl.from_custom_id(
        MagicMock(),
        MagicMock(),
        re.fullmatch(tk.CONTROL_TEMPLATE, "gg:tk:claim"),
    )
    assert ctl.action == "claim"


async def test_komponenten_sind_registriert(bot: GGBot) -> None:
    await bot.add_cog(TicketsCog(bot))
    patterns = {p.pattern for p in bot._connection._view_store._dynamic_items}
    assert {tk.OPEN_TEMPLATE, tk.CONTROL_TEMPLATE, tk.FEEDBACK_TEMPLATE} <= patterns
    await bot.remove_cog("TicketsCog")


# --- Auto-Close ---


async def test_auto_close(bot: GGBot, monkeypatch: pytest.MonkeyPatch) -> None:
    await configure(bot, auto_close_hours=24)
    guild = make_world(bot)
    monkeypatch.setattr(type(bot), "guilds", property(lambda self: [guild]))
    cat_id = await setup_category(bot)
    stale, stale_ticket = await open_one(bot, guild, cat_id, member(guild, CREATOR))
    fresh, fresh_ticket = await open_one(bot, guild, cat_id, member(guild, CREATOR + 1))
    quiet, quiet_ticket = await open_one(bot, guild, cat_id, member(guild, CREATOR + 2))
    now = datetime.now(UTC) + timedelta(hours=30)
    stale.last_message_id = snowflake_at(now - timedelta(hours=26))
    fresh.last_message_id = snowflake_at(now - timedelta(hours=2))
    quiet.last_message_id = None  # nie jemand geschrieben, aber seit 30 h offen
    user = MagicMock()
    user.send = AsyncMock()
    bot.get_user = MagicMock(return_value=user)  # type: ignore[method-assign]
    cog = TicketsCog(bot)
    assert await cog.sweep(now) == 2
    assert not (await bot.services.tickets.require_channel(GUILD_ID, stale.id)).is_open
    assert (await bot.services.tickets.require_channel(GUILD_ID, fresh.id)).is_open
    assert not (await bot.services.tickets.require_channel(GUILD_ID, quiet.id)).is_open
    closed = await bot.services.tickets.require_channel(GUILD_ID, stale.id)
    assert closed.closed_by == 999  # der Bot
    assert "24" in (closed.close_reason or "")
    _ = (stale_ticket, fresh_ticket, quiet_ticket)


async def test_auto_close_aus_und_ohne_kanal(bot: GGBot, monkeypatch: pytest.MonkeyPatch) -> None:
    guild = make_world(bot)
    monkeypatch.setattr(type(bot), "guilds", property(lambda self: [guild]))
    cat_id = await setup_category(bot)
    await configure(bot, auto_close_hours=0)
    channel, _ = await open_one(bot, guild, cat_id)
    far = datetime.now(UTC) + timedelta(days=100)
    assert await TicketsCog(bot).sweep(far) == 0  # ausgeschaltet
    await configure(bot, auto_close_hours=1)
    del guild.channels_by_id[channel.id]
    assert await TicketsCog(bot).sweep(far) == 0  # Kanal fehlt → anderer Weg (Löschereignis)


async def test_manuell_geloeschter_kanal_schliesst_das_ticket(bot: GGBot) -> None:
    await configure(bot)
    guild = make_world(bot)
    channel, _ = await open_one(bot, guild, await setup_category(bot))
    channel.guild = guild
    cog = TicketsCog(bot)
    await cog.on_guild_channel_delete(channel)
    closed = await bot.services.tickets.require_channel(GUILD_ID, channel.id)
    assert closed.status == "closed"
    assert closed.close_reason is not None
    await cog.on_guild_channel_delete(channel)  # zweites Mal: nichts passiert

    foreign = text_channel(1234)
    foreign.guild = guild
    await cog.on_guild_channel_delete(foreign)  # kein Ticket: nichts passiert


async def test_panel_nachricht_geloescht(bot: GGBot) -> None:
    await configure(bot)
    make_world(bot)
    panel = await bot.services.tickets.create_panel(GUILD_ID, title="t")
    await bot.services.tickets.set_panel_message(GUILD_ID, panel.id, 1, 555)
    cog = TicketsCog(bot)
    event: Any = MagicMock(guild_id=None, message_id=555)
    await cog.on_raw_message_delete(event)
    assert (await bot.services.tickets.get_panel(GUILD_ID, panel.id)).published is True
    event = MagicMock(guild_id=GUILD_ID, message_id=555)
    await cog.on_raw_message_delete(event)
    assert (await bot.services.tickets.get_panel(GUILD_ID, panel.id)).published is False


# --- Befehle im Ticket ---


async def test_befehle_hinzufuegen_entfernen(bot: GGBot) -> None:
    await configure(bot)
    guild = make_world(bot)
    cog = TicketsCog(bot)
    channel, _ = await open_one(bot, guild, await setup_category(bot))
    guest = member(guild, STRANGER)
    agent = member(guild, AGENT, SUPPORT)

    with pytest.raises(UserError) as exc:
        await invoke(cog.add, cog, click(bot, guild, member(guild, CREATOR), channel), guest)
    assert exc.value.key == "error.ticket_support_only"
    channel.set_permissions.assert_not_awaited()

    await invoke(cog.add, cog, click(bot, guild, agent, channel), guest)
    overwrite = channel.set_permissions.await_args.kwargs["overwrite"]
    assert (overwrite.view_channel, overwrite.send_messages) == (True, True)
    await invoke(cog.remove, cog, click(bot, guild, agent, channel), guest)
    assert channel.set_permissions.await_args.kwargs["overwrite"] is None
    with pytest.raises(UserError) as exc:
        await invoke(
            cog.remove, cog, click(bot, guild, agent, channel), guild.members_by_id[CREATOR]
        )
    assert exc.value.key == "error.ticket_remove_creator"


async def test_befehle_in_geschlossenem_ticket(bot: GGBot) -> None:
    await configure(bot)
    guild = make_world(bot)
    cog = TicketsCog(bot)
    channel, _ = await open_one(bot, guild, await setup_category(bot))
    user = MagicMock()
    user.send = AsyncMock()
    bot.get_user = MagicMock(return_value=user)  # type: ignore[method-assign]
    await close_as(bot, guild, channel, guild.members_by_id[CREATOR])
    with pytest.raises(UserError) as exc:
        await invoke(
            cog.add,
            cog,
            click(bot, guild, member(guild, AGENT, SUPPORT), channel),
            member(guild, STRANGER),
        )
    assert exc.value.key == "error.ticket_closed"


async def test_abgeben_befehl(bot: GGBot) -> None:
    await configure(bot)
    guild = make_world(bot)
    cog = TicketsCog(bot)
    channel, _ = await open_one(bot, guild, await setup_category(bot))
    agent = member(guild, AGENT, SUPPORT)
    await invoke(cog.claim, cog, click(bot, guild, agent, channel))
    interaction = click(bot, guild, agent, channel)
    await invoke(cog.unclaim, cog, interaction)
    assert (await bot.services.tickets.require_channel(GUILD_ID, channel.id)).claimed_by is None
    with pytest.raises(UserError):
        await invoke(cog.unclaim, cog, click(bot, guild, member(guild, STRANGER), channel))


async def test_transkript_befehl(bot: GGBot) -> None:
    await configure(bot)
    guild = make_world(bot)
    cog = TicketsCog(bot)
    channel, ticket = await open_one(bot, guild, await setup_category(bot))
    user = MagicMock()
    user.send = AsyncMock()
    bot.get_user = MagicMock(return_value=user)  # type: ignore[method-assign]
    await close_as(bot, guild, channel, guild.members_by_id[CREATOR])

    for who in (
        guild.members_by_id[CREATOR],
        member(guild, AGENT, SUPPORT),
        member(guild, AGENT + 1, manager=True),
    ):
        interaction = click(bot, guild, who)
        await invoke(cog.transcript, cog, interaction, ticket.number)
        sent = interaction.response.send_message.await_args.kwargs
        assert sent["file"].filename == "ticket-0001.html"
        assert sent["ephemeral"] is True
    with pytest.raises(UserError) as exc:
        await invoke(cog.transcript, cog, click(bot, guild, member(guild, STRANGER)), ticket.number)
    assert (
        exc.value.key == "error.ticket_unknown"
    )  # Fremde erfahren nicht einmal, dass es existiert
    with pytest.raises(UserError) as exc:
        await invoke(cog.transcript, cog, click(bot, guild, member(guild, STRANGER)), 99)
    assert exc.value.key == "error.ticket_unknown"


async def test_elternkanal_muss_eine_discord_kategorie_sein(bot: GGBot) -> None:
    await configure(bot)
    guild = make_world(bot)
    # Die ID zeigt auf einen Textkanal (den Log-Kanal), nicht auf eine Kategorie.
    cat_id = await setup_category(bot, parent_id=LOG)
    await open_one(bot, guild, cat_id)
    assert guild.created[0].create_kwargs["category"] is None
