"""Leveling: XP pro Nachricht und pro Voice-Minute, Level-Up, /rank, /leaderboard, /card."""

import asyncio
import io
import re

import discord
from discord import app_commands
from discord.ext import commands

from ggbot.bot.checks import ensure_guild
from ggbot.bot.client import GGBot
from ggbot.bot.i18n import d
from ggbot.bot.rewards import apply_rewards
from ggbot.core import events
from ggbot.core.errors import UserError
from ggbot.core.i18n import translator
from ggbot.core.leveling.announce import render_template
from ggbot.core.leveling.rules import VoiceMember, voice_eligible
from ggbot.core.logging import get_logger
from ggbot.core.services.leveling import Award, XpContext
from ggbot.core.settings.leveling import CARD_TEMPLATES, CardStyle, LevelingSettings
from ggbot.render.rank_card import RankCardData, format_number, render_rank_card

log = get_logger(__name__)

VOICE_TICK_SECONDS = 60
_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")
TEMPLATE_CHOICES = [app_commands.Choice(name=name, value=name) for name in CARD_TEMPLATES]
Messageable = discord.TextChannel | discord.VoiceChannel | discord.StageChannel | discord.Thread


def parse_color(text: str) -> int:
    match = _HEX.match(text.strip())
    if match is None:
        raise UserError("error.card_color_invalid", value=text[:20])
    return int(match.group(1), 16)


