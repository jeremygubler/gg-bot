"""Rollen-Hierarchie als reine Entscheidung ohne Discord-Typen."""

from dataclasses import dataclass

from ggbot.core.errors import UserError


@dataclass(frozen=True)
class Principal:
    """Das, was die Hierarchieprüfung von einem Mitglied wissen muss."""

    user_id: int
    top_role_position: int
    is_owner: bool = False


def ensure_can_target(actor: Principal, target: Principal, bot: Principal) -> None:
    """Wirft `UserError`, wenn `actor` das Ziel nicht sanktionieren darf.

    Gleiche Position zählt als nicht über dem Ziel (Discord vergleicht strikt).
    """
    if actor.user_id == target.user_id:
        raise UserError("error.hierarchy_self")
    if target.is_owner:
        raise UserError("error.hierarchy_owner")
    if not actor.is_owner and actor.top_role_position <= target.top_role_position:
        raise UserError("error.hierarchy_actor")
    if bot.top_role_position <= target.top_role_position:
        raise UserError("error.hierarchy_bot")
