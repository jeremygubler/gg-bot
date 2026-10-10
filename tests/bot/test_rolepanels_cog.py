"""/rolepanel und Reaktions-Rollen von der Eingabe bis zur Wirkung."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from redis.asyncio import Redis

from ggbot.bot.client import GGBot
from ggbot.bot.cogs import rolepanels as rolepanels_module
from ggbot.bot.cogs.rolepanels import RolePanelsCog
from ggbot.bot.services import Services
from ggbot.core.config import AppConfig
from ggbot.core.db.engine import Database
from ggbot.core.errors import UserError
from ggbot.core.services.rolepanels import PanelView
from tests.bot.test_automod_cog import make_text_channel
from tests.bot.test_cog_flows import GUILD_ID, make_guild, make_interaction, make_member, sent_text
from tests.bot.test_panels import role, role_ids, user_with
from tests.fakes import BOT, invoke

CH = 70
ACTOR = 11


@pytest.fixture(autouse=True)
def no_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rolepanels_module, "REACTION_PAUSE", 0)


@pytest.fixture
async def bot(db: Database, redis: Redis) -> GGBot:
    instance = GGBot(Services.build(AppConfig(), db, redis))
    instance._connection.user = SimpleNamespace(id=BOT)  # type: ignore[assignment]
    return instance


def make_message(message_id: int = 900) -> MagicMock:
    message = MagicMock(spec=discord.Message)
    message.id = message_id
    message.edit = AsyncMock()
    message.delete = AsyncMock()
    message.add_reaction = AsyncMock()
    message.clear_reaction = AsyncMock()
    message.remove_reaction = AsyncMock()
    message.reactions = []
    return message


def make_channel(channel_id: int = CH) -> MagicMock:
    channel = make_text_channel(channel_id)
    channel.guild = None
    channel.permissions_for = MagicMock(return_value=discord.Permissions.all())
    channel.send = AsyncMock(side_effect=lambda *a, **k: channel.last)
    channel.last = make_message(900)
    channel.fetch_message = AsyncMock(side_effect=lambda mid: channel.stored[mid])
    channel.stored = {}
    return channel


def world(bot: GGBot, channel: MagicMock, *roles: MagicMock) -> MagicMock:
    guild = make_guild(bot_position=10)
    guild.get_channel_or_thread = MagicMock(side_effect={channel.id: channel}.get)
    table = {r.id: r for r in roles}
    guild.get_role = MagicMock(side_effect=table.get)
    bot.get_guild = MagicMock(return_value=guild)  # type: ignore[method-assign]
    guild.members_by_id = {}
    return guild


def actor_at(position: int) -> MagicMock:
    member = make_member(ACTOR, position)
    member.bot = False
    return member


async def panel_of(
    bot: GGBot, kind: str = "button", mode: str = "normal", *roles: int, **kw: Any
) -> PanelView:
    service = bot.services.rolepanels
    panel = await service.create(GUILD_ID, kind=kind, mode=mode, title="Rollen", **kw)  # type: ignore[arg-type]
    for r, emoji in zip(roles, ("🎮", "🎵", "📚", "🍕"), strict=False):
        panel = await service.add_item(GUILD_ID, panel.id, role_id=r, label=f"R{r}", emoji=emoji)
    return panel


# --- Anlegen ---


async def test_panel_anlegen_und_auflisten(bot: GGBot) -> None:
    cog = RolePanelsCog(bot)
    guild = world(bot, make_channel())
    interaction = make_interaction(bot, guild, actor_at(50))
    await invoke(
        cog.create,
        cog,
        interaction,
        "Spiele",
        SimpleNamespace(value="button"),
        SimpleNamespace(value="unique"),
        "Wähle",
        None,
        None,
    )
    panel = (await bot.services.rolepanels.list(GUILD_ID))[0]
    assert (panel.title, panel.kind, panel.mode, panel.description) == (
        "Spiele",
        "button",
        "unique",
        "Wähle",
    )
    assert f"#{panel.id}" in sent_text(interaction.response.send_message)

    listing = make_interaction(bot, guild, actor_at(50))
    await invoke(cog.list_, cog, listing)
    text = sent_text(listing.response.send_message)
    assert "Spiele" in text
    assert listing.response.send_message.await_args.kwargs["allowed_mentions"].roles is False


async def test_liste_ohne_panels(bot: GGBot) -> None:
    cog = RolePanelsCog(bot)
    interaction = make_interaction(bot, world(bot, make_channel()), actor_at(50))
    await invoke(cog.list_, cog, interaction)
    assert "/rolepanel create" in sent_text(interaction.response.send_message)


async def test_vorhandene_nachricht_uebernehmen(bot: GGBot) -> None:
    cog = RolePanelsCog(bot)
    channel = make_channel()
    channel.stored[555] = make_message(555)
    guild = world(bot, channel)
    interaction = make_interaction(bot, guild, actor_at(50))
    await invoke(
        cog.create,
        cog,
        interaction,
        "Alt",
        SimpleNamespace(value="reaction"),
        None,
        None,
        "555",
        channel,
    )
    panel = (await bot.services.rolepanels.list(GUILD_ID))[0]
    assert (panel.message_id, panel.channel_id, panel.external) == (555, CH, True)


@pytest.mark.parametrize(
    ("message_id", "with_channel", "kind", "key"),
    [
        ("abc", True, "reaction", "error.panel_message_invalid"),
        ("123", False, "reaction", "error.panel_message_needs_channel"),
        ("999", True, "reaction", "error.panel_message_not_found"),
        ("555", True, "button", "error.panel_external_only_reactions"),
    ],
)
async def test_vorhandene_nachricht_fehler(
    bot: GGBot, message_id: str, with_channel: bool, kind: str, key: str
) -> None:
    cog = RolePanelsCog(bot)
    channel = make_channel()
    channel.stored[555] = make_message(555)
    channel.fetch_message = AsyncMock(
        side_effect=lambda mid: (
            channel.stored[mid]
            if mid in channel.stored
            else (_ for _ in ()).throw(discord.NotFound(MagicMock(status=404), "x"))
        )
    )
    guild = world(bot, channel)
    interaction = make_interaction(bot, guild, actor_at(50))
    with pytest.raises(UserError) as exc:
        await invoke(
            cog.create,
            cog,
            interaction,
            "T",
            SimpleNamespace(value=kind),
            None,
            None,
            message_id,
            channel if with_channel else None,
        )
    assert exc.value.key == key
    assert await bot.services.rolepanels.list(GUILD_ID) == []


# --- Rollen hinzufügen und entfernen ---


async def test_rolle_hinzufuegen_prueft_die_hierarchie(bot: GGBot) -> None:
    cog = RolePanelsCog(bot)
    guild = world(
        bot,
        make_channel(),
        role(1, position=3),
        role(2, position=6),
        role(3, position=10),
        role(4, position=2, managed=True),
    )
    panel = await panel_of(bot)
    actor = actor_at(5)

    ok = make_interaction(bot, guild, actor)
    await invoke(cog.add, cog, ok, panel.id, guild.get_role(1), "Eins", None, None)
    assert (await bot.services.rolepanels.get(GUILD_ID, panel.id)).role_ids == (1,)

    for role_id, key in (
        (2, "error.hierarchy_actor"),
        (3, "error.role_unassignable"),
        (4, "error.role_unassignable"),
    ):
        with pytest.raises(UserError) as exc:
            await invoke(
                cog.add,
                cog,
                make_interaction(bot, guild, actor),
                panel.id,
                guild.get_role(role_id),
                "x",
                None,
                None,
            )
        assert exc.value.key == key, role_id
    assert (await bot.services.rolepanels.get(GUILD_ID, panel.id)).role_ids == (1,)


async def test_besitzer_darf_auch_ueber_der_eigenen_rolle(bot: GGBot) -> None:
    cog = RolePanelsCog(bot)
    guild = world(bot, make_channel(), role(2, position=6))
    owner = make_member(guild.owner_id, 1)
    panel = await panel_of(bot)
    await invoke(
        cog.add,
        cog,
        make_interaction(bot, guild, owner),
        panel.id,
        guild.get_role(2),
        "Zwei",
        None,
        None,
    )
    assert (await bot.services.rolepanels.get(GUILD_ID, panel.id)).role_ids == (2,)


async def test_ungueltiges_emoji(bot: GGBot) -> None:
    cog = RolePanelsCog(bot)
    guild = world(bot, make_channel(), role(1))
    panel = await panel_of(bot, "reaction")
    for bad in ("", "  ", "<:kaputt:>"):
        with pytest.raises(UserError) as exc:
            await invoke(
                cog.add,
                cog,
                make_interaction(bot, guild, actor_at(5)),
                panel.id,
                guild.get_role(1),
                None,
                bad,
                None,
            )
        assert exc.value.key == "error.panel_emoji_invalid", bad
    assert (await bot.services.rolepanels.get(GUILD_ID, panel.id)).items == ()


async def test_fremdes_panel_laesst_sich_nicht_bearbeiten(bot: GGBot) -> None:
    cog = RolePanelsCog(bot)
    guild = world(bot, make_channel(), role(1))
    other = await bot.services.rolepanels.create(
        GUILD_ID + 1, kind="button", mode="normal", title="x"
    )
    for call in (
        invoke(
            cog.add,
            cog,
            make_interaction(bot, guild, actor_at(5)),
            other.id,
            guild.get_role(1),
            "a",
            None,
            None,
        ),
        invoke(
            cog.remove, cog, make_interaction(bot, guild, actor_at(5)), other.id, guild.get_role(1)
        ),
        invoke(cog.delete, cog, make_interaction(bot, guild, actor_at(5)), other.id),
        invoke(cog.publish, cog, make_interaction(bot, guild, actor_at(5)), other.id, None),
    ):
        with pytest.raises(UserError) as exc:
            await call
        assert exc.value.key == "error.panel_not_found"
    assert (await bot.services.rolepanels.get(GUILD_ID + 1, other.id)).title == "x"


async def test_rolle_entfernen_aktualisiert_die_nachricht(bot: GGBot) -> None:
    cog = RolePanelsCog(bot)
    channel = make_channel()
    message = make_message(900)
    channel.stored[900] = message
    guild = world(bot, channel, role(1), role(2))
    panel = await panel_of(bot, "button", "normal", 1, 2)
    await bot.services.rolepanels.set_message(GUILD_ID, panel.id, CH, 900)
    await invoke(
        cog.remove, cog, make_interaction(bot, guild, actor_at(5)), panel.id, guild.get_role(1)
    )
    assert (await bot.services.rolepanels.get(GUILD_ID, panel.id)).role_ids == (2,)
    message.edit.assert_awaited_once()
    embed = message.edit.await_args.kwargs["embed"]
    assert "<@&1>" not in embed.description
    assert "<@&2>" in embed.description
    with pytest.raises(UserError) as exc:
        await invoke(
            cog.remove, cog, make_interaction(bot, guild, actor_at(5)), panel.id, guild.get_role(1)
        )
    assert exc.value.key == "error.panel_role_not_in_panel"


async def test_reaktion_beim_entfernen_der_rolle_wegnehmen(bot: GGBot) -> None:
    cog = RolePanelsCog(bot)
    channel = make_channel()
    message = make_message(900)
    channel.stored[900] = message
    guild = world(bot, channel, role(1), role(2))
    panel = await panel_of(bot, "reaction", "normal", 1, 2)
    await bot.services.rolepanels.set_message(GUILD_ID, panel.id, CH, 900)
    await invoke(
        cog.remove, cog, make_interaction(bot, guild, actor_at(5)), panel.id, guild.get_role(1)
    )
    message.clear_reaction.assert_awaited_once()


# --- Veröffentlichen ---


async def test_veroeffentlichen_sendet_und_merkt_die_nachricht(bot: GGBot) -> None:
    cog = RolePanelsCog(bot)
    channel = make_channel()
    guild = world(bot, channel, role(1), role(2))
    panel = await panel_of(bot, "button", "normal", 1, 2)
    interaction = make_interaction(bot, guild, actor_at(5))
    await invoke(cog.publish, cog, interaction, panel.id, channel)
    channel.send.assert_awaited_once()
    kwargs = channel.send.await_args.kwargs
    assert kwargs["embed"].title == "Rollen"
    assert len(kwargs["view"].children) == 2
    assert kwargs["allowed_mentions"].roles is False
    stored = await bot.services.rolepanels.get(GUILD_ID, panel.id)
    assert (stored.channel_id, stored.message_id) == (CH, 900)


async def test_erneutes_veroeffentlichen_ersetzt_die_alte_nachricht(bot: GGBot) -> None:
    cog = RolePanelsCog(bot)
    channel = make_channel()
    old = make_message(800)
    channel.stored[800] = old
    guild = world(bot, channel, role(1))
    panel = await panel_of(bot, "button", "normal", 1)
    await bot.services.rolepanels.set_message(GUILD_ID, panel.id, CH, 800)
    await invoke(cog.publish, cog, make_interaction(bot, guild, actor_at(5)), panel.id, channel)
    old.delete.assert_awaited_once()
    assert (await bot.services.rolepanels.get(GUILD_ID, panel.id)).message_id == 900


async def test_reaktions_panel_setzt_die_reaktionen(bot: GGBot) -> None:
    cog = RolePanelsCog(bot)
    channel = make_channel()
    guild = world(bot, channel, role(1), role(2))
    panel = await panel_of(bot, "reaction", "normal", 1, 2)
    await invoke(cog.publish, cog, make_interaction(bot, guild, actor_at(5)), panel.id, channel)
    emojis = [str(c.args[0]) for c in channel.last.add_reaction.await_args_list]
    assert emojis == ["🎮", "🎵"]
    assert channel.send.await_args.kwargs["view"] is discord.utils.MISSING


@pytest.mark.parametrize(
    ("setup", "key"),
    [("empty", "error.panel_empty"), ("external", "error.panel_external_publish")],
)
async def test_veroeffentlichen_fehler(bot: GGBot, setup: str, key: str) -> None:
    cog = RolePanelsCog(bot)
    channel = make_channel()
    guild = world(bot, channel, role(1))
    if setup == "empty":
        panel = await panel_of(bot)
    else:
        panel = await panel_of(bot, "reaction", "normal", 1, channel_id=CH, message_id=555)
    with pytest.raises(UserError) as exc:
        await invoke(cog.publish, cog, make_interaction(bot, guild, actor_at(5)), panel.id, channel)
    assert exc.value.key == key
    channel.send.assert_not_awaited()


async def test_veroeffentlichen_ohne_bot_rechte(bot: GGBot) -> None:
    cog = RolePanelsCog(bot)
    channel = make_channel()
    channel.permissions_for = MagicMock(return_value=discord.Permissions(view_channel=True))
    guild = world(bot, channel, role(1))
    panel = await panel_of(bot, "button", "normal", 1)
    with pytest.raises(UserError) as exc:
        await invoke(cog.publish, cog, make_interaction(bot, guild, actor_at(5)), panel.id, channel)
    assert exc.value.key == "error.missing_bot_permissions"
    assert "Send Messages" in str(exc.value.params["permissions"])
    channel.send.assert_not_awaited()


async def test_reaktions_panel_braucht_add_reactions(bot: GGBot) -> None:
    cog = RolePanelsCog(bot)
    channel = make_channel()
    channel.permissions_for = MagicMock(
        return_value=discord.Permissions(view_channel=True, send_messages=True, embed_links=True)
    )
    guild = world(bot, channel, role(1))
    panel = await panel_of(bot, "reaction", "normal", 1)
    with pytest.raises(UserError) as exc:
        await invoke(cog.publish, cog, make_interaction(bot, guild, actor_at(5)), panel.id, channel)
    assert "Add Reactions" in str(exc.value.params["permissions"])


async def test_loeschen_entfernt_die_nachricht_ausser_bei_fremder(bot: GGBot) -> None:
    cog = RolePanelsCog(bot)
    channel = make_channel()
    mine, theirs = make_message(900), make_message(555)
    channel.stored.update({900: mine, 555: theirs})
    guild = world(bot, channel, role(1))
    own = await panel_of(bot, "button", "normal", 1)
    await bot.services.rolepanels.set_message(GUILD_ID, own.id, CH, 900)
    await invoke(cog.delete, cog, make_interaction(bot, guild, actor_at(5)), own.id)
    mine.delete.assert_awaited_once()

    external = await panel_of(bot, "reaction", "normal", 1, channel_id=CH, message_id=555)
    await invoke(cog.delete, cog, make_interaction(bot, guild, actor_at(5)), external.id)
    theirs.delete.assert_not_awaited()  # die Nachricht gehört nicht dem Bot
    assert await bot.services.rolepanels.list(GUILD_ID) == []


async def test_autocomplete_zeigt_nur_panels_dieses_servers(bot: GGBot) -> None:
    cog = RolePanelsCog(bot)
    guild = world(bot, make_channel())
    mine = await panel_of(bot)
    await bot.services.rolepanels.create(
        GUILD_ID + 1, kind="button", mode="normal", title="Rollen fremd"
    )
    interaction = make_interaction(bot, guild, actor_at(5))
    choices = await cog._panel_choices(interaction, "rol")
    assert [c.value for c in choices] == [mine.id]
    assert await cog._panel_choices(interaction, "zzz") == []
    interaction.guild_id = None
    assert await cog._panel_choices(interaction, "") == []


# --- Reaktions-Rollen ---


def reaction(message_id: int, emoji: str, member: MagicMock | None, user_id: int = 500) -> Any:
    return SimpleNamespace(
        guild_id=GUILD_ID, message_id=message_id, user_id=user_id, emoji=emoji, member=member
    )


async def reaction_world(
    bot: GGBot, mode: str = "normal"
) -> tuple[RolePanelsCog, MagicMock, MagicMock, MagicMock]:
    cog = RolePanelsCog(bot)
    channel = make_channel()
    message = make_message(900)
    channel.stored[900] = message
    guild = world(bot, channel, role(1), role(2), role(3))
    panel = await panel_of(bot, "reaction", mode, 1, 2, 3)
    await bot.services.rolepanels.set_message(GUILD_ID, panel.id, CH, 900)
    member = user_with(guild)
    guild.get_member = MagicMock(return_value=member)
    return cog, guild, member, message


async def test_reaktion_gibt_rolle_und_nimmt_sie_zurueck(bot: GGBot) -> None:
    cog, _, member, _ = await reaction_world(bot)
    await cog.on_raw_reaction_add(reaction(900, "🎮", member))
    assert role_ids(member) == {1}
    await cog.on_raw_reaction_remove(reaction(900, "🎮", None))
    assert role_ids(member) == set()


async def test_reaktion_ignoriert_variationsselektor(bot: GGBot) -> None:
    cog = RolePanelsCog(bot)
    channel = make_channel()
    guild = world(bot, channel, role(1))
    panel = await bot.services.rolepanels.create(
        GUILD_ID, kind="reaction", mode="normal", title="t"
    )
    await bot.services.rolepanels.add_item(GUILD_ID, panel.id, role_id=1, emoji="✔️")
    await bot.services.rolepanels.set_message(GUILD_ID, panel.id, CH, 900)
    member = user_with(guild)
    await cog.on_raw_reaction_add(reaction(900, "✔", member))
    assert role_ids(member) == {1}


async def test_eigenes_emoji_wird_ueber_die_id_erkannt(bot: GGBot) -> None:
    cog = RolePanelsCog(bot)
    guild = world(bot, make_channel(), role(1))
    panel = await bot.services.rolepanels.create(
        GUILD_ID, kind="reaction", mode="normal", title="t"
    )
    await bot.services.rolepanels.add_item(
        GUILD_ID, panel.id, role_id=1, emoji="<:party:123456789012345678>"
    )
    await bot.services.rolepanels.set_message(GUILD_ID, panel.id, CH, 900)
    member = user_with(guild)
    await cog.on_raw_reaction_add(reaction(900, "<:umbenannt:123456789012345678>", member))
    assert role_ids(member) == {1}


async def test_unique_reaktion_tauscht_rolle_und_raeumt_reaktionen_auf(bot: GGBot) -> None:
    cog, _guild, member, message = await reaction_world(bot, "unique")
    member.roles = [SimpleNamespace(id=1)]
    message.reactions = [SimpleNamespace(emoji="🎮"), SimpleNamespace(emoji="🎵")]
    await cog.on_raw_reaction_add(reaction(900, "🎵", member))
    assert role_ids(member) == {2}
    removed = [str(c.args[0]) for c in message.remove_reaction.await_args_list]
    assert removed == ["🎮"]  # die eigene neue Reaktion bleibt


async def test_verify_reaktion_wird_nie_entzogen(bot: GGBot) -> None:
    cog, _, member, _ = await reaction_world(bot, "verify")
    await cog.on_raw_reaction_add(reaction(900, "🎮", member))
    await cog.on_raw_reaction_remove(reaction(900, "🎮", None))
    assert role_ids(member) == {1}
    member.remove_roles.assert_not_awaited()


@pytest.mark.parametrize(
    "event",
    [
        reaction(901, "🎮", None),  # andere Nachricht
        reaction(900, "👍", None),  # Emoji gehört nicht zum Panel
        reaction(900, "🎮", None, user_id=BOT),  # die eigenen Reaktionen des Bots
        SimpleNamespace(guild_id=None, message_id=900, user_id=500, emoji="🎮", member=None),  # DM
    ],
)
async def test_irrelevante_reaktionen_aendern_nichts(bot: GGBot, event: Any) -> None:
    cog, _, member, _ = await reaction_world(bot)
    event.member = member if event.member is None else event.member
    await cog.on_raw_reaction_add(event)
    await cog.on_raw_reaction_remove(event)
    assert role_ids(member) == set()
    member.add_roles.assert_not_awaited()


async def test_reaktion_von_bots_wird_ignoriert(bot: GGBot) -> None:
    cog, _, member, _ = await reaction_world(bot)
    member.bot = True
    await cog.on_raw_reaction_add(reaction(900, "🎮", member))
    member.add_roles.assert_not_awaited()


async def test_reaktion_auf_panel_eines_anderen_servers(bot: GGBot) -> None:
    cog = RolePanelsCog(bot)
    channel = make_channel()
    guild = world(bot, channel, role(1))
    other = await bot.services.rolepanels.create(
        GUILD_ID + 1, kind="reaction", mode="normal", title="t", channel_id=CH
    )
    await bot.services.rolepanels.add_item(GUILD_ID + 1, other.id, role_id=1, emoji="🎮")
    await bot.services.rolepanels.set_message(GUILD_ID + 1, other.id, CH, 900)
    member = user_with(guild)
    await cog.on_raw_reaction_add(
        reaction(900, "🎮", member)
    )  # gleiche Nachrichten-ID, anderer Server
    member.add_roles.assert_not_awaited()


async def test_reaktion_ohne_bot_rechte_wirft_nicht(bot: GGBot) -> None:
    cog, _, member, _ = await reaction_world(bot)
    member.add_roles = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "x"))
    await cog.on_raw_reaction_add(reaction(900, "🎮", member))  # kein Absturz im Listener


# --- Aufräumen ---


def raw(**kw: Any) -> Any:
    return SimpleNamespace(**kw)


async def test_geloeschte_nachricht_macht_das_panel_unveroeffentlicht(bot: GGBot) -> None:
    cog = RolePanelsCog(bot)
    world(bot, make_channel())
    panel = await panel_of(bot, "button", "normal", 1)
    await bot.services.rolepanels.set_message(GUILD_ID, panel.id, CH, 900)
    await cog.on_raw_message_delete(raw(guild_id=GUILD_ID, message_id=901))
    assert (await bot.services.rolepanels.get(GUILD_ID, panel.id)).published is True
    await cog.on_raw_message_delete(raw(guild_id=None, message_id=900))
    assert (await bot.services.rolepanels.get(GUILD_ID, panel.id)).published is True
    await cog.on_raw_message_delete(raw(guild_id=GUILD_ID, message_id=900))
    assert (await bot.services.rolepanels.get(GUILD_ID, panel.id)).published is False


async def test_massenloeschung(bot: GGBot) -> None:
    cog = RolePanelsCog(bot)
    world(bot, make_channel())
    panel = await panel_of(bot, "button", "normal", 1)
    await bot.services.rolepanels.set_message(GUILD_ID, panel.id, CH, 900)
    await cog.on_raw_bulk_message_delete(raw(guild_id=GUILD_ID, message_ids={1, 900, 2}))
    assert (await bot.services.rolepanels.get(GUILD_ID, panel.id)).published is False


async def test_abgelehnte_reaktionen_werden_gemeldet(bot: GGBot) -> None:
    cog = RolePanelsCog(bot)
    channel = make_channel()
    guild = world(bot, channel, role(1), role(2))
    panel = await panel_of(bot, "reaction", "normal", 1, 2)

    async def add_reaction(emoji: Any) -> None:
        if str(emoji) == "🎵":
            raise discord.HTTPException(MagicMock(status=400), "Unknown Emoji")

    channel.last.add_reaction = AsyncMock(side_effect=add_reaction)
    interaction = make_interaction(bot, guild, actor_at(5))
    await invoke(cog.publish, cog, interaction, panel.id, channel)
    text = sent_text(interaction.followup.send)
    assert "🎵" in text
    assert "🎮" not in text
    assert (await bot.services.rolepanels.get(GUILD_ID, panel.id)).published is True


async def test_reaktion_entfernen_ohne_mitglied_im_cache(bot: GGBot) -> None:
    cog, guild, member, _ = await reaction_world(bot)
    await cog.on_raw_reaction_add(reaction(900, "🎮", member))
    assert role_ids(member) == {1}
    guild.get_member = MagicMock(return_value=None)  # ohne Members-Intent nicht im Cache
    guild.fetch_member = AsyncMock(return_value=member)
    await cog.on_raw_reaction_remove(reaction(900, "🎮", None))
    guild.fetch_member.assert_awaited_once_with(500)
    assert role_ids(member) == set()


async def test_reaktion_von_jemandem_der_gegangen_ist(bot: GGBot) -> None:
    cog, guild, member, _ = await reaction_world(bot)
    guild.get_member = MagicMock(return_value=None)
    guild.fetch_member = AsyncMock(side_effect=discord.NotFound(MagicMock(status=404), "x"))
    await cog.on_raw_reaction_remove(reaction(900, "🎮", None))  # kein Absturz
    member.remove_roles.assert_not_awaited()