class LevelingCog(commands.Cog):
    def __init__(self, bot: GGBot) -> None:
        self.bot = bot
        self._ticker: asyncio.Task[None] | None = None
        self._warned_unknown_voice: set[int] = set()

    async def cog_load(self) -> None:
        self._ticker = asyncio.create_task(self._voice_loop(), name="leveling-voice")

    async def cog_unload(self) -> None:
        if self._ticker is not None:
            self._ticker.cancel()

    @commands.Cog.listener()
    async def on_ggbot_settings_changed(self, event: events.SettingsChanged) -> None:
        if event.module == "leveling":
            self.bot.services.leveling.invalidate(event.guild_id)

    # --- Text-XP ---

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        guild = message.guild
        author = message.author
        if guild is None or message.webhook_id is not None or author.bot:
            return
        if message.type not in (discord.MessageType.default, discord.MessageType.reply):
            return
        if not isinstance(author, discord.Member):
            return
        channel = message.channel
        context = XpContext(
            guild_id=guild.id,
            channel_id=channel.id,
            parent_channel_id=channel.parent_id if isinstance(channel, discord.Thread) else None,
            user_id=author.id,
            role_ids=frozenset(role.id for role in author.roles),
        )
        award = await self.bot.services.leveling.award_message(context)
        if award is not None and award.level_changed:
            await self._after_award(guild, author, award, channel)

    async def _after_award(
        self,
        guild: discord.Guild,
        member: discord.Member,
        award: Award,
        source: discord.abc.Messageable | None,
    ) -> None:
        settings = await self.bot.services.leveling.settings(guild.id)
        await apply_rewards(guild, member, award.new_level, settings)
        if award.leveled_up:
            await self._announce(guild, member, award, settings, source)

    async def _announce(
        self,
        guild: discord.Guild,
        member: discord.Member,
        award: Award,
        settings: LevelingSettings,
        source: discord.abc.Messageable | None,
    ) -> None:
        if settings.announce == "off":
            return
        locale = await self.bot.services.guilds.get_locale(guild.id)
        template = settings.announce_message or translator.t(locale, "leveling.levelup_default")
        text = render_template(
            template,
            user=member.mention,
            name=member.display_name,
            level=award.new_level,
            xp=award.xp,
        )
        target: discord.abc.Messageable | None
        if settings.announce == "dm":
            target = member
        elif settings.announce == "channel":
            target = guild.get_channel(settings.announce_channel_id or 0)  # type: ignore[assignment]
        else:
            target = source
        if target is None:
            return
        try:
            # `{name}` ist ein vom Mitglied frei wählbarer Nickname: nur das Mitglied selbst darf
            # angepingt werden, nie @everyone oder eine Rolle.
            await target.send(
                text,
                allowed_mentions=discord.AllowedMentions(
                    everyone=False, roles=False, users=[member], replied_user=False
                ),
            )
        except discord.Forbidden:
            # Geschlossene DMs oder fehlendes Schreibrecht: erwartbar, der Aufstieg zählt trotzdem.
            log.debug("leveling_announce_not_allowed", guild_id=guild.id, user_id=member.id)

    # --- Voice-XP ---

    async def _voice_loop(self) -> None:
        while True:
            await asyncio.sleep(VOICE_TICK_SECONDS)
            try:
                await self.voice_tick()
            except Exception:
                # Ein Fehler in einer Minute darf den Takt nicht beenden.
                log.exception("leveling_voice_tick_failed")

    async def voice_tick(self) -> None:
        """Eine Minute Voice-XP für alle, die die Bedingungen erfüllen."""
        for guild in self.bot.guilds:
            settings = await self.bot.services.leveling.settings(guild.id)
            if not (settings.enabled and settings.voice.enabled):
                continue
            channels: list[discord.VoiceChannel | discord.StageChannel] = [
                *guild.voice_channels,
                *guild.stage_channels,
            ]
            for channel in channels:
                if channel == guild.afk_channel:
                    continue  # Der AFK-Kanal zählt nie.
                await self._tick_channel(guild, channel, settings)

    async def _tick_channel(
        self,
        guild: discord.Guild,
        channel: discord.VoiceChannel | discord.StageChannel,
        settings: LevelingSettings,
    ) -> None:
        present: list[tuple[discord.Member, VoiceMember]] = []
        unknown = 0
        for user_id, state in channel.voice_states.items():
            member = guild.get_member(user_id)
            if member is None:
                unknown += 1  # ohne Mitglied wissen wir weder Bot noch Rollen: lieber kein XP
                continue
            muted = state.self_mute or state.self_deaf or state.mute or state.deaf
            present.append((member, VoiceMember(user_id, member.bot, muted)))
        if unknown and guild.id not in self._warned_unknown_voice:
            self._warned_unknown_voice.add(guild.id)
            log.warning("leveling_voice_unknown_members", guild_id=guild.id, count=unknown)

        eligible = voice_eligible(
            [state for _, state in present], requires_company=settings.voice.requires_company
        )
        for member, _ in present:
            if member.id not in eligible:
                continue
            award = await self.bot.services.leveling.award_voice(
                XpContext(
                    guild_id=guild.id,
                    user_id=member.id,
                    channel_id=channel.id,
                    parent_channel_id=None,
                    role_ids=frozenset(role.id for role in member.roles),
                )
            )
            if award is not None and award.level_changed:
                await self._after_award(guild, member, award, channel)

    # --- Anzeige ---

    async def _locale(self, guild: discord.Guild) -> str:
        return await self.bot.services.guilds.get_locale(guild.id)

    async def _require_enabled(self, guild: discord.Guild) -> LevelingSettings:
        settings = await self.bot.services.leveling.settings(guild.id)
        if not settings.enabled:
            raise UserError("error.leveling_disabled")
        return settings

    async def _card_file(
        self, guild: discord.Guild, member: discord.Member, locale: str
    ) -> discord.File:
        service = self.bot.services.leveling
        info = await service.rank(guild.id, member.id)
        style = await service.card_style(guild.id, member.id)
        try:
            avatar = await member.display_avatar.replace(size=128, static_format="png").read()
        except (discord.HTTPException, discord.NotFound):
            avatar = None  # die Karte kommt dann mit Platzhalter
        data = RankCardData(
            name=member.display_name,
            level=info.progress.level,
            into_level=info.progress.into_level,
            needed=info.progress.needed,
            total_xp=info.progress.xp,
            position=info.position,
            avatar=avatar,
            accent=style.accent,
            background=style.background,
            template=style.template,
            rank_label=translator.t(locale, "leveling.card.rank"),
            level_label=translator.t(locale, "leveling.card.level"),
        )
        png = await asyncio.to_thread(render_rank_card, data)
        return discord.File(io.BytesIO(png), filename="rank.png")

    @app_commands.command(name="rank", description=d("cmd.rank.desc"))
    @app_commands.describe(member=d("cmd.arg.member.desc"))
    @app_commands.guild_only()
    async def rank(
        self, interaction: discord.Interaction, member: discord.Member | None = None
    ) -> None:
        guild = ensure_guild(interaction)
        await self._require_enabled(guild)
        target = member or interaction.user
        if not isinstance(target, discord.Member):
            raise UserError("error.guild_only")
        if target.bot:
            raise UserError("error.leveling_bot")
        locale = await self._locale(guild)
        await interaction.response.defer()
        await interaction.followup.send(file=await self._card_file(guild, target, locale))

    @app_commands.command(name="leaderboard", description=d("cmd.leaderboard.desc"))
    @app_commands.describe(page=d("cmd.arg.page.desc"))
    @app_commands.guild_only()
    async def leaderboard(
        self, interaction: discord.Interaction, page: app_commands.Range[int, 1] = 1
    ) -> None:
        guild = ensure_guild(interaction)
        await self._require_enabled(guild)
        locale = await self._locale(guild)
        board = await self.bot.services.leveling.leaderboard(guild.id, page)
        t = translator.t
        embed = discord.Embed(title=t(locale, "leveling.leaderboard.title", server=guild.name))
        if not board.rows:
            embed.description = t(locale, "leveling.leaderboard.empty")
        else:
            embed.description = "\n".join(
                t(
                    locale,
                    "leveling.leaderboard.line",
                    position=row.position,
                    user=f"<@{row.user_id}>",
                    level=row.level,
                    xp=format_number(row.xp),
                )
                for row in board.rows
            )
            embed.set_footer(
                text=t(locale, "leveling.leaderboard.footer", page=board.page, pages=board.pages)
            )
        await interaction.response.send_message(
            embed=embed, allowed_mentions=discord.AllowedMentions.none()
        )

    # --- Persönliche Karte ---

    card = app_commands.Group(name="card", description=d("cmd.card.desc"), guild_only=True)

    @card.command(name="set", description=d("cmd.card.set.desc"))
    @app_commands.describe(
        accent=d("cmd.arg.accent.desc"),
        background=d("cmd.arg.background.desc"),
        template=d("cmd.arg.template.desc"),
    )
    @app_commands.choices(template=TEMPLATE_CHOICES)
    async def card_set(
        self,
        interaction: discord.Interaction,
        accent: str | None = None,
        background: str | None = None,
        template: app_commands.Choice[str] | None = None,
    ) -> None:
        guild = ensure_guild(interaction)
        member = interaction.user
        if not isinstance(member, discord.Member):
            raise UserError("error.guild_only")
        if accent is None and background is None and template is None:
            raise UserError("error.card_nothing_to_change")
        service = self.bot.services.leveling
        current = await service.card_style(guild.id, member.id)
        style = CardStyle(
            accent=parse_color(accent) if accent is not None else current.accent,
            background=parse_color(background) if background is not None else current.background,
            template=template.value if template is not None else current.template,
        )
        await service.set_card_style(guild.id, member.id, style)
        locale = await self._locale(guild)
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(
            translator.t(locale, "leveling.card.saved"),
            file=await self._card_file(guild, member, locale),
            ephemeral=True,
        )

    @card.command(name="reset", description=d("cmd.card.reset.desc"))
    async def card_reset(self, interaction: discord.Interaction) -> None:
        guild = ensure_guild(interaction)
        await self.bot.services.leveling.reset_card_style(guild.id, interaction.user.id)
        locale = await self._locale(guild)
        await interaction.response.send_message(
            translator.t(locale, "leveling.card.reset"), ephemeral=True
        )


async def setup(bot: GGBot) -> None:
    await bot.add_cog(LevelingCog(bot))
