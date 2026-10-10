"""Belohnungsrollen vergeben und entziehen."""

from dataclasses import dataclass

import discord

from ggbot.core.leveling.rules import reward_plan
from ggbot.core.logging import get_logger
from ggbot.core.settings.leveling import LevelingSettings

log = get_logger(__name__)


@dataclass(frozen=True)
class RoleChange:
    added: tuple[int, ...] = ()
    removed: tuple[int, ...] = ()
    # Rollen, die der Bot nicht vergeben darf (gelöscht, verwaltet oder über seiner eigenen Rolle).
    skipped: tuple[int, ...] = ()
    forbidden: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.added or self.removed)


def assignable(guild: discord.Guild, role: discord.Role | None) -> bool:
    """Darf der Bot diese Rolle vergeben? Discord erlaubt nur Rollen unter der eigenen höchsten."""
    if role is None or role.managed or role.is_default():
        return False
    return role.position < guild.me.top_role.position


async def apply_rewards(
    guild: discord.Guild, member: discord.Member, level: int, settings: LevelingSettings
) -> RoleChange:
    """Bringt die Belohnungsrollen des Mitglieds auf den Stand für `level`."""
    if not settings.rewards:
        return RoleChange()
    plan = reward_plan(
        settings.rewards, settings.reward_mode, level, {role.id for role in member.roles}
    )
    add: list[discord.Role] = []
    remove: list[discord.Role] = []
    skipped: list[int] = []
    for role_id in sorted(plan.add):
        role = guild.get_role(role_id)
        if assignable(guild, role) and role is not None:
            add.append(role)
        else:
            skipped.append(role_id)
    for role_id in sorted(plan.remove):
        role = guild.get_role(role_id)
        if assignable(guild, role) and role is not None:
            remove.append(role)
        # Eine gelöschte oder unerreichbare Rolle zu entfernen ist nichts, was man melden müsste.

    try:
        if add:
            await member.add_roles(*add, reason="Leveling")
        if remove:
            await member.remove_roles(*remove, reason="Leveling")
    except discord.Forbidden:
        log.warning("leveling_roles_forbidden", guild_id=guild.id, user_id=member.id)
        return RoleChange(skipped=tuple(skipped), forbidden=True)
    if skipped:
        log.warning("leveling_roles_unassignable", guild_id=guild.id, roles=skipped)
    return RoleChange(
        tuple(r.id for r in add), tuple(r.id for r in remove), tuple(skipped), forbidden=False
    )
