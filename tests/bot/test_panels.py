"""Klicks, Auswahlen und Nachrichten der Rollen-Panels, mit Fake-Discord-Objekten."""

import re
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from redis.asyncio import Redis

from ggbot.bot import panels
from ggbot.bot.client import GGBot
from ggbot.bot.cogs.rolepanels import RolePanelsCog
from ggbot.bot.services import Services
from ggbot.core.config import AppConfig
from ggbot.core.db.engine import Database
from ggbot.core.errors import UserError
from ggbot.core.i18n import translator
from ggbot.core.redis import key
from ggbot.core.roles.plan import Change
from ggbot.core.services.rolepanels import PanelView
from tests.bot.test_cog_flows import GUILD_ID, make_guild, make_interaction, make_member, sent_text

USER = 500


@pytest.fixture
async def bot(db: Database, redis: Redis) -> GGBot:
    return GGBot(Services.build(AppConfig(), db, redis))


def role(role_id: int, *, position: int = 1, managed: bool = False) -> MagicMock:
    mock = MagicMock(spec=discord.Role)
    mock.id = role_id
    mock.position = position
    mock.managed = managed
    mock.mention = f"<@&{role_id}>"
    mock.is_default = MagicMock(return_value=False)
    return mock


def world(*roles: MagicMock) -> MagicMock:
    guild = make_guild(bot_position=10)
    table = {r.id: r for r in roles}
    guild.get_role = MagicMock(side_effect=table.get)
    return guild


def user_with(guild: MagicMock, *role_ids: int) -> MagicMock:
    """Ein Mitglied, dessen Rollen sich bei add/remove_roles wirklich ändern."""
    member = make_member(USER, 1, guild)
    member.bot = False
    member.roles = [SimpleNamespace(id=r) for r in role_ids]
    member.calls = []

    async def add(*given: Any, **_: Any) -> None:
        member.calls.append(("add", [r.id for r in given]))
        member.roles += [SimpleNamespace(id=r.id) for r in given]

    async def remove(*given: Any, **_: Any) -> None:
        member.calls.append(("remove", [r.id for r in given]))
        gone = {r.id for r in given}
        member.roles = [r for r in member.roles if r.id not in gone]

    member.add_roles = AsyncMock(side_effect=add)
    member.remove_roles = AsyncMock(side_effect=remove)
    return member


def role_ids(member: MagicMock) -> set[int]:
    return {r.id for r in member.roles}


async def new_panel(
    bot: GGBot, kind: str = "button", mode: str = "normal", *roles: int
) -> PanelView:
    service = bot.services.rolepanels
    panel = await service.create(GUILD_ID, kind=kind, mode=mode, title="Rollen")  # type: ignore[arg-type]
    for r in roles or (1, 2, 3):
        panel = await service.add_item(
            GUILD_ID,
            panel.id,
            role_id=r,
            label=f"Rolle {r}",
            emoji="🎮" if r == 1 and kind == "reaction" else None,
        )
    return panel


async def click(
    bot: GGBot, member: MagicMock, guild: MagicMock, panel: PanelView, role_id: int
) -> MagicMock:
    # Ohne das gälte der Klick-Cooldown auch zwischen den Schritten eines Tests.
    await bot.services.redis.delete(key("cd", "rolepanel", f"{GUILD_ID}:{USER}"))
    interaction = make_interaction(bot, guild, member)
    item = panel.item_by_role(role_id)
    assert item is not None
    await panels.handle_button(interaction, panel.id, item.id)
    return interaction


async def choose(
    bot: GGBot, member: MagicMock, guild: MagicMock, panel: PanelView, values: list[int]
) -> MagicMock:
    await bot.services.redis.delete(key("cd", "rolepanel", f"{GUILD_ID}:{USER}"))
    interaction = make_interaction(bot, guild, member)
    await panels.handle_select(interaction, panel.id, values)
    return interaction


# --- Buttons ---


async def test_button_schaltet_um(bot: GGBot) -> None:
    guild = world(role(1), role(2), role(3))
    member = user_with(guild)
    panel = await new_panel(bot)
    first = await click(bot, member, guild, panel, 1)
    assert role_ids(member) == {1}
    assert "<@&1>" in sent_text(first.response.send_message)
    assert first.response.send_message.await_args.kwargs["ephemeral"] is True
    second = await click(bot, member, guild, panel, 1)
    assert role_ids(member) == set()
    assert "<@&1>" in sent_text(second.response.send_message)


