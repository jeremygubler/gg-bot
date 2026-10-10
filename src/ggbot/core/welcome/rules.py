"""Wer wann begrüsst wird. Reine Entscheidung, damit die Sonderfälle testbar bleiben."""

from dataclasses import dataclass

from ggbot.core.settings.welcome import WelcomeSettings


@dataclass(frozen=True)
class JoinPlan:
    message: bool = False
    dm: bool = False
    roles: tuple[int, ...] = ()

    @property
    def nothing(self) -> bool:
        return not (self.message or self.dm or self.roles)


def plan_join(settings: WelcomeSettings, *, is_bot: bool, pending: bool, raid: bool) -> JoinPlan:
    """Was beim Beitritt (oder nach bestandenem Mitglieder-Screening) geschieht.

    - Bots werden nicht begrüsst; sie bekommen nur ihre eigenen Autorollen.
    - Wer das Screening noch nicht bestanden hat (`pending`), bekommt noch nichts: er sieht die
      Kanäle nicht, und eine Rolle würde das Screening umgehen. Der Aufrufer plant erneut, sobald
      `pending` endet.
    - Im Raid-Modus bleibt es still: weder Nachrichten (Spam für die Mitglieder) noch Rollen
      (Rechte für Raider).
    """
    if is_bot:
        return JoinPlan(roles=tuple(settings.bot_autoroles))
    if pending or raid:
        return JoinPlan()
    return JoinPlan(
        message=settings.join_enabled and settings.join_channel_id is not None,
        dm=settings.dm_enabled,
        roles=tuple(settings.autoroles),
    )


def plan_leave(settings: WelcomeSettings, *, is_bot: bool, pending: bool, raid: bool) -> bool:
    """Gibt es eine Abschiedsnachricht? Nicht für Bots, nicht für Wartende (nie begrüsst) und
    nicht im Raid-Modus."""
    if is_bot or pending or raid:
        return False
    return settings.leave_enabled and settings.leave_channel_id is not None
