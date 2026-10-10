"""Regeln rund um XP: Multiplikatoren, Belohnungsrollen, Voice-Berechtigung."""

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Literal


def excluded(
    *,
    channel_ids: Collection[int | None],
    role_ids: Collection[int],
    no_xp_channels: Collection[int],
    no_xp_roles: Collection[int],
) -> bool:
    """Kein XP in diesem Kanal (auch über den Elternkanal eines Threads) oder mit dieser Rolle."""
    if {c for c in channel_ids if c is not None} & set(no_xp_channels):
        return True
    return bool(set(role_ids) & set(no_xp_roles))


def effective_multiplier(
    *,
    base: float,
    role_ids: Collection[int],
    channel_ids: Collection[int | None],
    role_multipliers: Mapping[int, float],
    channel_multipliers: Mapping[int, float],
) -> float:
    """Globaler Faktor × bester Rollenfaktor × bester Kanalfaktor.

    Mehrere Rollen *stapeln nicht*: es gilt die höchste. Sonst bekäme jemand mit fünf kleinen
    Boosts plötzlich das Mehrfache, ohne dass es jemand so eingestellt hätte.
    """
    role_factor = max((role_multipliers[r] for r in role_ids if r in role_multipliers), default=1.0)
    channel_factor = max(
        (channel_multipliers[c] for c in channel_ids if c is not None and c in channel_multipliers),
        default=1.0,
    )
    return base * role_factor * channel_factor


def scaled(amount: int, factor: float) -> int:
    """Skalierte XP, auf ganze Zahlen gerundet, nie unter 0."""
    return max(0, round(amount * factor))


RewardMode = Literal["stack", "replace"]


@dataclass(frozen=True)
class RolePlan:
    add: frozenset[int]
    remove: frozenset[int]


def reward_plan(
    rewards: Mapping[int, int],
    mode: RewardMode,
    level: int,
    current_roles: Collection[int],
) -> RolePlan:
    """Welche Belohnungsrollen hinzukommen und welche wegfallen. `rewards`: Level → Rolle.

    stack: alle Rollen bis zum aktuellen Level. replace: nur die höchste erreichte, die anderen
    Belohnungsrollen werden entfernt. Rollen, die keine Belohnung sind, fasst der Plan nie an.
    """
    current = set(current_roles)
    reward_roles = set(rewards.values())
    reached = {lvl: role for lvl, role in rewards.items() if lvl <= level}
    if mode == "stack":
        wanted = set(reached.values())
    else:
        wanted = {reached[max(reached)]} if reached else set()
    # Was erwünscht ist, wird nie erst entfernt und dann wieder hinzugefügt.
    return RolePlan(
        add=frozenset(wanted - current),
        remove=frozenset((reward_roles - wanted) & current),
    )


@dataclass(frozen=True)
class VoiceMember:
    user_id: int
    is_bot: bool
    muted: bool  # selbst oder vom Server stumm/taub geschaltet


def voice_eligible(members: Collection[VoiceMember], *, requires_company: bool) -> set[int]:
    """Wer in diesem Kanal diese Minute XP bekommt.

    Bots bekommen nie XP und zählen nicht als Gesellschaft. Wer stumm oder taub ist, bekommt
    keines (sonst liesse sich XP im Hintergrund farmen). Allein im Kanal gibt es nichts,
    wenn `requires_company` gilt.
    """
    humans = [m for m in members if not m.is_bot]
    if requires_company and len(humans) < 2:
        return set()
    return {m.user_id for m in humans if not m.muted}
