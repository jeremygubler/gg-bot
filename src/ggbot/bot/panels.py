"""Rollen-Panels auf Discord: Nachricht bauen, Klicks und Auswahlen entgegennehmen.

Die Entscheidung, welche Rollen sich ändern, liegt in `ggbot.core.roles.plan`; hier steht nur,
was Discord dazu wissen muss (Hierarchie, Komponenten, Fehlermeldungen).
"""

import re
import secrets
from dataclasses import dataclass
from typing import Any

import discord

from ggbot.bot.errors import respond_ephemeral
from ggbot.bot.rewards import assignable
from ggbot.core.errors import UserError
from ggbot.core.i18n import translator
from ggbot.core.logging import get_logger
from ggbot.core.roles.plan import Change, PanelMode, plan_select, plan_toggle
from ggbot.core.services.rolepanels import ItemView, PanelView

log = get_logger(__name__)

BUTTON_COOLDOWN_SECONDS = 2
EMBED_LIMIT = 4000
BUTTON_TEMPLATE = r"gg:role:(?P<panel>\d+):(?P<item>\d+)"
SELECT_TEMPLATE = r"gg:rolesel:(?P<panel>\d+)"


def button_id(panel_id: int, item_id: int) -> str:
    return f"gg:role:{panel_id}:{item_id}"


def select_id(panel_id: int) -> str:
    return f"gg:rolesel:{panel_id}"


@dataclass(frozen=True)
class Applied:
    added: tuple[int, ...] = ()
    removed: tuple[int, ...] = ()
    # Rollen, die der Bot nicht vergeben darf (gelöscht, verwaltet oder über seiner eigenen Rolle).
    skipped: tuple[int, ...] = ()


async def apply_change(
    guild: discord.Guild, member: discord.Member, change: Change, reason: str
) -> Applied:
    """Setzt einen Plan um. Was der Bot nicht vergeben darf, wird gemeldet und nicht versucht."""
    add: list[discord.Role] = []
    remove: list[discord.Role] = []
    skipped: list[int] = []
    for role_id in sorted(change.add):
        role = guild.get_role(role_id)
        if role is not None and assignable(guild, role):
            add.append(role)
        else:
            skipped.append(role_id)
    for role_id in sorted(change.remove):
        role = guild.get_role(role_id)
        if role is not None and assignable(guild, role):
            remove.append(role)
        # Eine gelöschte Rolle ist beim Mitglied ohnehin weg.
    try:
        # Erst entfernen, dann hinzufügen: bei "unique" ist so nie kurz mehr als eine Rolle da.
        if remove:
            await member.remove_roles(*remove, reason=reason)
        if add:
            await member.add_roles(*add, reason=reason)
    except discord.Forbidden as exc:
        log.warning("rolepanel_forbidden", guild_id=guild.id, user_id=member.id)
        raise UserError("error.panel_forbidden") from exc
    if skipped:
        log.warning("rolepanel_unassignable", guild_id=guild.id, roles=skipped)
    return Applied(tuple(r.id for r in add), tuple(r.id for r in remove), tuple(skipped))


def reply_text(locale: str, applied: Applied) -> str:
    t = translator.t
    if not (applied.added or applied.removed or applied.skipped):
        return t(locale, "roles.unchanged")
    lines: list[str] = []
    if applied.added:
        lines.append(t(locale, "roles.added", roles=_mentions(applied.added)))
    if applied.removed:
        lines.append(t(locale, "roles.removed", roles=_mentions(applied.removed)))
    if applied.skipped:
        lines.append(t(locale, "roles.skipped", roles=_mentions(applied.skipped)))
    return "\n".join(lines)


def _mentions(role_ids: tuple[int, ...]) -> str:
    return ", ".join(f"<@&{role_id}>" for role_id in role_ids)


# --- Nachricht ---


def _item_line(item: ItemView) -> str:
    left = f"{item.emoji} " if item.emoji else ""
    name = f"{item.label} · " if item.label else ""
    return f"{left}{name}<@&{item.role_id}>"


def build_embed(panel: PanelView, locale: str) -> discord.Embed:
    parts: list[str] = []
    if panel.description:
        parts.append(panel.description)
    parts.append("\n".join(_item_line(item) for item in panel.items))
    parts.append(f"*{translator.t(locale, f'roles.mode_hint_{panel.mode}')}*")
    embed = discord.Embed(title=panel.title, description="\n\n".join(p for p in parts if p))
    embed.description = (embed.description or "")[:EMBED_LIMIT]
    return embed


def _partial(emoji: str | None) -> discord.PartialEmoji | None:
    return discord.PartialEmoji.from_str(emoji) if emoji else None


