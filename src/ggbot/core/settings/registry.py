"""Alle Einstellungsmodule an einer Stelle.

Das Dashboard bietet genau diese Module an, mit dem JSON-Schema der Modelle als
Formularvorlage. Ein neues Modul gehört hier eingetragen; ein Test stellt sicher,
dass kein `SettingsModel` fehlt.
"""

from ggbot.core.settings.autodelete import AutodeleteSettings
from ggbot.core.settings.automod import AutomodSettings
from ggbot.core.settings.birthday import BirthdaySettings
from ggbot.core.settings.eventlog import LoggingSettings
from ggbot.core.settings.leveling import LevelingSettings
from ggbot.core.settings.schema import GeneralSettings, ModerationSettings, SettingsModel
from ggbot.core.settings.starboard import StarboardSettings
from ggbot.core.settings.tickets import TicketSettings
from ggbot.core.settings.verification import VerificationSettings
from ggbot.core.settings.welcome import WelcomeSettings

MODELS: dict[str, type[SettingsModel]] = {
    model.module: model
    for model in (
        GeneralSettings,
        ModerationSettings,
        AutomodSettings,
        LoggingSettings,
        LevelingSettings,
        WelcomeSettings,
        TicketSettings,
        BirthdaySettings,
        StarboardSettings,
        VerificationSettings,
        AutodeleteSettings,
    )
}