async def test_unique_tauscht_und_entfernt_zuerst(bot: GGBot) -> None:
    guild = world(role(1), role(2), role(3), role(99))
    member = user_with(guild, 1, 99)
    panel = await new_panel(bot, "button", "unique")
    await click(bot, member, guild, panel, 2)
    assert role_ids(member) == {2, 99}  # die fremde Rolle bleibt
    assert member.calls == [("remove", [1]), ("add", [2])]  # nie kurz zwei Rollen des Panels


async def test_verify_gibt_nur_und_nimmt_nie(bot: GGBot) -> None:
    guild = world(role(1), role(2), role(3))
    member = user_with(guild)
    panel = await new_panel(bot, "button", "verify")
    await click(bot, member, guild, panel, 1)
    again = await click(bot, member, guild, panel, 1)
    assert role_ids(member) == {1}
    assert member.remove_roles.await_count == 0
    assert member.add_roles.await_count == 1
    unchanged = translator.t(bot.services.config.default_locale, "roles.unchanged")
    assert sent_text(again.response.send_message) == unchanged


async def test_rolle_ueber_dem_bot_wird_gemeldet_nicht_versucht(bot: GGBot) -> None:
    guild = world(role(1, position=10), role(2), role(3))  # gleiche Position wie der Bot
    member = user_with(guild)
    panel = await new_panel(bot)
    interaction = await click(bot, member, guild, panel, 1)
    member.add_roles.assert_not_awaited()
    assert role_ids(member) == set()
    assert "<@&1>" in sent_text(interaction.response.send_message)


async def test_verwaltete_und_geloeschte_rolle_werden_uebersprungen(bot: GGBot) -> None:
    guild = world(role(1, managed=True), role(3))  # Rolle 2 gibt es nicht mehr
    member = user_with(guild)
    panel = await new_panel(bot)
    await click(bot, member, guild, panel, 1)
    await click(bot, member, guild, panel, 2)
    member.add_roles.assert_not_awaited()


async def test_fehlende_rechte_werden_zur_freundlichen_meldung(bot: GGBot) -> None:
    guild = world(role(1), role(2), role(3))
    member = user_with(guild)
    member.add_roles = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "x"))
    panel = await new_panel(bot)
    with pytest.raises(UserError) as exc:
        await click(bot, member, guild, panel, 1)
    assert exc.value.key == "error.panel_forbidden"


async def test_klick_cooldown(bot: GGBot) -> None:
    guild = world(role(1), role(2), role(3))
    member = user_with(guild)
    panel = await new_panel(bot)
    await click(bot, member, guild, panel, 1)
    interaction = make_interaction(bot, guild, member)
    item = panel.item_by_role(2)
    assert item is not None
    with pytest.raises(UserError) as exc:
        await panels.handle_button(interaction, panel.id, item.id)  # sofort danach
    assert exc.value.key == "error.cooldown"
    assert role_ids(member) == {1}


async def test_geloeschtes_panel_und_fremder_server(bot: GGBot) -> None:
    guild = world(role(1), role(2), role(3))
    member = user_with(guild)
    panel = await new_panel(bot)
    item = panel.items[0]
    await bot.services.rolepanels.delete(GUILD_ID, panel.id)
    interaction = make_interaction(bot, guild, member)
    with pytest.raises(UserError) as exc:
        await panels.handle_button(interaction, panel.id, item.id)
    assert exc.value.key == "error.panel_not_found"

    other = await bot.services.rolepanels.create(
        GUILD_ID + 1, kind="button", mode="normal", title="x"
    )
    await bot.services.rolepanels.add_item(GUILD_ID + 1, other.id, role_id=1, label="a")
    await bot.services.redis.flushdb()
    with pytest.raises(UserError) as exc2:
        await panels.handle_button(interaction, other.id, 1)  # Panel eines anderen Servers
    assert exc2.value.key == "error.panel_not_found"


async def test_entfernter_eintrag_alter_button(bot: GGBot) -> None:
    guild = world(role(1), role(2), role(3))
    member = user_with(guild)
    panel = await new_panel(bot)
    gone = panel.items[0]
    await bot.services.rolepanels.remove_item(GUILD_ID, panel.id, gone.role_id)
    interaction = make_interaction(bot, guild, member)
    with pytest.raises(UserError) as exc:
        await panels.handle_button(interaction, panel.id, gone.id)
    assert exc.value.key == "error.panel_item_gone"


