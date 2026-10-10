"""/notify: Twitch- und YouTube-Ankündigungen einrichten; dazu die Abfrage im Hintergrund."""

import asyncio
from datetime import UTC, datetime

import discord
from discord import app_commands
from discord.ext import commands

from ggbot.bot.checks import ensure_guild
from ggbot.bot.client import GGBot
from ggbot.bot.i18n import d
from ggbot.bot.notifier import DiscordAnnouncer, stream_embed, stream_text, video_embed, video_text
from ggbot.core.errors import UserError
from ggbot.core.i18n import translator
from ggbot.core.logging import get_logger
from ggbot.core.notify.poller import Poller
from ggbot.core.notify.twitch import normalize_login
from ggbot.core.notify.types import ProviderError, Resolved, StreamInfo, VideoInfo
from ggbot.core.services.notify import SubView

log = get_logger(__name__)

POLL_SECONDS = 60
READY_CHECK_SECONDS = 2
YOUTUBE_SECONDS = 300  # ein Feed ändert sich nicht im Minutentakt
NEEDS = ("view_channel", "send_messages", "embed_links")


class NotifyCog(commands.Cog):
    def __init__(self, bot: GGBot) -> None:
        self.bot = bot
        self.announcer = DiscordAnnouncer(bot)
        self.poller = Poller(
            bot.services.notify,
            self.announcer,
            bot.services.twitch,
            bot.services.youtube,
            youtube_gate=self._gate,
        )
        self._task: asyncio.Task[None] | None = None

    async def _gate(self, ref: str) -> bool:
        return await self.bot.services.cooldown.hit("ytpoll", ref, seconds=YOUTUBE_SECONDS)

    async def cog_load(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="notify-poll")

    async def cog_unload(self) -> None:
        if self._task is not None:
            self._task.cancel()

    async def _loop(self) -> None:
        while True:
            # Erst abfragen, wenn die Server bekannt sind: sonst gälte jeder als "nicht meiner".
            if self.bot.is_ready():
                try:
                    await self.poller.poll()
                except Exception:
                    # Ein Fehler in einer Runde darf die Abfrage nicht beenden.
                    log.exception("notify_poll_failed")
                await asyncio.sleep(POLL_SECONDS)
            else:
                await asyncio.sleep(READY_CHECK_SECONDS)

    # --- Befehle ---

    notify = app_commands.Group(
        name="notify",
        description=d("cmd.notify.desc"),
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )
    twitch = app_commands.Group(
        name="twitch", description=d("cmd.notify.twitch.desc"), parent=notify
    )
    youtube = app_commands.Group(
        name="youtube", description=d("cmd.notify.youtube.desc"), parent=notify
    )

    async def _reply(self, interaction: discord.Interaction, key: str, **params: object) -> None:
        locale = await self.bot.services.guilds.get_locale(interaction.guild_id)
        await interaction.response.send_message(
            translator.t(locale, key, **params),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    def _check_target(
        self, guild: discord.Guild, channel: discord.TextChannel, role: discord.Role | None
    ) -> None:
        have = channel.permissions_for(guild.me)
        missing = [name for name in NEEDS if not getattr(have, name)]
        if missing:
            raise UserError(
                "error.missing_bot_permissions",
                permissions=", ".join(n.replace("_", " ").title() for n in missing),
            )
        if role is not None and role.is_default():
            raise UserError("error.notify_role_everyone")

    async def _resolve(self, provider: str, text: str) -> Resolved:
        services = self.bot.services
        try:
            if provider == "twitch":
                if not services.twitch.configured:
                    raise UserError("error.notify_twitch_off")
                return await services.twitch.resolve(text)
            return await services.youtube.resolve(text)
        except ProviderError as exc:
            raise UserError(f"error.notify_{exc.key}") from exc

    async def _add(
        self,
        interaction: discord.Interaction,
        provider: str,
        target: str,
        channel: discord.TextChannel,
        message: str | None,
        role: discord.Role | None,
    ) -> None:
        guild = ensure_guild(interaction)
        self._check_target(guild, channel, role)
        await interaction.response.defer(ephemeral=True)  # die Anfrage beim Anbieter dauert
        resolved = await self._resolve(provider, target)
        sub = await self.bot.services.notify.add(
            guild.id,
            resolved,
            channel_id=channel.id,
            message=message,
            role_id=role.id if role else None,
        )
        locale = await self.bot.services.guilds.get_locale(guild.id)
        key = "notify.added_live" if resolved.live_stream_id else "notify.added"
        await interaction.followup.send(
            translator.t(locale, key, name=sub.display_name, channel=channel.mention),
            ephemeral=True,
        )

    @twitch.command(name="add", description=d("cmd.notify.twitch.add.desc"))
    @app_commands.describe(
        streamer=d("cmd.arg.notify_streamer.desc"),
        channel=d("cmd.arg.channel.desc"),
        message=d("cmd.arg.notify_message_twitch.desc"),
        role=d("cmd.arg.notify_role.desc"),
    )
    async def twitch_add(
        self,
        interaction: discord.Interaction,
        streamer: str,
        channel: discord.TextChannel,
        message: str | None = None,
        role: discord.Role | None = None,
    ) -> None:
        await self._add(interaction, "twitch", streamer, channel, message, role)

    @youtube.command(name="add", description=d("cmd.notify.youtube.add.desc"))
    @app_commands.describe(
        youtube_channel=d("cmd.arg.notify_youtube.desc"),
        channel=d("cmd.arg.channel.desc"),
        message=d("cmd.arg.notify_message_youtube.desc"),
        role=d("cmd.arg.notify_role.desc"),
    )
    async def youtube_add(
        self,
        interaction: discord.Interaction,
        youtube_channel: str,
        channel: discord.TextChannel,
        message: str | None = None,
        role: discord.Role | None = None,
    ) -> None:
        await self._add(interaction, "youtube", youtube_channel, channel, message, role)

    async def _remove(self, interaction: discord.Interaction, provider: str, text: str) -> None:
        guild = ensure_guild(interaction)
        service = self.bot.services.notify
        subs = [s for s in await service.for_guild(guild.id) if s.provider == provider]
        wanted = text.strip().casefold().removeprefix("@")
        match = next(
            (s for s in subs if wanted in (s.ref.casefold(), s.display_name.casefold())), None
        )
        if match is None and provider == "twitch":
            try:
                ref = normalize_login(text)
            except ProviderError:
                ref = None
            match = next((s for s in subs if s.ref == ref), None)
        if match is None:
            raise UserError("error.notify_unknown")
        await service.remove(guild.id, provider, match.ref)
        await self._reply(interaction, "notify.removed", name=match.display_name)

    @twitch.command(name="remove", description=d("cmd.notify.twitch.remove.desc"))
    @app_commands.describe(streamer=d("cmd.arg.notify_streamer.desc"))
    async def twitch_remove(self, interaction: discord.Interaction, streamer: str) -> None:
        await self._remove(interaction, "twitch", streamer)

    @youtube.command(name="remove", description=d("cmd.notify.youtube.remove.desc"))
    @app_commands.describe(youtube_channel=d("cmd.arg.notify_youtube.desc"))
    async def youtube_remove(self, interaction: discord.Interaction, youtube_channel: str) -> None:
        await self._remove(interaction, "youtube", youtube_channel)

    async def _choices(
        self, interaction: discord.Interaction, current: str, provider: str
    ) -> list[app_commands.Choice[str]]:
        if interaction.guild_id is None:
            return []
        needle = current.casefold()
        return [
            app_commands.Choice(name=s.display_name[:100], value=s.ref)
            for s in await self.bot.services.notify.for_guild(interaction.guild_id)
            if s.provider == provider and needle in s.display_name.casefold()
        ][:25]

    @twitch_remove.autocomplete("streamer")
    async def _ac_twitch(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self._choices(interaction, current, "twitch")

    @youtube_remove.autocomplete("youtube_channel")
    async def _ac_youtube(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self._choices(interaction, current, "youtube")

    @notify.command(name="list", description=d("cmd.notify.list.desc"))
    async def list_(self, interaction: discord.Interaction) -> None:
        guild = ensure_guild(interaction)
        locale = await self.bot.services.guilds.get_locale(guild.id)
        subs = await self.bot.services.notify.for_guild(guild.id)
        if not subs:
            text = translator.t(locale, "notify.list_empty")
        else:
            text = "\n".join(
                translator.t(
                    locale,
                    "notify.list_line",
                    id=s.id,
                    provider=s.provider.title(),
                    name=s.display_name,
                    channel=f"<#{s.channel_id}>",
                    role=f" · <@&{s.role_id}>" if s.role_id else "",
                )
                for s in subs
            )[:1900]
        await interaction.response.send_message(
            text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )

    @notify.command(name="test", description=d("cmd.notify.test.desc"))
    @app_commands.describe(subscription=d("cmd.arg.notify_sub.desc"))
    async def test(self, interaction: discord.Interaction, subscription: int) -> None:
        """Schickt eine Beispiel-Meldung in den Zielkanal: prüft Rechte, Rolle und Vorlage."""
        guild = ensure_guild(interaction)
        sub = await self.bot.services.notify.get(guild.id, subscription)
        locale = await self.bot.services.guilds.get_locale(guild.id)
        channel = guild.get_channel(sub.channel_id)
        if not isinstance(channel, discord.TextChannel):
            raise UserError("error.notify_channel_gone")
        self._check_target(guild, channel, None)
        now = datetime.now(UTC)
        if sub.provider == "twitch":
            stream = StreamInfo(
                "test", sub.ref, sub.display_name, translator.t(locale, "notify.sample_title"),
                translator.t(locale, "notify.sample_game"), 123, now, "",
            )  # fmt: skip
            text, embed = stream_text(sub, stream, locale), stream_embed(stream, locale)
        else:
            video = VideoInfo(
                "dQw4w9WgXcQ", sub.ref, sub.display_name,
                translator.t(locale, "notify.sample_title"), now, "",
            )  # fmt: skip
            text, embed = video_text(sub, video, locale), video_embed(video)
        result = await self.announcer.send(sub, text, embed)
        if result.kind != "sent":
            raise UserError("error.notify_test_failed")
        await self._reply(interaction, "notify.test_sent", channel=channel.mention)

    @test.autocomplete("subscription")
    async def _ac_sub(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        if interaction.guild_id is None:
            return []
        needle = current.casefold()
        subs: list[SubView] = await self.bot.services.notify.for_guild(interaction.guild_id)
        return [
            app_commands.Choice(name=f"{s.provider.title()}: {s.display_name}"[:100], value=s.id)
            for s in subs
            if needle in s.display_name.casefold()
        ][:25]


async def setup(bot: GGBot) -> None:
    await bot.add_cog(NotifyCog(bot))
