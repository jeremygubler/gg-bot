"""Starboard: Nachrichten mit genug Sternen werden in einen eigenen Kanal übernommen."""

import contextlib
from collections.abc import Callable
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands
from pydantic import ValidationError

from ggbot.bot.checks import ensure_guild
from ggbot.bot.client import GGBot
from ggbot.bot.i18n import d
from ggbot.core.community import starboard as rules
from ggbot.core.db.models import ScheduledJob
from ggbot.core.errors import UserError
from ggbot.core.i18n import translator
from ggbot.core.logging import get_logger
from ggbot.core.redis import key
from ggbot.core.roles.plan import emoji_key
from ggbot.core.settings.starboard import StarboardSettings

log = get_logger(__name__)

REFRESH_JOB = "starboard.refresh"
EDIT_PAUSE_SECONDS = 3  # so oft darf eine Kopie höchstens bearbeitet werden
REFRESH_DELAY_SECONDS = 6
GOLD = 0xFFAC33


class StarboardCog(commands.Cog):
    def __init__(self, bot: GGBot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.services.scheduler.register(REFRESH_JOB, self._refresh_job)

    @commands.Cog.listener()
    async def on_ggbot_settings_changed(self, event: Any) -> None:
        if event.module == "starboard":
            self.bot.services.starboard.invalidate(event.guild_id)

    # --- Sterne ---

    async def _relevant(
        self, payload: discord.RawReactionActionEvent
    ) -> tuple[discord.Guild, StarboardSettings] | None:
        """Frühe, billige Prüfung: die meisten Reaktionen eines Servers sind keine Sterne."""
        if payload.guild_id is None:
            return None
        settings = await self.bot.services.starboard.settings(payload.guild_id)
        if not settings.enabled or settings.channel_id is None:
            return None
        if emoji_key(str(payload.emoji)) != emoji_key(settings.emoji):
            return None
        guild = self.bot.get_guild(payload.guild_id)
        return (guild, settings) if guild is not None else None

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        relevant = await self._relevant(payload)
        if relevant is None:
            return
        guild, settings = relevant
        channel = guild.get_channel_or_thread(payload.channel_id)
        author_id = payload.message_author_id
        if author_id is None:
            message = await self._fetch(guild, payload.channel_id, payload.message_id)
            if message is None:
                return
            author_id = message.author.id
        member = payload.member
        is_bot = member.bot if member is not None else False
        assert settings.channel_id is not None
        if not rules.counts(
            voter_id=payload.user_id,
            voter_is_bot=is_bot,
            author_id=author_id,
            channel_id=payload.channel_id,
            starboard_channel_id=settings.channel_id,
            ignored_channels=settings.ignored_channels,
            self_star=settings.self_star,
            nsfw=bool(getattr(channel, "nsfw", False)),
            ignore_nsfw=settings.ignore_nsfw,
        ):
            return
        service = self.bot.services.starboard
        await service.vote(
            guild.id,
            payload.message_id,
            payload.user_id,
            channel_id=payload.channel_id,
            author_id=author_id,
        )
        # Neu zählen statt dem Rückgabewert zu trauen: bei gleichzeitigen Sternen sähe sonst jeder
        # nur seinen eigenen Stand, und die Schwelle würde knapp verpasst.
        await self._apply(
            guild, settings, payload.message_id, await service.count(guild.id, payload.message_id)
        )

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        relevant = await self._relevant(payload)
        if relevant is None:
            return
        guild, settings = relevant
        service = self.bot.services.starboard
        if await service.unvote(guild.id, payload.message_id, payload.user_id) is not None:
            await self._apply(
                guild,
                settings,
                payload.message_id,
                await service.count(guild.id, payload.message_id),
            )

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        if payload.guild_id is None:
            return
        guild = self.bot.get_guild(payload.guild_id)
        entry = await self.bot.services.starboard.forget(payload.guild_id, payload.message_id)
        if entry is not None and entry.star_message_id and guild is not None:
            settings = await self.bot.services.starboard.settings(guild.id)
            await self._delete_copy(guild, settings, entry.star_message_id)

    # --- Kopie im Starboard-Kanal ---

    def _starboard_channel(
        self, guild: discord.Guild, settings: StarboardSettings
    ) -> discord.TextChannel | None:
        channel = guild.get_channel(settings.channel_id or 0)
        return channel if isinstance(channel, discord.TextChannel) else None

    async def _fetch(
        self, guild: discord.Guild, channel_id: int, message_id: int
    ) -> discord.Message | None:
        channel = guild.get_channel_or_thread(channel_id)
        if not isinstance(channel, discord.TextChannel | discord.Thread):
            return None
        try:
            return await channel.fetch_message(message_id)
        except (discord.NotFound, discord.Forbidden):
            return None

    async def _apply(
        self, guild: discord.Guild, settings: StarboardSettings, message_id: int, stars: int
    ) -> None:
        service = self.bot.services.starboard
        entry = await service.entry(guild.id, message_id)
        if entry is None:
            return
        what = rules.action(stars, settings.threshold, entry.star_message_id is not None)
        if what == "post":
            await self._post(guild, settings, entry.channel_id, message_id, stars)
        elif what == "update":
            await self._schedule_update(guild, entry.message_id)
        elif what == "remove" and entry.star_message_id:
            await self._delete_copy(guild, settings, entry.star_message_id)
            await service.finish_post(guild.id, message_id, None)

    async def _embed(
        self, message: discord.Message, settings: StarboardSettings, stars: int, locale: str
    ) -> tuple[str, discord.Embed]:
        channel = message.channel
        mention = channel.mention if hasattr(channel, "mention") else ""
        embed = discord.Embed(
            description=message.content[:4000] or None,
            color=GOLD,
            timestamp=message.created_at,
        )
        embed.set_author(
            name=message.author.display_name, icon_url=message.author.display_avatar.url
        )
        image = next(
            (a for a in message.attachments if (a.content_type or "").startswith("image/")), None
        )
        if image is not None:
            embed.set_image(url=image.url)
        embed.add_field(
            name=translator.t(locale, "starboard.source"),
            value=f"[{translator.t(locale, 'starboard.jump')}]({message.jump_url})",
            inline=False,
        )
        return rules.star_line(settings.emoji, stars, mention), embed

    async def _post(
        self,
        guild: discord.Guild,
        settings: StarboardSettings,
        channel_id: int,
        message_id: int,
        stars: int,
    ) -> None:
        service = self.bot.services.starboard
        if not await service.claim_post(guild.id, message_id):
            return  # ein gleichzeitiger Stern war schneller
        star_message_id: int | None = None
        try:
            target = self._starboard_channel(guild, settings)
            source = await self._fetch(guild, channel_id, message_id)
            if target is None or source is None:
                return
            locale = await self.bot.services.guilds.get_locale(guild.id)
            text, embed = await self._embed(source, settings, stars, locale)
            sent = await target.send(
                text, embed=embed, allowed_mentions=discord.AllowedMentions.none()
            )
            star_message_id = sent.id
        except discord.HTTPException:
            log.warning("starboard_post_failed", guild_id=guild.id, message_id=message_id)
        finally:
            await service.finish_post(guild.id, message_id, star_message_id)

    async def _delete_copy(
        self, guild: discord.Guild, settings: StarboardSettings, star_message_id: int
    ) -> None:
        target = self._starboard_channel(guild, settings)
        if target is not None:
            with contextlib.suppress(discord.HTTPException):
                await target.get_partial_message(star_message_id).delete()

    async def _schedule_update(self, guild: discord.Guild, message_id: int) -> None:
        """Höchstens alle paar Sekunden bearbeiten; der letzte Stand kommt per Job nach."""
        cooldown = self.bot.services.cooldown
        if await cooldown.hit("starup", f"{guild.id}:{message_id}", seconds=EDIT_PAUSE_SECONDS):
            await self._refresh(guild, message_id)
            return
        pending = key("starboard", "pending", guild.id, message_id)
        if await self.bot.services.redis.set(pending, "1", ex=REFRESH_DELAY_SECONDS * 2, nx=True):
            import datetime as dt

            await self.bot.services.scheduler.enqueue(
                guild.id,
                REFRESH_JOB,
                dt.datetime.now(dt.UTC) + dt.timedelta(seconds=REFRESH_DELAY_SECONDS),
                {"message_id": message_id},
            )

    async def _refresh_job(self, job: ScheduledJob) -> None:
        guild = self.bot.get_guild(job.guild_id)
        if guild is None:
            raise LookupError(f"Server {job.guild_id} nicht verfügbar")
        message_id = int(job.payload["message_id"])
        await self.bot.services.redis.delete(key("starboard", "pending", guild.id, message_id))
        await self._refresh(guild, message_id)

    async def _refresh(self, guild: discord.Guild, message_id: int) -> None:
        """Setzt die Zahl in der Kopie auf den Stand der Datenbank."""
        service = self.bot.services.starboard
        entry = await service.entry(guild.id, message_id)
        if entry is None or entry.star_message_id is None:
            return
        settings = await service.settings(guild.id)
        target = self._starboard_channel(guild, settings)
        if target is None:
            return
        stars = await service.count(guild.id, message_id)
        source_channel = guild.get_channel_or_thread(entry.channel_id)
        mention = source_channel.mention if source_channel is not None else ""
        try:
            await target.get_partial_message(entry.star_message_id).edit(
                content=rules.star_line(settings.emoji, stars, mention)
            )
        except discord.NotFound:
            # Die Kopie wurde von Hand gelöscht: beim nächsten Stern wird sie neu gepostet.
            await service.finish_post(guild.id, message_id, None)
        except discord.HTTPException:
            log.debug("starboard_edit_failed", guild_id=guild.id, message_id=message_id)

    # --- Befehle ---

    starboard = app_commands.Group(
        name="starboard",
        description=d("cmd.starboard.desc"),
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    async def _edit(
        self,
        interaction: discord.Interaction,
        mutate: Callable[[Any], None],
        reply_key: str,
        **params: object,
    ) -> None:
        guild = ensure_guild(interaction)
        services = self.bot.services
        data = (await services.starboard.settings(guild.id)).model_dump()
        mutate(data)
        try:
            updated = StarboardSettings.model_validate(data)
        except ValidationError as exc:
            error = exc.errors()[0]
            detail = f"{'.'.join(str(p) for p in error['loc'])}: {error['msg']}"[:150]
            raise UserError("error.starboard_invalid", detail=detail) from exc
        await services.settings.save(guild.id, updated)
        services.starboard.invalidate(guild.id)
        locale = await services.guilds.get_locale(guild.id)
        await interaction.response.send_message(
            translator.t(locale, reply_key, **params),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @starboard.command(name="set", description=d("cmd.starboard.set.desc"))
    @app_commands.describe(
        channel=d("cmd.arg.channel.desc"),
        emoji=d("cmd.arg.sb_emoji.desc"),
        threshold=d("cmd.arg.sb_threshold.desc"),
    )
    async def set_(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        emoji: str | None = None,
        threshold: app_commands.Range[int, 1, 100] | None = None,
    ) -> None:
        guild = ensure_guild(interaction)
        have = channel.permissions_for(guild.me)
        missing = [
            n for n in ("view_channel", "send_messages", "embed_links") if not getattr(have, n)
        ]
        if missing:
            raise UserError(
                "error.missing_bot_permissions",
                permissions=", ".join(n.replace("_", " ").title() for n in missing),
            )

        def mutate(data: dict[str, Any]) -> None:
            data.update(enabled=True, channel_id=channel.id)
            if emoji is not None:
                data["emoji"] = emoji
            if threshold is not None:
                data["threshold"] = threshold

        await self._edit(interaction, mutate, "starboard.set", channel=channel.mention)

    @starboard.command(name="off", description=d("cmd.starboard.off.desc"))
    async def off(self, interaction: discord.Interaction) -> None:
        await self._edit(interaction, lambda data: data.update(enabled=False), "starboard.off")

    @starboard.command(name="options", description=d("cmd.starboard.options.desc"))
    @app_commands.describe(
        self_star=d("cmd.arg.sb_self.desc"), ignore_nsfw=d("cmd.arg.sb_nsfw.desc")
    )
    async def options(
        self,
        interaction: discord.Interaction,
        self_star: bool | None = None,
        ignore_nsfw: bool | None = None,
    ) -> None:
        if self_star is None and ignore_nsfw is None:
            raise UserError("error.card_nothing_to_change")

        def mutate(data: dict[str, Any]) -> None:
            if self_star is not None:
                data["self_star"] = self_star
            if ignore_nsfw is not None:
                data["ignore_nsfw"] = ignore_nsfw

        await self._edit(interaction, mutate, "starboard.options")

    @starboard.command(name="ignore", description=d("cmd.starboard.ignore.desc"))
    @app_commands.describe(channel=d("cmd.arg.channel.desc"))
    async def ignore(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        """Schaltet um: Sterne in diesem Kanal zählen nicht (mehr)."""
        settings = await self.bot.services.starboard.settings(ensure_guild(interaction).id)
        adding = channel.id not in settings.ignored_channels

        def mutate(data: dict[str, Any]) -> None:
            ids = [i for i in data["ignored_channels"] if i != channel.id]
            data["ignored_channels"] = [*ids, channel.id] if adding else ids

        await self._edit(
            interaction,
            mutate,
            "starboard.ignored" if adding else "starboard.unignored",
            channel=channel.mention,
        )

    @starboard.command(name="status", description=d("cmd.starboard.status.desc"))
    async def status(self, interaction: discord.Interaction) -> None:
        guild = ensure_guild(interaction)
        s = await self.bot.services.starboard.settings(guild.id)
        locale = await self.bot.services.guilds.get_locale(guild.id)
        t = translator.t
        on, off = t(locale, "welcome.on"), t(locale, "welcome.off")
        text = t(
            locale,
            "starboard.status",
            channel=f"<#{s.channel_id}>" if s.enabled and s.channel_id else off,
            emoji=s.emoji,
            threshold=s.threshold,
            self_star=on if s.self_star else off,
            nsfw=on if s.ignore_nsfw else off,
            ignored=", ".join(f"<#{c}>" for c in s.ignored_channels) or t(locale, "welcome.none"),
        )
        await interaction.response.send_message(
            text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )


async def setup(bot: GGBot) -> None:
    await bot.add_cog(StarboardCog(bot))