async def test_nur_mitglieder_koennen_klicken(bot: GGBot) -> None:
    guild = world(role(1))
    panel = await new_panel(bot, "button", "normal", 1)
    interaction = make_interaction(bot, guild, MagicMock(spec=discord.User))
    with pytest.raises(UserError) as exc:
        await panels.handle_button(interaction, panel.id, panel.items[0].id)
    assert exc.value.key == "error.guild_only"


# --- Auswahlmenü ---


async def test_auswahl_setzt_den_gewuenschten_stand(bot: GGBot) -> None:
    guild = world(role(1), role(2), role(3), role(99))
    member = user_with(guild, 3, 99)
    panel = await new_panel(bot, "select")
    await choose(bot, member, guild, panel, [1, 2])
    assert role_ids(member) == {1, 2, 99}  # 3 abgewählt, 99 nicht Teil des Panels
    await choose(bot, member, guild, panel, [])
    assert role_ids(member) == {99}


async def test_auswahl_ignoriert_fremde_rollen_in_der_anfrage(bot: GGBot) -> None:
    guild = world(role(1), role(2), role(3), role(77))
    member = user_with(guild)
    panel = await new_panel(bot, "select")
    await choose(bot, member, guild, panel, [1, 77])  # 77 gehört nicht zum Panel
    assert role_ids(member) == {1}


async def test_auswahl_unique_erlaubt_nur_eine(bot: GGBot) -> None:
    guild = world(role(1), role(2), role(3))
    member = user_with(guild)
    panel = await new_panel(bot, "select", "unique")
    with pytest.raises(UserError) as exc:
        await choose(bot, member, guild, panel, [1, 2])
    assert exc.value.key == "error.panel_unique_many"
    await choose(bot, member, guild, panel, [2])
    await choose(bot, member, guild, panel, [3])
    assert role_ids(member) == {3}


async def test_auswahl_verify_entfernt_nie(bot: GGBot) -> None:
    guild = world(role(1), role(2), role(3))
    member = user_with(guild, 1)
    panel = await new_panel(bot, "select", "verify")
    await choose(bot, member, guild, panel, [2])
    assert role_ids(member) == {1, 2}
    member.remove_roles.assert_not_awaited()


# --- Komponenten und Persistenz ---


def test_vorlagen_erkennen_nur_eigene_ids() -> None:
    button = re.compile(panels.BUTTON_TEMPLATE)
    select = re.compile(panels.SELECT_TEMPLATE)
    match = button.fullmatch(panels.button_id(12, 34))
    assert match is not None
    assert (match["panel"], match["item"]) == ("12", "34")
    assert select.fullmatch(panels.select_id(9)) is not None
    for bad in ("gg:role:1", "gg:role:a:2", "gg:role:1:2:3", "x:gg:role:1:2", "gg:rolesel:1:2"):
        assert button.fullmatch(bad) is None, bad
    assert select.fullmatch("gg:rolesel:x") is None
    assert button.fullmatch(panels.select_id(9)) is None  # nicht verwechselbar


async def test_komponenten_werden_aus_der_custom_id_wiederhergestellt(bot: GGBot) -> None:
    interaction = make_interaction(bot, make_guild(), make_member(1, 1))
    item = MagicMock()
    rebuilt = await panels.RoleButton.from_custom_id(
        interaction,
        item,
        re.fullmatch(panels.BUTTON_TEMPLATE, "gg:role:12:34"),  # type: ignore[arg-type]
    )
    assert (rebuilt.panel_id, rebuilt.item_id) == (12, 34)
    assert rebuilt.custom_id == "gg:role:12:34"
    menu = await panels.RoleSelect.from_custom_id(
        interaction,
        item,
        re.fullmatch(panels.SELECT_TEMPLATE, "gg:rolesel:5"),  # type: ignore[arg-type]
    )
    assert menu.panel_id == 5
    assert menu.custom_id == "gg:rolesel:5"


async def test_nach_neustart_gilt_ein_alter_button_noch(bot: GGBot) -> None:
    """Ein frisch gestarteter Bot kennt die Nachricht nicht, nur die custom_id."""
    await bot.add_cog(RolePanelsCog(bot))
    guild = world(role(1), role(2), role(3))
    member = user_with(guild)
    panel = await new_panel(bot)
    patterns = {p.pattern for p in bot._connection._view_store._dynamic_items}
    assert panels.BUTTON_TEMPLATE in patterns
    assert panels.SELECT_TEMPLATE in patterns

    item = panel.items[1]
    interaction = make_interaction(bot, guild, member)
    match = re.fullmatch(panels.BUTTON_TEMPLATE, panels.button_id(panel.id, item.id))
    assert match is not None
    restored = await panels.RoleButton.from_custom_id(interaction, MagicMock(), match)
    await restored.callback(interaction)
    assert role_ids(member) == {item.role_id}


