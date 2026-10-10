from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ggbot.core.db.models import RolePanel, RolePanelItem


async def create_panel(
    session: AsyncSession,
    *,
    guild_id: int,
    kind: str,
    mode: str,
    title: str,
    description: str | None,
    channel_id: int | None,
    message_id: int | None,
    external: bool,
) -> RolePanel:
    panel = RolePanel(
        guild_id=guild_id,
        kind=kind,
        mode=mode,
        title=title,
        description=description,
        channel_id=channel_id,
        message_id=message_id,
        external=external,
    )
    session.add(panel)
    await session.flush()
    await session.refresh(panel)
    return panel


async def get_panel(session: AsyncSession, guild_id: int, panel_id: int) -> RolePanel | None:
    result = await session.execute(
        select(RolePanel)
        .where(RolePanel.guild_id == guild_id, RolePanel.id == panel_id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def list_panels(session: AsyncSession, guild_id: int) -> list[RolePanel]:
    rows = await session.execute(
        select(RolePanel).where(RolePanel.guild_id == guild_id).order_by(RolePanel.id)
    )
    return list(rows.scalars())


async def count_panels(session: AsyncSession, guild_id: int) -> int:
    value = await session.execute(
        select(func.count()).select_from(RolePanel).where(RolePanel.guild_id == guild_id)
    )
    return int(value.scalar_one())


async def delete_panel(session: AsyncSession, guild_id: int, panel_id: int) -> bool:
    result = await session.execute(
        delete(RolePanel).where(RolePanel.guild_id == guild_id, RolePanel.id == panel_id)
    )
    return int(result.rowcount) > 0  # type: ignore[attr-defined]


async def set_message(
    session: AsyncSession,
    guild_id: int,
    panel_id: int,
    channel_id: int | None,
    message_id: int | None,
) -> None:
    await session.execute(
        update(RolePanel)
        .where(RolePanel.guild_id == guild_id, RolePanel.id == panel_id)
        .values(channel_id=channel_id, message_id=message_id)
    )


async def clear_message(session: AsyncSession, guild_id: int, message_id: int) -> int:
    result = await session.execute(
        update(RolePanel)
        .where(RolePanel.guild_id == guild_id, RolePanel.message_id == message_id)
        .values(message_id=None)
    )
    return int(result.rowcount)  # type: ignore[attr-defined]


async def panel_by_message(
    session: AsyncSession, guild_id: int, message_id: int
) -> RolePanel | None:
    result = await session.execute(
        select(RolePanel)
        .where(RolePanel.guild_id == guild_id, RolePanel.message_id == message_id)
        .execution_options(populate_existing=True)
    )
    return result.scalars().first()


async def reaction_messages(session: AsyncSession, guild_id: int) -> dict[int, int]:
    """message_id → panel_id aller Reaktions-Panels mit Nachricht (für den Reaktions-Cache)."""
    rows = await session.execute(
        select(RolePanel.message_id, RolePanel.id).where(
            RolePanel.guild_id == guild_id,
            RolePanel.kind == "reaction",
            RolePanel.message_id.is_not(None),
        )
    )
    return {message_id: panel_id for message_id, panel_id in rows.all() if message_id is not None}


async def items(session: AsyncSession, panel_id: int) -> list[RolePanelItem]:
    rows = await session.execute(
        select(RolePanelItem)
        .where(RolePanelItem.panel_id == panel_id)
        .order_by(RolePanelItem.position, RolePanelItem.id)
        .execution_options(populate_existing=True)
    )
    return list(rows.scalars())


async def add_item(
    session: AsyncSession,
    *,
    panel_id: int,
    guild_id: int,
    role_id: int,
    label: str | None,
    emoji: str | None,
    emoji_key: str | None,
    description: str | None,
    position: int,
) -> RolePanelItem:
    item = RolePanelItem(
        panel_id=panel_id,
        guild_id=guild_id,
        role_id=role_id,
        label=label,
        emoji=emoji,
        emoji_key=emoji_key,
        description=description,
        position=position,
    )
    session.add(item)
    await session.flush()
    return item


async def remove_item(session: AsyncSession, panel_id: int, role_id: int) -> bool:
    result = await session.execute(
        delete(RolePanelItem).where(
            RolePanelItem.panel_id == panel_id, RolePanelItem.role_id == role_id
        )
    )
    return int(result.rowcount) > 0  # type: ignore[attr-defined]