class RoleButton(discord.ui.DynamicItem[discord.ui.Button[Any]], template=BUTTON_TEMPLATE):
    def __init__(self, panel_id: int, item_id: int, *, label: str | None = None,
                 emoji: str | None = None) -> None:  # fmt: skip
        super().__init__(
            discord.ui.Button(
                style=discord.ButtonStyle.secondary,
                label=label,
                emoji=_partial(emoji),
                custom_id=button_id(panel_id, item_id),
            )
        )
        self.panel_id = panel_id
        self.item_id = item_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str], /
    ) -> "RoleButton":
        return cls(int(match["panel"]), int(match["item"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        await handle_button(interaction, self.panel_id, self.item_id)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await component_error(interaction, error)


class RoleSelect(discord.ui.DynamicItem[discord.ui.Select[Any]], template=SELECT_TEMPLATE):
    def __init__(self, panel_id: int, *, options: list[discord.SelectOption] | None = None,
                 max_values: int = 1, placeholder: str | None = None) -> None:  # fmt: skip
        super().__init__(
            discord.ui.Select(
                custom_id=select_id(panel_id),
                options=options or [],
                min_values=0,
                max_values=max(max_values, 1),
                placeholder=placeholder,
            )
        )
        self.panel_id = panel_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: re.Match[str], /
    ) -> "RoleSelect":
        return cls(int(match["panel"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        data: Any = interaction.data or {}
        values = [int(v) for v in data.get("values", [])]
        await handle_select(interaction, self.panel_id, values)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await component_error(interaction, error)


def build_view(panel: PanelView, locale: str) -> discord.ui.View | None:
    """Die Komponenten des Panels; Reaktions-Panels haben keine."""
    if panel.kind == "reaction":
        return None
    view = discord.ui.View(timeout=None)
    if panel.kind == "button":
        for item in panel.items:
            view.add_item(RoleButton(panel.id, item.id, label=item.label, emoji=item.emoji))
    else:
        options = [
            discord.SelectOption(
                label=item.label or str(item.role_id),
                value=str(item.role_id),
                emoji=_partial(item.emoji),
                description=item.description,
            )
            for item in panel.items
        ]
        view.add_item(
            RoleSelect(
                panel.id,
                options=options,
                max_values=1 if panel.mode == "unique" else len(options),
                placeholder=translator.t(locale, "roles.select_placeholder"),
            )
        )
    return view


# --- Klicks ---


async def _locale(interaction: discord.Interaction) -> str:
    bot: Any = interaction.client
    return str(await bot.services.guilds.get_locale(interaction.guild_id))


async def _context(
    interaction: discord.Interaction, panel_id: int
) -> tuple[Any, discord.Guild, discord.Member, PanelView, str]:
    bot: Any = interaction.client
    guild = interaction.guild
    member = interaction.user
    if guild is None or not isinstance(member, discord.Member):
        raise UserError("error.guild_only")
    # Zählt der Klick zu einem Panel dieses Servers? Eine fremde Panel-ID gibt es hier nicht.
    panel = await bot.services.rolepanels.get(guild.id, panel_id)
    await bot.services.cooldown.ensure(
        "rolepanel", f"{guild.id}:{member.id}", seconds=BUTTON_COOLDOWN_SECONDS
    )
    return bot, guild, member, panel, await _locale(interaction)


async def handle_button(interaction: discord.Interaction, panel_id: int, item_id: int) -> None:
    _, guild, member, panel, locale = await _context(interaction, panel_id)
    item = panel.item_by_id(item_id)
    if item is None:
        raise UserError("error.panel_item_gone")  # der Button ist von einer alten Version
    mode: PanelMode = panel.mode  # type: ignore[assignment]
    change = plan_toggle(mode, item.role_id, panel.role_ids, {r.id for r in member.roles})
    applied = await apply_change(guild, member, change, f"Rollen-Panel {panel.id}")
    await interaction.response.send_message(reply_text(locale, applied), ephemeral=True)


async def handle_select(interaction: discord.Interaction, panel_id: int, values: list[int]) -> None:
    _, guild, member, panel, locale = await _context(interaction, panel_id)
    mode: PanelMode = panel.mode  # type: ignore[assignment]
    try:
        change = plan_select(mode, values, panel.role_ids, {r.id for r in member.roles})
    except ValueError as exc:
        raise UserError("error.panel_unique_many") from exc
    applied = await apply_change(guild, member, change, f"Rollen-Panel {panel.id}")
    await interaction.response.send_message(reply_text(locale, applied), ephemeral=True)


async def component_error(interaction: discord.Interaction, error: Exception) -> None:
    """Komponenten laufen nicht durch den Command-Tree: gleiche freundliche Antwort hier."""
    bot: Any = interaction.client
    try:
        locale = await bot.services.guilds.get_locale(interaction.guild_id)
    except Exception:
        log.exception("locale_lookup_failed")
        locale = bot.services.config.default_locale
    if isinstance(error, UserError):
        text = translator.t(locale, error.key, **error.params)
    else:
        error_id = secrets.token_hex(4)
        log.error("component_error", error_id=error_id, exc_info=error)
        text = translator.t(locale, "error.generic", error_id=error_id)
    await respond_ephemeral(interaction, text)
