"""Rollen-Panels verwalten. Ohne Discord: die Cogs setzen Nachrichten und Rollen um."""

import time
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from ggbot.core.db.engine import Database
from ggbot.core.db.models import RolePanel, RolePanelItem
from ggbot.core.db.repos import rolepanels as repo
from ggbot.core.errors import UserError
from ggbot.core.roles.plan import emoji_key

PanelKind = Literal["button", "select", "reaction"]
MAX_PANELS = 25
MAX_ITEMS: dict[str, int] = {"button": 25, "select": 25, "reaction": 20}  # Grenzen von Discord
MAX_TITLE = 256
MAX_DESCRIPTION = 1000
MAX_LABEL = 80
MAX_ITEM_DESCRIPTION = 100
CACHE_TTL_SECONDS = 60.0


@dataclass(frozen=True)
class ItemView:
    id: int
    role_id: int
    label: str | None
    emoji: str | None
    emoji_key: str | None
    description: str | None
    position: int

    @classmethod
    def from_model(cls, item: RolePanelItem) -> "ItemView":
        return cls(
            item.id,
            item.role_id,
            item.label,
            item.emoji,
            item.emoji_key,
            item.description,
            item.position,
        )


@dataclass(frozen=True)
class PanelView:
    id: int
    guild_id: int
    channel_id: int | None
    message_id: int | None
    kind: str
    mode: str
    title: str
    description: str | None
    external: bool
    items: tuple[ItemView, ...]

    @property
    def role_ids(self) -> tuple[int, ...]:
        return tuple(item.role_id for item in self.items)

    @property
    def published(self) -> bool:
        return self.message_id is not None

    def item_by_role(self, role_id: int) -> ItemView | None:
        return next((i for i in self.items if i.role_id == role_id), None)

    def item_by_id(self, item_id: int) -> ItemView | None:
        return next((i for i in self.items if i.id == item_id), None)

    def item_by_emoji(self, key: str) -> ItemView | None:
        return next((i for i in self.items if i.emoji_key == key), None)

    @classmethod
    def from_models(cls, panel: RolePanel, items: list[RolePanelItem]) -> "PanelView":
        return cls(
            panel.id,
            panel.guild_id,
            panel.channel_id,
            panel.message_id,
            panel.kind,
            panel.mode,
            panel.title,
            panel.description,
            panel.external,
            tuple(ItemView.from_model(i) for i in items),
        )


