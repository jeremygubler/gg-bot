"""/autodelete: Nachrichten in bestimmten Kanälen nach einer Frist automatisch löschen."""

import asyncio
import contextlib
from datetime import UTC, datetime
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands
from pydantic import ValidationError

from ggbot.bot.checks import ensure_guild
from ggbot.bot.client import GGBot
from ggbot.bot.i18n import d
from ggbot.core.autodelete import rules
from ggbot.core.duration import format_duration, parse_duration
from ggbot.core.errors import UserError
from ggbot.core.i18n import translator
from ggbot.core.logging import get_logger
from ggbot.core.settings.autodelete import (
    MAX_AGE_MINUTES,
    MIN_AGE_MINUTES,
    AutodeleteRule,
    AutodeleteSettings,
)

log = get_logger(__name__)

RUN_SECONDS = 300
# Wie viele Nachrichten je Lauf und Kanal angesehen werden: etwas mehr, als gelöscht werden darf,
# damit angeheftete und geschützte nicht den ganzen Lauf verbrauchen.
SCAN_LIMIT = 500
SINGLE_PAUSE = 1.0  # Einzel-Löschungen unter dem Ratenlimit halten
NEEDS = ("view_channel", "read_message_history", "manage_messages")


class AutodeleteCog(commands.Cog):
    def __init__(self, bot: GGBot) -> None:
        self.bot = bot
        self._task: asyncio.Task[None] | None = None

    async def cog_load(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="autodelete")

    async def cog_unload(self) -> None:
        if self._task is not None:
            self._task.cancel()

    @commands.Cog.listener()
    async def on_ggbot_settings_changed(self, event: Any) -> None:
        if event.module == "autodelete":
            self.bot.services.autodelete.invalidate(event.guild_id)

    async def _loop(self) -> None:
        while True:
            if self.bot.is_ready():
                try:
                    await self.run_all()
                except Exception:
                    log.exception("autodelete_failed")
                await asyncio.sleep(RUN_SECONDS)
            else:
                await asyncio.sleep(5)

    async def run_all(self) -> int:
        deleted = 0
        for guild in list(self.bot.guilds):
            try:
                deleted += await self.run_guild(guild)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Ein Server darf die übrigen nicht aufhalten.
                log.exception("autodelete_guild_failed", guild_id=guild.id)
        return deleted

    async def run_guild(self, guild: discord.Guild) -> int:
        service = self.bot.services.autodelete
        settings = await service.settings(guild.id)
        if not settings.enabled or not settings.rules:
            return 0
        protected = await self._protected(guild)
        deleted = 0
        for rule in settings.rules:
            channel = guild.get_channel(rule.channel_id)
            if not isinstance(channel, discord.TextChannel):
                log.warning(
                    "autodelete_channel_missing", guild_id=guild.id, channel_id=rule.channel_id
                )
                continue
            have = channel.permissions_for(guild.me)
            if not all(getattr(have, name) for name in NEEDS):
                log.warning("autodelete_no_permission", guild_id=guild.id, channel_id=channel.id)
                continue
            if not await service.claim_run(guild.id, channel.id, seconds=RUN_SECONDS - 30):
                continue
            deleted += await self._run_rule(channel, rule, protected)
        return deleted

    async def _protected(self, guild: discord.Guild) -> frozenset[int]:
        """Nachrichten, die der Bot noch braucht: Panels (ohne sie sind die Knöpfe tot), laufende
        Giveaways (die Teilnahme hängt am Knopf) und die Kopien im Starboard."""
        services = self.bot.services
        ids: set[int] = set()
        ids.update(p.message_id for p in await services.rolepanels.list(guild.id) if p.message_id)
        ids.update(
            p.message_id for p in await services.tickets.list_panels(guild.id) if p.message_id
        )
        verification = await services.verification.settings(guild.id)
        if verification.panel_message_id:
            ids.add(verification.panel_message_id)
        ids.update(
            g.message_id
            for g in await services.engagement.active_giveaways(guild.id)
            if g.message_id
        )
        ids.update(await services.starboard.copy_message_ids(guild.id))
        return frozenset(ids)

    async def _run_rule(
        self, channel: discord.TextChannel, rule: AutodeleteRule, protected: frozenset[int]
    ) -> int:
        now = datetime.now(UTC)
        before = discord.Object(
            id=discord.utils.time_snowflake(rules.cutoff(now, rule.max_age_minutes))
        )
        history = [m async for m in channel.history(limit=SCAN_LIMIT, before=before)]
        # Eine Umfrage, die noch läuft, wird mit ihrer Nachricht gelöscht; die Stimmen wären weg.
        running = frozenset(
            m.id for m in history if m.poll is not None and not m.poll.is_finalised()
        )
        plan = rules.plan(
            [rules.Message(m.id, m.created_at, m.pinned) for m in history],
            now=now,
            max_age_minutes=rule.max_age_minutes,
            keep_pinned=rule.keep_pinned,
            protected=protected | running,
        )
        if plan.total == 0:
            return 0
        deleted = 0
        try:
            for start in range(0, len(plan.bulk), 100):
                chunk = plan.bulk[start : start + 100]
                await channel.delete_messages([discord.Object(id=i) for i in chunk])
                deleted += len(chunk)
            for message_id in plan.single:
                with contextlib.suppress(discord.NotFound):  # schon weg: auch gut
                    await channel.get_partial_message(message_id).delete()
                deleted += 1
                await asyncio.sleep(SINGLE_PAUSE)
        except discord.Forbidden:
            log.warning("autodelete_forbidden", channel_id=channel.id)
        except discord.HTTPException:
            log.warning("autodelete_failed_channel", channel_id=channel.id, exc_info=True)
        log.info(
            "autodelete_run",
            guild_id=channel.guild.id,
            channel_id=channel.id,
            planned=plan.total,
            deleted=deleted,
        )
        return deleted

    # --- Befehle ---

    autodelete = app_commands.Group(
        name="autodelete",
        description=d("cmd.autodelete.desc"),
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    async def _save(self, guild_id: int, data: dict[str, Any]) -> AutodeleteSettings:
        services = self.bot.services
        try:
            updated = AutodeleteSettings.model_validate(data)
        except ValidationError as exc:
            error = exc.errors()[0]
            detail = f"{'.'.join(str(p) for p in error['loc'])}: {error['msg']}"[:150]
            raise UserError("error.autodelete_invalid", detail=detail) from exc
        await services.settings.save(guild_id, updated)
        services.autodelete.invalidate(guild_id)
        return updated

    async def _say(self, interaction: discord.Interaction, key: str, **params: object) -> None:
        locale = await self.bot.services.guilds.get_locale(interaction.guild_id or 0)
        await interaction.response.send_message(
            translator.t(locale, key, **params),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @autodelete.command(name="add", description=d("cmd.autodelete.add.desc"))
    @app_commands.describe(
        channel=d("cmd.arg.channel.desc"),
        age=d("cmd.arg.ad_age.desc"),
        keep_pinned=d("cmd.arg.ad_keep_pinned.desc"),
        confirm=d("cmd.arg.ad_confirm.desc"),
    )
    async def add(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        age: str,
        keep_pinned: bool = True,
        confirm: bool = False,
    ) -> None:
        guild = ensure_guild(interaction)
        minutes = parse_duration(age) // 60
        if not MIN_AGE_MINUTES <= minutes <= MAX_AGE_MINUTES:
            raise UserError(
                "error.autodelete_age",
                min=format_duration(MIN_AGE_MINUTES * 60),
                max=format_duration(MAX_AGE_MINUTES * 60),
            )
        have = channel.permissions_for(guild.me)
        missing = [name for name in NEEDS if not getattr(have, name)]
        if missing:
            raise UserError(
                "error.missing_bot_permissions",
                permissions=", ".join(n.replace("_", " ").title() for n in missing),
            )
        if not confirm:
            # Löschen ist endgültig: erst die Folge nennen, dann mit confirm:True bestätigen lassen.
            raise UserError(
                "error.autodelete_confirm",
                channel=channel.mention,
                age=format_duration(minutes * 60),
            )
        current = await self.bot.services.autodelete.settings(guild.id)
        data = current.model_dump()
        data["enabled"] = True
        data["rules"] = [
            *(r for r in data["rules"] if r["channel_id"] != channel.id),
            {"channel_id": channel.id, "max_age_minutes": minutes, "keep_pinned": keep_pinned},
        ]
        await self._save(guild.id, data)
        await self._say(
            interaction,
            "autodelete.added",
            channel=channel.mention,
            age=format_duration(minutes * 60),
        )

    @autodelete.command(name="remove", description=d("cmd.autodelete.remove.desc"))
    @app_commands.describe(channel=d("cmd.arg.channel.desc"))
    async def remove(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        guild = ensure_guild(interaction)
        current = await self.bot.services.autodelete.settings(guild.id)
        data = current.model_dump()
        kept = [r for r in data["rules"] if r["channel_id"] != channel.id]
        if len(kept) == len(data["rules"]):
            raise UserError("error.autodelete_no_rule", channel=channel.mention)
        data["rules"] = kept
        await self._save(guild.id, data)
        await self._say(interaction, "autodelete.removed", channel=channel.mention)

    @autodelete.command(name="list", description=d("cmd.autodelete.list.desc"))
    async def list_(self, interaction: discord.Interaction) -> None:
        guild = ensure_guild(interaction)
        s = await self.bot.services.autodelete.settings(guild.id)
        locale = await self.bot.services.guilds.get_locale(guild.id)
        t = translator.t
        if not s.rules:
            await self._say(interaction, "autodelete.list_empty")
            return
        lines = [
            t(
                locale,
                "autodelete.list_line",
                channel=f"<#{r.channel_id}>",
                age=format_duration(r.max_age_minutes * 60),
                pinned=t(locale, "welcome.on") if r.keep_pinned else t(locale, "welcome.off"),
            )
            for r in s.rules
        ]
        head = t(locale, "autodelete.list_on" if s.enabled else "autodelete.list_off")
        await interaction.response.send_message(
            f"{head}\n" + "\n".join(lines),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @autodelete.command(name="on", description=d("cmd.autodelete.on.desc"))
    async def on(self, interaction: discord.Interaction) -> None:
        await self._toggle(interaction, True)

    @autodelete.command(name="off", description=d("cmd.autodelete.off.desc"))
    async def off(self, interaction: discord.Interaction) -> None:
        await self._toggle(interaction, False)

    async def _toggle(self, interaction: discord.Interaction, enabled: bool) -> None:
        guild = ensure_guild(interaction)
        current = await self.bot.services.autodelete.settings(guild.id)
        if enabled and not current.rules:
            raise UserError("error.autodelete_no_rules")
        await self._save(guild.id, {**current.model_dump(), "enabled": enabled})
        await self._say(interaction, "autodelete.on" if enabled else "autodelete.off")


async def setup(bot: GGBot) -> None:
    await bot.add_cog(AutodeleteCog(bot))
