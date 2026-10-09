"""Discord-Umsetzung des `ModerationPort` aus dem Service."""

from datetime import datetime

import discord

from ggbot.core.errors import UserError


class DiscordModerationPort:
    """Übersetzt Discord-Ausnahmen in `UserError`, damit der Service sie nicht kennen muss."""

    def __init__(self, client: discord.Client, guild: discord.Guild) -> None:
        self._client = client
        self._guild = guild
        self.guild_name = guild.name
        self.bot_user_id = guild.me.id

    async def timeout(self, user_id: int, until: datetime | None, reason: str) -> None:
        try:
            member = self._guild.get_member(user_id) or await self._guild.fetch_member(user_id)
            await member.timeout(until, reason=reason)
        except discord.NotFound as exc:
            raise UserError("error.mod_target_not_found") from exc
        except discord.Forbidden as exc:
            raise UserError("error.mod_forbidden") from exc

    async def kick(self, user_id: int, reason: str) -> None:
        try:
            await self._guild.kick(discord.Object(user_id), reason=reason)
        except discord.NotFound as exc:
            raise UserError("error.mod_target_not_found") from exc
        except discord.Forbidden as exc:
            raise UserError("error.mod_forbidden") from exc

    async def ban(self, user_id: int, reason: str, delete_seconds: int) -> None:
        try:
            await self._guild.ban(
                discord.Object(user_id), reason=reason, delete_message_seconds=delete_seconds
            )
        except discord.NotFound as exc:
            raise UserError("error.mod_target_not_found") from exc
        except discord.Forbidden as exc:
            raise UserError("error.mod_forbidden") from exc

    async def unban(self, user_id: int, reason: str) -> bool:
        try:
            await self._guild.unban(discord.Object(user_id), reason=reason)
        except discord.NotFound:
            return False  # war nicht gebannt
        except discord.Forbidden as exc:
            raise UserError("error.mod_forbidden") from exc
        return True

    async def dm(self, user_id: int, text: str) -> bool:
        # Geschlossene DMs sind der Normalfall; der Aufrufer meldet sie dem Moderator.
        try:
            user = self._client.get_user(user_id) or await self._client.fetch_user(user_id)
            await user.send(text)
        except (discord.Forbidden, discord.NotFound):
            return False
        return True