class RolePanelService:
    def __init__(self, db: Database) -> None:
        self._db = db
        # Reaktionen treffen auf jede Nachricht des Servers ein: ein Blick in die DB pro Reaktion
        # wäre zu teuer. Der Cache hält pro Server nur "Nachricht → Panel" der Reaktions-Panels.
        self._messages: dict[int, tuple[float, dict[int, int]]] = {}

    def invalidate(self, guild_id: int) -> None:
        self._messages.pop(guild_id, None)

    # --- Lesen ---

    async def _view(self, session: AsyncSession, panel: RolePanel) -> PanelView:
        return PanelView.from_models(panel, await repo.items(session, panel.id))

    async def get(self, guild_id: int, panel_id: int) -> PanelView:
        async with self._db.session() as session:
            panel = await repo.get_panel(session, guild_id, panel_id)
            if panel is None:
                raise UserError("error.panel_not_found", panel_id=panel_id)
            return await self._view(session, panel)

    async def list(self, guild_id: int) -> list[PanelView]:
        async with self._db.session() as session:
            return [await self._view(session, p) for p in await repo.list_panels(session, guild_id)]

    async def panel_for_message(self, guild_id: int, message_id: int) -> PanelView | None:
        """Das Reaktions-Panel dieser Nachricht, sonst None (über den Cache, ohne DB-Zugriff)."""
        now = time.monotonic()
        cached = self._messages.get(guild_id)
        if cached is None or cached[0] <= now:
            async with self._db.session() as session:
                table = await repo.reaction_messages(session, guild_id)
            self._messages[guild_id] = (now + CACHE_TTL_SECONDS, table)
            cached = self._messages[guild_id]
        panel_id = cached[1].get(message_id)
        if panel_id is None:
            return None
        return await self.get(guild_id, panel_id)

    # --- Schreiben ---

    async def create(
        self,
        guild_id: int,
        *,
        kind: PanelKind,
        mode: str,
        title: str,
        description: str | None = None,
        channel_id: int | None = None,
        message_id: int | None = None,
    ) -> PanelView:
        title = title.strip()
        if not title or len(title) > MAX_TITLE:
            raise UserError("error.panel_title", limit=MAX_TITLE)
        description = description.strip() if description else None
        if description and len(description) > MAX_DESCRIPTION:
            raise UserError("error.panel_description", limit=MAX_DESCRIPTION)
        external = message_id is not None
        if external and kind != "reaction":
            raise UserError("error.panel_external_only_reactions")
        async with self._db.session() as session:
            if await repo.count_panels(session, guild_id) >= MAX_PANELS:
                raise UserError("error.panel_limit", limit=MAX_PANELS)
            panel = await repo.create_panel(
                session,
                guild_id=guild_id,
                kind=kind,
                mode=mode,
                title=title,
                description=description or None,
                channel_id=channel_id,
                message_id=message_id,
                external=external,
            )
            view = PanelView.from_models(panel, [])
        self.invalidate(guild_id)
        return view

    async def add_item(
        self,
        guild_id: int,
        panel_id: int,
        *,
        role_id: int,
        label: str | None = None,
        emoji: str | None = None,
        description: str | None = None,
    ) -> PanelView:
        label = label.strip() if label else None
        emoji = emoji.strip() if emoji else None
        description = description.strip() if description else None
        if label and len(label) > MAX_LABEL:
            raise UserError("error.panel_label", limit=MAX_LABEL)
        if description and len(description) > MAX_ITEM_DESCRIPTION:
            raise UserError("error.panel_item_description", limit=MAX_ITEM_DESCRIPTION)
        async with self._db.session() as session:
            panel = await repo.get_panel(session, guild_id, panel_id)
            if panel is None:
                raise UserError("error.panel_not_found", panel_id=panel_id)
            existing = await repo.items(session, panel.id)
            if panel.kind == "reaction" and not emoji:
                raise UserError("error.panel_emoji_needed")
            if panel.kind != "reaction" and not (label or emoji):
                raise UserError("error.panel_label_needed")
            if len(existing) >= MAX_ITEMS[panel.kind]:
                raise UserError("error.panel_items_limit", limit=MAX_ITEMS[panel.kind])
            if any(item.role_id == role_id for item in existing):
                raise UserError("error.panel_role_duplicate")
            key = emoji_key(emoji) if emoji else None
            if key is not None and any(item.emoji_key == key for item in existing):
                raise UserError("error.panel_emoji_duplicate")
            await repo.add_item(
                session,
                panel_id=panel.id,
                guild_id=guild_id,
                role_id=role_id,
                label=label,
                emoji=emoji,
                emoji_key=key,
                description=description if panel.kind == "select" else None,
                position=max((item.position for item in existing), default=-1) + 1,
            )
            view = await self._view(session, panel)
        self.invalidate(guild_id)
        return view

    async def remove_item(
        self, guild_id: int, panel_id: int, role_id: int
    ) -> tuple[PanelView, ItemView]:
        """Das Panel nach dem Entfernen und der entfernte Eintrag (für die Aufräumarbeit)."""
        async with self._db.session() as session:
            panel = await repo.get_panel(session, guild_id, panel_id)
            if panel is None:
                raise UserError("error.panel_not_found", panel_id=panel_id)
            before = PanelView.from_models(panel, await repo.items(session, panel.id))
            removed = before.item_by_role(role_id)
            if removed is None:
                raise UserError("error.panel_role_not_in_panel")
            await repo.remove_item(session, panel.id, role_id)
            after = await self._view(session, panel)
        self.invalidate(guild_id)
        return after, removed

    async def delete(self, guild_id: int, panel_id: int) -> PanelView:
        async with self._db.session() as session:
            panel = await repo.get_panel(session, guild_id, panel_id)
            if panel is None:
                raise UserError("error.panel_not_found", panel_id=panel_id)
            view = await self._view(session, panel)
            await repo.delete_panel(session, guild_id, panel_id)
        self.invalidate(guild_id)
        return view

    async def set_message(
        self, guild_id: int, panel_id: int, channel_id: int | None, message_id: int | None
    ) -> None:
        async with self._db.session() as session:
            if await repo.get_panel(session, guild_id, panel_id) is None:
                raise UserError("error.panel_not_found", panel_id=panel_id)
            await repo.set_message(session, guild_id, panel_id, channel_id, message_id)
        self.invalidate(guild_id)

    async def message_deleted(self, guild_id: int, message_id: int) -> int:
        """Eine Panel-Nachricht wurde gelöscht: das Panel gilt wieder als unveröffentlicht."""
        async with self._db.session() as session:
            count = await repo.clear_message(session, guild_id, message_id)
        if count:
            self.invalidate(guild_id)
        return count
