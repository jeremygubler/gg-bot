"""Ankündigungen auf Discord: Nachrichten bauen und senden."""

from datetime import UTC, datetime
from typing import Any

import discord

from ggbot.core import messages
from ggbot.core.i18n import translator
from ggbot.core.logging import get_logger
from ggbot.core.notify.poller import SendResult
from ggbot.core.notify.types import StreamInfo, VideoInfo
from ggbot.core.services.notify import TWITCH_FIELDS, YOUTUBE_FIELDS, SubView

log = get_logger(__name__)

TWITCH_COLOR = 0x9146FF
YOUTUBE_COLOR = 0xFF0000
ENDED_COLOR = 0x4F545C


def twitch_url(login: str) -> str:
    return f"https://www.twitch.tv/{login}"


def _mentions(sub: SubView) -> discord.AllowedMentions:
    """Gepingt wird höchstens die eingestellte Rolle: Titel und Namen kommen von Dritten und
    könnten @everyone enthalten."""
    roles: list[discord.abc.Snowflake] = [discord.Object(sub.role_id)] if sub.role_id else []
    return discord.AllowedMentions(everyone=False, roles=roles, users=False, replied_user=False)


def _content(sub: SubView, text: str) -> str:
    return f"<@&{sub.role_id}> {text}" if sub.role_id else text


def stream_text(sub: SubView, stream: StreamInfo, locale: str) -> str:
    template = sub.message or translator.t(locale, "notify.default_twitch")
    return messages.render_template(
        template,
        TWITCH_FIELDS,
        streamer=stream.name,
        title=stream.title,
        game=stream.game or "—",
        url=twitch_url(stream.login),
        viewers=stream.viewers,
    )


def video_text(sub: SubView, video: VideoInfo, locale: str) -> str:
    template = sub.message or translator.t(locale, "notify.default_youtube")
    return messages.render_template(
        template, YOUTUBE_FIELDS, channel=video.channel_name, title=video.title, url=video.url
    )


def stream_embed(stream: StreamInfo, locale: str) -> discord.Embed:
    embed = discord.Embed(
        title=(stream.title or stream.name)[:256],
        url=twitch_url(stream.login),
        color=TWITCH_COLOR,
        timestamp=stream.started_at,
    )
    embed.set_author(name=stream.name[:256], url=twitch_url(stream.login))
    if stream.game:
        embed.add_field(name=translator.t(locale, "notify.field_game"), value=stream.game[:1024])
    embed.add_field(name=translator.t(locale, "notify.field_viewers"), value=str(stream.viewers))
    if stream.thumbnail.startswith("https://"):
        # Twitch cacht das Vorschaubild lange: ohne Zusatz zeigt die Meldung ein altes Bild.
        embed.set_image(url=f"{stream.thumbnail}?t={int(datetime.now(UTC).timestamp())}")
    return embed


def video_embed(video: VideoInfo) -> discord.Embed:
    embed = discord.Embed(
        title=video.title[:256], url=video.url, color=YOUTUBE_COLOR, timestamp=video.published
    )
    embed.set_author(
        name=video.channel_name[:256], url=f"https://www.youtube.com/channel/{video.channel_id}"
    )
    if video.thumbnail.startswith("https://"):
        embed.set_image(url=video.thumbnail)
    return embed


class DiscordAnnouncer:
    def __init__(self, bot: Any) -> None:
        self.bot = bot

    def available(self, guild_id: int) -> bool:
        return self.bot.get_guild(guild_id) is not None

    async def _locale(self, guild_id: int) -> str:
        return str(await self.bot.services.guilds.get_locale(guild_id))

    def _channel(self, sub: SubView) -> discord.TextChannel | None:
        guild = self.bot.get_guild(sub.guild_id)
        channel = guild.get_channel(sub.channel_id) if guild else None
        return channel if isinstance(channel, discord.TextChannel) else None

    async def send(self, sub: SubView, text: str, embed: discord.Embed) -> SendResult:
        channel = self._channel(sub)
        if channel is None:
            log.warning("notify_channel_missing", guild_id=sub.guild_id, sub_id=sub.id)
            return SendResult("drop")
        try:
            message = await channel.send(
                _content(sub, text), embed=embed, allowed_mentions=_mentions(sub)
            )
        except discord.Forbidden:
            log.warning("notify_not_allowed", guild_id=sub.guild_id, channel_id=channel.id)
            return SendResult("drop")
        except discord.HTTPException as exc:
            log.warning("notify_send_failed", guild_id=sub.guild_id, status=exc.status)
            return SendResult("retry")
        return SendResult("sent", message.id)

    async def announce_stream(self, sub: SubView, stream: StreamInfo) -> SendResult:
        locale = await self._locale(sub.guild_id)
        return await self.send(sub, stream_text(sub, stream, locale), stream_embed(stream, locale))

    async def announce_video(self, sub: SubView, video: VideoInfo) -> SendResult:
        locale = await self._locale(sub.guild_id)
        return await self.send(sub, video_text(sub, video, locale), video_embed(video))

    async def end_stream(self, sub: SubView) -> None:
        """Markiert die Meldung als beendet. Fehlt sie oder darf der Bot sie nicht ändern, bleibt
        sie stehen: das ist kein Fehler."""
        channel = self._channel(sub)
        if channel is None or sub.message_id is None:
            return
        locale = await self._locale(sub.guild_id)
        try:
            message = await channel.fetch_message(sub.message_id)
            if not message.embeds:
                return
            embed = message.embeds[0].copy()
            embed.color = ENDED_COLOR
            embed.set_footer(text=translator.t(locale, "notify.stream_ended"))
            await message.edit(embed=embed)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            log.debug("notify_end_edit_failed", sub_id=sub.id)
