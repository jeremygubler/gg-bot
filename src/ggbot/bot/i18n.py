"""Brücke zwischen den TOML-Katalogen und discord.py-Lokalisierung."""

import discord
from discord import app_commands

from ggbot.core.i18n import translator


def d(key: str) -> app_commands.locale_str:
    """Beschreibung eines Befehls: Englisch als Standard, DE über den Translator."""
    return app_commands.locale_str(translator.t("en", key), i18n=key)


def command_key(qualified_name: str) -> str:
    """i18n-Schlüssel der Beschreibung eines Befehls, z. B. "config language"."""
    return "cmd." + qualified_name.replace(" ", ".") + ".desc"


_LOCALES: dict[discord.Locale, str] = {
    discord.Locale.german: "de",
    discord.Locale.american_english: "en",
    discord.Locale.british_english: "en",
}


class CommandTranslator(app_commands.Translator):
    async def translate(
        self,
        string: app_commands.locale_str,
        locale: discord.Locale,
        context: app_commands.TranslationContextTypes,
    ) -> str | None:
        key = string.extras.get("i18n")
        lang = _LOCALES.get(locale)
        if key is None or lang is None:
            return None
        return translator.t(lang, str(key))