async def test_auswahl_callback_liest_die_werte_aus_der_interaktion(bot: GGBot) -> None:
    guild = world(role(1), role(2), role(3))
    member = user_with(guild)
    panel = await new_panel(bot, "select")
    interaction = make_interaction(bot, guild, member)
    interaction.data = {"values": ["1", "3"], "custom_id": panels.select_id(panel.id)}
    await panels.RoleSelect(panel.id).callback(interaction)
    assert role_ids(member) == {1, 3}


async def test_build_view_button(bot: GGBot) -> None:
    panel = await new_panel(bot, "button", "normal", 1, 2, 3)
    view = panels.build_view(panel, "de")
    assert view is not None
    assert view.timeout is None  # persistent
    ids = [child.custom_id for child in view.children]  # type: ignore[attr-defined]
    assert ids == [panels.button_id(panel.id, i.id) for i in panel.items]
    assert all(len(i) <= 100 for i in ids)


async def test_build_view_auswahl(bot: GGBot) -> None:
    normal = await new_panel(bot, "select", "normal", 1, 2, 3)
    unique = await new_panel(bot, "select", "unique", 1, 2, 3)
    select = panels.build_view(normal, "en").children[0].item  # type: ignore[union-attr]
    assert [o.value for o in select.options] == ["1", "2", "3"]
    assert (select.min_values, select.max_values) == (0, 3)
    assert select.custom_id == panels.select_id(normal.id)
    single = panels.build_view(unique, "en").children[0].item  # type: ignore[union-attr]
    assert single.max_values == 1


async def test_reaktions_panel_hat_keine_komponenten(bot: GGBot) -> None:
    panel = await new_panel(bot, "reaction", "normal", 1)
    assert panels.build_view(panel, "de") is None


async def test_embed_zeigt_rollen_ohne_zu_pingen(bot: GGBot) -> None:
    panel = await new_panel(bot, "button", "unique", 1, 2)
    embed = panels.build_embed(panel, "de")
    assert embed.title == "Rollen"
    assert embed.description is not None
    assert "<@&1>" in embed.description
    assert "Rolle 2" in embed.description
    assert "nur eine" in embed.description  # Hinweis auf den Modus
    assert "nur eine" not in panels.build_embed(panel, "en").description  # type: ignore[operator]


async def test_embed_passt_immer_in_discords_grenze(bot: GGBot) -> None:
    service = bot.services.rolepanels
    panel = await service.create(
        GUILD_ID, kind="button", mode="normal", title="t", description="d" * 1000
    )
    for r in range(25):
        panel = await service.add_item(GUILD_ID, panel.id, role_id=10**17 + r, label="L" * 80)
    embed = panels.build_embed(panel, "de")
    assert embed.description is not None
    assert len(embed.description) <= 4096


# --- Umsetzung und Fehler ---


async def test_apply_change_leerer_plan_tut_nichts() -> None:
    guild = world()
    member = user_with(guild)
    applied = await panels.apply_change(guild, member, Change(), "x")
    assert applied == panels.Applied()
    member.add_roles.assert_not_awaited()
    member.remove_roles.assert_not_awaited()
    assert "Nichts" in panels.reply_text("de", applied)


async def test_antworttext_nennt_alles() -> None:
    text = panels.reply_text("en", panels.Applied((1,), (2,), (3,)))
    assert "<@&1>" in text and "<@&2>" in text and "<@&3>" in text  # noqa: PT018


async def test_komponentenfehler_sind_freundlich(bot: GGBot) -> None:
    interaction = make_interaction(bot, make_guild(), make_member(1, 1))
    await panels.component_error(interaction, UserError("error.panel_item_gone"))
    assert "veraltet" in sent_text(interaction.response.send_message)

    interaction = make_interaction(bot, make_guild(), make_member(1, 1))
    await panels.component_error(interaction, RuntimeError("geheim: Passwort123"))
    text = sent_text(interaction.response.send_message)
    assert "Passwort123" not in text
    assert re.search(r"[0-9a-f]{8}", text)
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True
