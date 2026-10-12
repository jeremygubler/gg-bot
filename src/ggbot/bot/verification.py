"""Verifizierung auf Discord: Panel-Knopf, Bild-Aufgabe, Eingabefenster.

Die Entscheidungen (Code, Versuche, Sperre) liegen in `ggbot.core.services.verification`;
hier steht, was Discord dazu wissen muss (Rolle vergeben, Bild senden, Fenster zeigen).
"""

import asyncio
import io
import secrets
from typing import Any

import discord

from ggbot.bot.panels import component_error
from ggbot.bot.rewards import assignable
from ggbot.core.errors import UserError
from ggbot.core.i18n import translator
from ggbot.core.logging import get_logger
from ggbot.core.services.verification import Outcome
from ggbot.core.settings.verification import VerificationSettings
from ggbot.core.verification import rules
from ggbot.render.verify_code import render_code

log = get_logger(__name__)

START_ID = "gg:verify:start"
ENTER_ID = "gg:verify:enter"
START_TEMPLATE = r"gg:verify:start"
ENTER_TEMPLATE = r"gg:verify:enter"
DEFAULT_TITLE_KEY = "verify.default_title"
DEFAULT_MESSAGE_KEY = "verify.default_message"


def _t(locale: str, key: str, **params: object) -> str:
    return translator.t(locale, key, **params)


async def _locale(interaction: discord.Interaction) -> str:
    bot: Any = interaction.client
    return str(await bot.services.guilds.get_locale(interaction.guild_id))


def panel_embed(settings: VerificationSettings, locale: str) -> discord.Embed:
    return discord.Embed(
        title=settings.title or _t(locale, DEFAULT_TITLE_KEY),
        description=settings.message or _t(locale, DEFAULT_MESSAGE_KEY),
    )


def panel_view(locale: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(StartButton(label=_t(locale, "verify.button")))
    return view


async def grant(interaction: discord.Interaction, settings: VerificationSettings) -> str:
    """Gibt dem Mitglied die Rolle. Liefert den Text für die Antwort."""
    guild, member = interaction.guild, interaction.user
    locale = await _locale(interaction)
    if guild is None or not isinstance(member, discord.Member):
        raise UserError("error.guild_only")
    role = guild.get_role(settings.role_id or 0)
    if role is None or not assignable(guild, role):
        # Gelöscht oder über der Rolle des Bots: ein Fehler des Servers, nicht des Nutzers.
        log.warning("verify_role_unavailable", guild_id=guild.id, role_id=settings.role_id)
        raise UserError("error.verify_role_unavailable")
    try:
        await member.add_roles(role, reason="Verifizierung")
    except discord.Forbidden as exc:
        log.warning("verify_forbidden", guild_id=guild.id)
        raise UserError("error.verify_role_unavailable") from exc
    return _t(locale, "verify.done")


class StartButton(discord.ui.DynamicItem[discord.ui.Button[Any]], template=START_TEMPLATE):
    def __init__(self, *, label: str = "Verify") -> None:
        super().__init__(
            discord.ui.Button(
                style=discord.ButtonStyle.success, label=label[:80], custom_id=START_ID
            )
        )

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: Any, /
    ) -> "StartButton":
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        bot: Any = interaction.client
        services = bot.services
        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            raise UserError("error.guild_only")
        settings = await services.verification.settings(guild.id)
        if not settings.enabled:
            raise UserError("error.verify_off")
        locale = await _locale(interaction)
        if settings.role_id is not None and any(r.id == settings.role_id for r in member.roles):
            await interaction.response.send_message(_t(locale, "verify.already"), ephemeral=True)
            return
        if settings.mode == "button":
            text = await grant(interaction, settings)
            await interaction.response.send_message(text, ephemeral=True)
            return
        # Ein neues Bild nur alle paar Sekunden, sonst liessen sich Fehlversuche neu würfeln.
        await services.cooldown.ensure(
            "verify", f"{guild.id}:{member.id}", seconds=rules.START_COOLDOWN_SECONDS
        )
        code = rules.new_code(secrets.choice)
        await services.verification.begin(guild.id, member.id, code)
        png = await asyncio.to_thread(render_code, code, secrets.randbits(32))
        view = discord.ui.View(timeout=None)
        view.add_item(EnterButton(label=_t(locale, "verify.enter")))
        await interaction.response.send_message(
            _t(locale, "verify.task", attempts=rules.MAX_ATTEMPTS),
            file=discord.File(io.BytesIO(png), filename="code.png"),
            view=view,
            ephemeral=True,
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await component_error(interaction, error)


class EnterButton(discord.ui.DynamicItem[discord.ui.Button[Any]], template=ENTER_TEMPLATE):
    def __init__(self, *, label: str = "Enter code") -> None:
        super().__init__(
            discord.ui.Button(
                style=discord.ButtonStyle.primary, label=label[:80], custom_id=ENTER_ID
            )
        )

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: Any, /
    ) -> "EnterButton":
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        locale = await _locale(interaction)
        await interaction.response.send_modal(CodeModal(locale))

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await component_error(interaction, error)


class CodeModal(discord.ui.Modal):
    def __init__(self, locale: str) -> None:
        super().__init__(title=_t(locale, "verify.modal_title")[:45], custom_id="gg:verify:modal")
        self.answer: discord.ui.TextInput[Any] = discord.ui.TextInput(
            label=_t(locale, "verify.modal_label")[:45],
            min_length=3,
            max_length=12,
            required=True,
        )
        self.add_item(self.answer)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        bot: Any = interaction.client
        services = bot.services
        guild, member = interaction.guild, interaction.user
        if guild is None or not isinstance(member, discord.Member):
            raise UserError("error.guild_only")
        locale = await _locale(interaction)
        settings = await services.verification.settings(guild.id)
        if not settings.enabled:
            raise UserError("error.verify_off")
        attempt = await services.verification.answer(guild.id, member.id, self.answer.value)
        if attempt.outcome is Outcome.OK:
            text = await grant(interaction, settings)
        elif attempt.outcome is Outcome.WRONG:
            text = _t(locale, "verify.wrong", remaining=attempt.remaining)
        elif attempt.outcome is Outcome.LOCKED:
            text = _t(locale, "verify.locked", minutes=rules.LOCK_SECONDS // 60)
        else:
            text = _t(locale, "verify.expired")
        await interaction.response.send_message(text, ephemeral=True)

    async def on_error(  # type: ignore[override]  # Modal ruft on_error(interaction, error) auf
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await component_error(interaction, error)
