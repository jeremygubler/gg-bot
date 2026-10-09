import discord

from ggbot.core.snapshot import ChannelInfo, EmojiInfo, GuildSnapshot, RoleInfo


def build_snapshot(guild: discord.Guild) -> GuildSnapshot:
    return GuildSnapshot(
        guild_id=guild.id,
        name=guild.name,
        channels=[
            ChannelInfo(
                id=ch.id,
                name=ch.name,
                type=ch.type.name,
                position=ch.position,
                parent_id=ch.category_id,
            )
            for ch in guild.channels
        ],
        roles=[
            RoleInfo(
                id=role.id,
                name=role.name,
                color=role.color.value,
                position=role.position,
                managed=role.managed,
            )
            for role in guild.roles
        ],
        emojis=[EmojiInfo(id=e.id, name=e.name, animated=e.animated) for e in guild.emojis],
    )
