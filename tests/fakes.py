"""Gemeinsame Test-Doubles."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ggbot.core.errors import UserError

BOT = 999


@dataclass
class FakePort:
    guild_name: str = "Testserver"
    bot_user_id: int = BOT
    dm_ok: bool = True
    banned: bool = True
    fail_on: set[str] = field(default_factory=set)
    unban_fail_after_first: bool = False
    calls: list[tuple[object, ...]] = field(default_factory=list)
    _unbans: int = 0

    def _maybe_fail(self, name: str) -> None:
        if name in self.fail_on:
            raise UserError("error.mod_forbidden")

    async def timeout(self, user_id: int, until: datetime | None, reason: str) -> None:
        self._maybe_fail("timeout")
        self.calls.append(("timeout", user_id, until, reason))

    async def kick(self, user_id: int, reason: str) -> None:
        self._maybe_fail("kick")
        self.calls.append(("kick", user_id, reason))

    async def ban(self, user_id: int, reason: str, delete_seconds: int) -> None:
        self._maybe_fail("ban")
        self.calls.append(("ban", user_id, reason, delete_seconds))

    async def unban(self, user_id: int, reason: str) -> bool:
        self._unbans += 1
        if (self.unban_fail_after_first and self._unbans > 1) or "unban" in self.fail_on:
            raise UserError("error.mod_forbidden")
        self.calls.append(("unban", user_id, reason))
        return self.banned

    async def dm(self, user_id: int, text: str) -> bool:
        self.calls.append(("dm", user_id, text))
        return self.dm_ok

    def names(self) -> list[object]:
        return [c[0] for c in self.calls]


async def invoke(command: Any, *args: Any) -> None:
    """Ruft einen Slash-Command wie Discord auf (mypy kennt `.callback` nicht)."""
    await command.callback(*args)
