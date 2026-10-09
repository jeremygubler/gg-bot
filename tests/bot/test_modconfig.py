from unittest.mock import MagicMock

import pytest
from discord import app_commands

from ggbot.bot.client import GGBot
from ggbot.bot.cogs.modconfig import ModConfigCog
from ggbot.core.duration import DAY, HOUR
from ggbot.core.errors import UserError
from ggbot.core.settings.schema import ModerationSettings
from tests.bot.test_cog_flows import (
    GUILD_ID,
    bot,  # noqa: F401  Fixture
    make_guild,
    make_interaction,
    make_member,
    sent_text,
)
from tests.fakes import invoke


def choice(value: str) -> app_commands.Choice[str]:
    return app_commands.Choice(name=value, value=value)


async def setup_cog(bot: GGBot) -> tuple[ModConfigCog, MagicMock]:  # noqa: F811
    cog = ModConfigCog(bot)
    interaction = make_interaction(bot, make_guild(), make_member(1, 5))
    return cog, interaction


async def load(bot: GGBot) -> ModerationSettings:  # noqa: F811
    return await bot.services.settings.get(GUILD_ID, ModerationSettings)


async def test_regeln_hinzufuegen_ersetzen_auflisten_entfernen(bot: GGBot) -> None:  # noqa: F811
    cog, interaction = await setup_cog(bot)
    await invoke(cog.escalation_add, cog, interaction, 5, choice("kick"), None)
    await invoke(cog.escalation_add, cog, interaction, 3, choice("timeout"), "1h")
    rules = (await load(bot)).escalation
    assert [(r.warns, r.action, r.duration_seconds) for r in rules] == [
        (3, "timeout", HOUR),
        (5, "kick", None),
    ]

    # Gleiche Anzahl ersetzt die Regel.
    await invoke(cog.escalation_add, cog, interaction, 5, choice("tempban"), "2d")
    rules = (await load(bot)).escalation
    assert [(r.warns, r.action, r.duration_seconds) for r in rules][1] == (5, "tempban", 2 * DAY)

    await invoke(cog.escalation_list, cog, interaction)
    embed = interaction.response.send_message.await_args.kwargs["embed"]
    assert "3" in embed.description
    assert "tempban" in embed.description

    await invoke(cog.escalation_remove, cog, interaction, 3)
    assert [r.warns for r in (await load(bot)).escalation] == [5]


@pytest.mark.parametrize(
    ("action", "duration", "key"),
    [
        ("timeout", None, "error.escalation_duration_needed"),
        ("tempban", None, "error.escalation_duration_needed"),
        ("kick", "1h", "error.escalation_duration_unexpected"),
        ("ban", "1h", "error.escalation_duration_unexpected"),
        ("timeout", "kaputt", "error.invalid_duration"),
        ("timeout", "30d", "error.escalation_invalid"),  # über dem Discord-Limit von 28 Tagen
        ("tempban", "10s", "error.escalation_invalid"),  # unter einer Minute
    ],
)
async def test_ungueltige_regeln_werden_nicht_gespeichert(
    bot: GGBot,  # noqa: F811
    action: str,
    duration: str | None,
    key: str,
) -> None:
    cog, interaction = await setup_cog(bot)
    with pytest.raises(UserError) as exc:
        await invoke(cog.escalation_add, cog, interaction, 3, choice(action), duration)
    assert exc.value.key == key
    assert (await load(bot)).escalation == []


async def test_leere_liste_und_unbekannte_regel(bot: GGBot) -> None:  # noqa: F811
    cog, interaction = await setup_cog(bot)
    await invoke(cog.escalation_list, cog, interaction)
    assert "Keine Eskalationsregeln" in sent_text(interaction.response.send_message)
    with pytest.raises(UserError) as exc:
        await invoke(cog.escalation_remove, cog, interaction, 9)
    assert exc.value.key == "error.escalation_not_found"


async def test_warn_expiry_und_dm_schalter(bot: GGBot) -> None:  # noqa: F811
    cog, interaction = await setup_cog(bot)
    await invoke(cog.warn_expiry, cog, interaction, 30)
    assert (await load(bot)).warn_expiry_days == 30
    await invoke(cog.warn_expiry, cog, interaction, 0)  # 0 = für immer
    assert (await load(bot)).warn_expiry_days is None

    await invoke(cog.dm, cog, interaction, False)
    assert (await load(bot)).dm_on_action is False
    await invoke(cog.dm, cog, interaction, True)
    assert (await load(bot)).dm_on_action is True


async def test_einstellungen_aendern_laesst_andere_felder_unberuehrt(bot: GGBot) -> None:  # noqa: F811
    cog, interaction = await setup_cog(bot)
    await invoke(cog.escalation_add, cog, interaction, 3, choice("kick"), None)
    await invoke(cog.warn_expiry, cog, interaction, 10)
    await invoke(cog.dm, cog, interaction, False)
    settings = await load(bot)
    assert [r.warns for r in settings.escalation] == [3]
    assert settings.warn_expiry_days == 10
    assert settings.dm_on_action is False
