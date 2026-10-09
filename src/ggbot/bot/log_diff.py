"""Unterschiede zwischen Discord-Objekten als `Change`-Listen für das Event-Log."""

import discord

from ggbot.core.logs.entries import Change, MessageSnapshot, UserRef


def user_ref(user: discord.abc.User) -> UserRef:
    return UserRef(user.id, str(user), user.bot)


def snapshot_of(message: discord.Message) -> MessageSnapshot:
    return MessageSnapshot(
        id=message.id,
        channel_id=message.channel.id,
        author=user_ref(message.author),
        content=message.content,
        attachments=tuple(a.url for a in message.attachments),
        created_at=message.created_at,
    )


def permission_names(value: int) -> set[str]:
    return {name for name, enabled in discord.Permissions(value) if enabled}


def _permission_text(before: int, after: int) -> tuple[str, str]:
    old, new = permission_names(before), permission_names(after)
    added = ", ".join(sorted(new - old)) or "–"
    removed = ", ".join(sorted(old - new)) or "–"
    return f"−{removed}", f"+{added}"


def _hex(value: int) -> str:
    return f"#{value:06x}"


def role_changes(before: discord.Role, after: discord.Role) -> list[Change]:
    """Nur Eigenschaften, die jemand bewusst ändert. Die Position fehlt absichtlich: Umsortieren
    verschiebt viele Rollen auf einmal und würde das Log fluten."""
    changes: list[Change] = []
    if before.name != after.name:
        changes.append(Change("logging.change.name", before.name, after.name))
    if before.color.value != after.color.value:
        changes.append(
            Change("logging.change.color", _hex(before.color.value), _hex(after.color.value))
        )
    if before.permissions.value != after.permissions.value:
        old, new = _permission_text(before.permissions.value, after.permissions.value)
        changes.append(Change("logging.change.permissions", old, new))
    if before.hoist != after.hoist:
        changes.append(Change("logging.change.hoist", str(before.hoist), str(after.hoist)))
    if before.mentionable != after.mentionable:
        changes.append(
            Change("logging.change.mentionable", str(before.mentionable), str(after.mentionable))
        )
    return changes


def channel_changes(
    before: discord.abc.GuildChannel, after: discord.abc.GuildChannel
) -> list[Change]:
    changes: list[Change] = []
    if before.name != after.name:
        changes.append(Change("logging.change.name", before.name, after.name))
    for attr, key in (
        ("topic", "logging.change.topic"),
        ("slowmode_delay", "logging.change.slowmode"),
        ("nsfw", "logging.change.nsfw"),
        ("bitrate", "logging.change.bitrate"),
        ("user_limit", "logging.change.user_limit"),
    ):
        old, new = getattr(before, attr, None), getattr(after, attr, None)
        if old != new:
            changes.append(
                Change(
                    key, str(old) if old is not None else "–", str(new) if new is not None else "–"
                )
            )
    old_category = before.category.name if before.category else "–"
    new_category = after.category.name if after.category else "–"
    if old_category != new_category:
        changes.append(Change("logging.change.category", old_category, new_category))
    return changes
