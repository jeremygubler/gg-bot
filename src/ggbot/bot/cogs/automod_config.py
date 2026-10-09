"""/automod: Regeln einschalten und einstellen (die Werte liegen in den Settings)."""

from collections.abc import Callable
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands
from pydantic import ValidationError

from ggbot.bot.checks import ensure_guild
from ggbot.bot.client import GGBot
from ggbot.bot.i18n import d
from ggbot.core.automod.rules import (
    extract_domains,
    find_invites,
    find_word_matches,
    normalize_domain,
    validate_entry,
)
from ggbot.core.duration import format_duration, parse_duration
from ggbot.core.errors import UserError
from ggbot.core.i18n import translator
from ggbot.core.settings.automod import JOIN_RULES, MESSAGE_RULES, AutomodSettings

Mutation = Callable[[dict[str, Any]], str | None]


def _choices(*names: str) -> list[app_commands.Choice[str]]:
    return [app_commands.Choice(name=name, value=name) for name in names]


ALL_RULES = _choices(*MESSAGE_RULES, *JOIN_RULES)
MESSAGE_RULE_CHOICES = _choices(*MESSAGE_RULES)
EXEMPT_CHOICES = _choices("all", *MESSAGE_RULES)
WORD_KINDS = _choices("word", "wildcard", "regex")
LINK_MODES = _choices("blacklist", "whitelist")
JOIN_ACTIONS = _choices("kick", "ban", "timeout")
MAX_LIST_CHARS = 3800


def describe_action(action: dict[str, Any], locale: str) -> str:
    t = translator.t
    parts = []
    if action["delete"]:
        parts.append(t(locale, "automod.action.delete"))
    if action["warn"]:
        parts.append(t(locale, "automod.action.warn"))
    if action["timeout_seconds"]:
        parts.append(
            f"{t(locale, 'automod.action.timeout')} {format_duration(action['timeout_seconds'])}"
        )
    return " + ".join(parts)


def describe_join_action(action: dict[str, Any]) -> str:
    if action["action"] == "timeout":
        return f"timeout {format_duration(action['timeout_seconds'])}"
    return str(action["action"])


def summarize(name: str, rule: dict[str, Any], locale: str) -> str:
    """Eine Zeile pro Regel für /automod status."""
    match name:
        case "spam":
            text = f"{rule['messages']} / {rule['seconds']} s"
        case "duplicates":
            text = f"{rule['count']}× / {rule['seconds']} s"
        case "caps":
            text = f"> {rule['max_percent']} %, ≥ {rule['min_length']}"
        case "mentions":
            text = f"> {rule['max_mentions']}"
        case "invites":
            text = f"{len(rule['allowed_codes'])} ✓"
        case "links":
            text = f"{rule['mode']} ({len(rule['domains'])})"
        case "words":
            text = str(len(rule["entries"]))
        case "raid":
            text = (
                f"{rule['joins']} / {rule['seconds']} s → {describe_join_action(rule['action'])}, "
                f"{rule['mode_minutes']} min"
            )
        case "accountage":
            text = f"< {rule['min_days']} d → {describe_join_action(rule['action'])}"
        case _:
            text = ""
    if name in MESSAGE_RULES:
        text += " · " + describe_action(rule["action"], locale)
    return text


class AutomodConfigCog(commands.Cog):
    def __init__(self, bot: GGBot) -> None:
        self.bot = bot

    automod = app_commands.Group(
        name="automod",
        description=d("cmd.automod.desc"),
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )
    exempt = app_commands.Group(
        name="exempt", description=d("cmd.automod.exempt.desc"), parent=automod
    )
    invite = app_commands.Group(
        name="invite", description=d("cmd.automod.invite.desc"), parent=automod
    )
    link = app_commands.Group(name="link", description=d("cmd.automod.link.desc"), parent=automod)
    word = app_commands.Group(name="word", description=d("cmd.automod.word.desc"), parent=automod)
    raid = app_commands.Group(name="raid", description=d("cmd.automod.raid.desc"), parent=automod)

    # --- Hilfen ---

    async def _locale(self, guild_id: int) -> str:
        return await self.bot.services.guilds.get_locale(guild_id)

    async def _edit(
        self, interaction: discord.Interaction, mutate: Mutation, reply_key: str, **params: object
    ) -> None:
        """Einstellungen laden, ändern, prüfen, speichern und die Antwort schicken.

        `mutate` arbeitet auf dem Wörterbuch und kann einen Zusatz für die Antwort liefern.
        Die Prüfung läuft über dieselben Pydantic-Modelle wie beim Laden, es gibt also keine
        zweite Wahrheit darüber, was gültig ist.
        """
        guild = ensure_guild(interaction)
        services = self.bot.services
        current = await services.settings.get(guild.id, AutomodSettings)
        data = current.model_dump()
        extra = mutate(data)
        try:
            updated = AutomodSettings.model_validate(data)
        except ValidationError as exc:
            raise UserError("error.automod_invalid", detail=_first_error(exc)) from exc
        await services.settings.save(guild.id, updated)
        services.automod.invalidate(guild.id)
        locale = await self._locale(guild.id)
        text = translator.t(locale, reply_key, **params)
        if extra:
            text += "\n" + extra
        await interaction.response.send_message(text, ephemeral=True)

    # --- Übersicht ---

    @automod.command(name="status", description=d("cmd.automod.status.desc"))
    async def status(self, interaction: discord.Interaction) -> None:
        guild = ensure_guild(interaction)
        locale = await self._locale(guild.id)
        t = translator.t
        settings = await self.bot.services.settings.get(guild.id, AutomodSettings)
        data = settings.model_dump()
        lines = []
        for name in (*MESSAGE_RULES, *JOIN_RULES):
            rule = data[name]
            mark = "✅" if rule["enabled"] else "⬜"
            lines.append(f"{mark} **{name}** – {summarize(name, rule, locale)}")
        embed = discord.Embed(title=t(locale, "automod.status.title"), description="\n".join(lines))
        intents = self.bot.intents
        embed.add_field(
            name=t(locale, "automod.status.intents"),
            value=(
                f"{'✅' if intents.message_content else '❌'} Message Content\n"
                f"{'✅' if intents.members else '❌'} Server Members"
            ),
        )
        raid_active = await self.bot.services.automod.raid_active(guild.id)
        embed.add_field(
            name=t(locale, "automod.status.raid"),
            value=t(locale, "automod.status.raid_on" if raid_active else "automod.status.raid_off"),
        )
        log_channel = f"<#{settings.log_channel_id}>" if settings.log_channel_id else "–"
        embed.add_field(name=t(locale, "automod.status.log"), value=log_channel)
        embed.set_footer(
            text=t(
                locale,
                "automod.status.footer",
                channels=len(settings.exempt_channels),
                roles=len(settings.exempt_roles),
                moderators=t(locale, "automod.yes" if settings.exempt_moderators else "automod.no"),
            )
        )
        missing = [
            key
            for key, present in (
                ("automod.status.need_message_content", intents.message_content),
                ("automod.status.need_members", intents.members),
            )
            if not present
        ]
        if missing:
            embed.add_field(
                name="⚠️", value="\n".join(t(locale, key) for key in missing), inline=False
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @automod.command(name="stats", description=d("cmd.automod.stats.desc"))
    @app_commands.describe(days=d("cmd.arg.days.desc"))
    async def stats(
        self, interaction: discord.Interaction, days: app_commands.Range[int, 1, 30] = 7
    ) -> None:
        guild = ensure_guild(interaction)
        locale = await self._locale(guild.id)
        counts = await self.bot.services.automod.stats(guild.id, days)
        if not counts:
            text = translator.t(locale, "automod.stats.empty", days=days)
        else:
            rows = sorted(counts.items(), key=lambda item: -item[1])
            lines = [f"**{name}**: {count}" for name, count in rows]
            text = translator.t(locale, "automod.stats.title", days=days) + "\n" + "\n".join(lines)
        await interaction.response.send_message(text, ephemeral=True)

    # --- Ein-/Ausschalten und Aktion ---

    async def _toggle(self, interaction: discord.Interaction, rule: str, enabled: bool) -> None:
        intents = self.bot.intents
        if enabled and rule in MESSAGE_RULES and not intents.message_content:
            raise UserError("error.automod_needs_message_content")
        if enabled and rule in JOIN_RULES and not intents.members:
            raise UserError("error.automod_needs_members")

        def mutate(data: dict[str, Any]) -> None:
            data[rule]["enabled"] = enabled

        key = "automod.enabled" if enabled else "automod.disabled"
        await self._edit(interaction, mutate, key, rule=rule)

    @automod.command(name="enable", description=d("cmd.automod.enable.desc"))
    @app_commands.describe(rule=d("cmd.arg.rule.desc"))
    @app_commands.choices(rule=ALL_RULES)
    async def enable(
        self, interaction: discord.Interaction, rule: app_commands.Choice[str]
    ) -> None:
        await self._toggle(interaction, rule.value, True)

    @automod.command(name="disable", description=d("cmd.automod.disable.desc"))
    @app_commands.describe(rule=d("cmd.arg.rule.desc"))
    @app_commands.choices(rule=ALL_RULES)
    async def disable(
        self, interaction: discord.Interaction, rule: app_commands.Choice[str]
    ) -> None:
        await self._toggle(interaction, rule.value, False)

    @automod.command(name="action", description=d("cmd.automod.action.desc"))
    @app_commands.describe(
        rule=d("cmd.arg.rule.desc"),
        delete=d("cmd.arg.delete.desc"),
        warn=d("cmd.arg.warn.desc"),
        timeout_duration=d("cmd.arg.timeout_duration.desc"),
    )
    @app_commands.choices(rule=MESSAGE_RULE_CHOICES)
    async def action(
        self,
        interaction: discord.Interaction,
        rule: app_commands.Choice[str],
        delete: bool = True,
        warn: bool = False,
        timeout_duration: str | None = None,
    ) -> None:
        off = timeout_duration is None or timeout_duration.lower() == "off"
        seconds = None if off or timeout_duration is None else parse_duration(timeout_duration)

        def mutate(data: dict[str, Any]) -> None:
            data[rule.value]["action"] = {
                "delete": delete,
                "warn": warn,
                "timeout_seconds": seconds,
            }

        await self._edit(interaction, mutate, "automod.action_set", rule=rule.value)

    # --- Zahlenwerte der Regeln ---

    @automod.command(name="spam", description=d("cmd.automod.spam.desc"))
    @app_commands.describe(messages=d("cmd.arg.messages.desc"), window=d("cmd.arg.window.desc"))
    async def spam(
        self,
        interaction: discord.Interaction,
        messages: app_commands.Range[int, 2, 30],
        window: app_commands.Range[int, 1, 60],
    ) -> None:
        def mutate(data: dict[str, Any]) -> None:
            data["spam"].update(messages=messages, seconds=window)

        await self._edit(interaction, mutate, "automod.spam_set", messages=messages, seconds=window)

    @automod.command(name="duplicates", description=d("cmd.automod.duplicates.desc"))
    @app_commands.describe(count=d("cmd.arg.count.desc"), window=d("cmd.arg.window.desc"))
    async def duplicates(
        self,
        interaction: discord.Interaction,
        count: app_commands.Range[int, 2, 20],
        window: app_commands.Range[int, 5, 300],
    ) -> None:
        def mutate(data: dict[str, Any]) -> None:
            data["duplicates"].update(count=count, seconds=window)

        await self._edit(interaction, mutate, "automod.duplicates_set", count=count, seconds=window)

    @automod.command(name="caps", description=d("cmd.automod.caps.desc"))
    @app_commands.describe(
        min_length=d("cmd.arg.min_length.desc"), percent=d("cmd.arg.percent.desc")
    )
    async def caps(
        self,
        interaction: discord.Interaction,
        min_length: app_commands.Range[int, 5, 200],
        percent: app_commands.Range[int, 30, 100],
    ) -> None:
        def mutate(data: dict[str, Any]) -> None:
            data["caps"].update(min_length=min_length, max_percent=percent)

        await self._edit(
            interaction, mutate, "automod.caps_set", min_length=min_length, percent=percent
        )

    @automod.command(name="mentions", description=d("cmd.automod.mentions.desc"))
    @app_commands.describe(maximum=d("cmd.arg.maximum.desc"))
    async def mentions(
        self, interaction: discord.Interaction, maximum: app_commands.Range[int, 2, 50]
    ) -> None:
        def mutate(data: dict[str, Any]) -> None:
            data["mentions"]["max_mentions"] = maximum

        await self._edit(interaction, mutate, "automod.mentions_set", maximum=maximum)

    @automod.command(name="log", description=d("cmd.automod.log.desc"))
    @app_commands.describe(channel=d("cmd.arg.log_channel.desc"))
    async def log(
        self, interaction: discord.Interaction, channel: discord.TextChannel | None = None
    ) -> None:
        def mutate(data: dict[str, Any]) -> None:
            data["log_channel_id"] = channel.id if channel else None

        key = "automod.log_set" if channel else "automod.log_off"
        await self._edit(interaction, mutate, key, channel=channel.mention if channel else "")

    @automod.command(name="moderators", description=d("cmd.automod.moderators.desc"))
    @app_commands.describe(exempt=d("cmd.arg.enabled.desc"))
    async def moderators(self, interaction: discord.Interaction, exempt: bool) -> None:
        def mutate(data: dict[str, Any]) -> None:
            data["exempt_moderators"] = exempt

        key = "automod.moderators_on" if exempt else "automod.moderators_off"
        await self._edit(interaction, mutate, key)

    # --- Ausnahmen ---

    async def _exempt(
        self,
        interaction: discord.Interaction,
        scope: str,
        field: str,
        target_id: int,
        mention: str,
        *,
        add: bool,
    ) -> None:
        def mutate(data: dict[str, Any]) -> None:
            holder = data if scope == "all" else data[scope]
            ids: list[int] = holder[field]
            if add and target_id not in ids:
                ids.append(target_id)
            if not add:
                holder[field] = [i for i in ids if i != target_id]

        key = "automod.exempt_added" if add else "automod.exempt_removed"
        await self._edit(interaction, mutate, key, target=mention, rule=scope)

    @exempt.command(name="channel_add", description=d("cmd.automod.exempt.channel_add.desc"))
    @app_commands.describe(rule=d("cmd.arg.rule.desc"), channel=d("cmd.arg.channel.desc"))
    @app_commands.choices(rule=EXEMPT_CHOICES)
    async def exempt_channel_add(
        self,
        interaction: discord.Interaction,
        rule: app_commands.Choice[str],
        channel: discord.abc.GuildChannel,
    ) -> None:
        await self._exempt(
            interaction, rule.value, "exempt_channels", channel.id, channel.mention, add=True
        )

    @exempt.command(name="channel_remove", description=d("cmd.automod.exempt.channel_remove.desc"))
    @app_commands.describe(rule=d("cmd.arg.rule.desc"), channel=d("cmd.arg.channel.desc"))
    @app_commands.choices(rule=EXEMPT_CHOICES)
    async def exempt_channel_remove(
        self,
        interaction: discord.Interaction,
        rule: app_commands.Choice[str],
        channel: discord.abc.GuildChannel,
    ) -> None:
        await self._exempt(
            interaction, rule.value, "exempt_channels", channel.id, channel.mention, add=False
        )

    @exempt.command(name="role_add", description=d("cmd.automod.exempt.role_add.desc"))
    @app_commands.describe(rule=d("cmd.arg.rule.desc"), role=d("cmd.arg.role.desc"))
    @app_commands.choices(rule=EXEMPT_CHOICES)
    async def exempt_role_add(
        self,
        interaction: discord.Interaction,
        rule: app_commands.Choice[str],
        role: discord.Role,
    ) -> None:
        await self._exempt(interaction, rule.value, "exempt_roles", role.id, role.mention, add=True)

    @exempt.command(name="role_remove", description=d("cmd.automod.exempt.role_remove.desc"))
    @app_commands.describe(rule=d("cmd.arg.rule.desc"), role=d("cmd.arg.role.desc"))
    @app_commands.choices(rule=EXEMPT_CHOICES)
    async def exempt_role_remove(
        self,
        interaction: discord.Interaction,
        rule: app_commands.Choice[str],
        role: discord.Role,
    ) -> None:
        await self._exempt(
            interaction, rule.value, "exempt_roles", role.id, role.mention, add=False
        )

    # --- Einladungen ---

    @invite.command(name="allow", description=d("cmd.automod.invite.allow.desc"))
    @app_commands.describe(code=d("cmd.arg.code.desc"))
    async def invite_allow(
        self, interaction: discord.Interaction, code: app_commands.Range[str, 2, 100]
    ) -> None:
        wanted = (find_invites(code) or [code.strip().lower()])[0]

        def mutate(data: dict[str, Any]) -> None:
            codes: list[str] = data["invites"]["allowed_codes"]
            if wanted not in codes:
                codes.append(wanted)

        await self._edit(interaction, mutate, "automod.invite_allowed", code=wanted)

    @invite.command(name="disallow", description=d("cmd.automod.invite.disallow.desc"))
    @app_commands.describe(code=d("cmd.arg.code.desc"))
    async def invite_disallow(
        self, interaction: discord.Interaction, code: app_commands.Range[str, 2, 100]
    ) -> None:
        wanted = (find_invites(code) or [code.strip().lower()])[0]

        def mutate(data: dict[str, Any]) -> None:
            data["invites"]["allowed_codes"] = [
                c for c in data["invites"]["allowed_codes"] if c != wanted
            ]

        await self._edit(interaction, mutate, "automod.invite_disallowed", code=wanted)

    # --- Links ---

    @link.command(name="mode", description=d("cmd.automod.link.mode.desc"))
    @app_commands.describe(mode=d("cmd.arg.mode.desc"))
    @app_commands.choices(mode=LINK_MODES)
    async def link_mode(
        self, interaction: discord.Interaction, mode: app_commands.Choice[str]
    ) -> None:
        def mutate(data: dict[str, Any]) -> None:
            data["links"]["mode"] = mode.value

        await self._edit(interaction, mutate, f"automod.link_mode_{mode.value}")

    @staticmethod
    def _domain(text: str) -> str:
        found = extract_domains(text) or ([normalize_domain(text)] if "." in text else [])
        if not found:
            raise UserError("error.automod_domain_invalid", value=text[:50])
        return found[0]

    @link.command(name="add", description=d("cmd.automod.link.add.desc"))
    @app_commands.describe(domain=d("cmd.arg.domain.desc"))
    async def link_add(
        self, interaction: discord.Interaction, domain: app_commands.Range[str, 3, 200]
    ) -> None:
        wanted = self._domain(domain)

        def mutate(data: dict[str, Any]) -> None:
            if wanted not in data["links"]["domains"]:
                data["links"]["domains"].append(wanted)

        await self._edit(interaction, mutate, "automod.link_added", domain=wanted)

    @link.command(name="remove", description=d("cmd.automod.link.remove.desc"))
    @app_commands.describe(domain=d("cmd.arg.domain.desc"))
    async def link_remove(
        self, interaction: discord.Interaction, domain: app_commands.Range[str, 3, 200]
    ) -> None:
        wanted = self._domain(domain)

        def mutate(data: dict[str, Any]) -> None:
            data["links"]["domains"] = [d_ for d_ in data["links"]["domains"] if d_ != wanted]

        await self._edit(interaction, mutate, "automod.link_removed", domain=wanted)

    @link.command(name="list", description=d("cmd.automod.link.list.desc"))
    async def link_list(self, interaction: discord.Interaction) -> None:
        guild = ensure_guild(interaction)
        locale = await self._locale(guild.id)
        settings = await self.bot.services.settings.get(guild.id, AutomodSettings)
        body = "\n".join(f"• `{domain}`" for domain in settings.links.domains)
        text = translator.t(locale, f"automod.link_mode_{settings.links.mode}")
        text += "\n" + (body or translator.t(locale, "automod.list_empty"))
        await interaction.response.send_message(text[:MAX_LIST_CHARS], ephemeral=True)

    # --- Wortfilter ---

    @word.command(name="add", description=d("cmd.automod.word.add.desc"))
    @app_commands.describe(kind=d("cmd.arg.kind.desc"), pattern=d("cmd.arg.pattern.desc"))
    @app_commands.choices(kind=WORD_KINDS)
    async def word_add(
        self,
        interaction: discord.Interaction,
        kind: app_commands.Choice[str],
        pattern: app_commands.Range[str, 1, 200],
    ) -> None:
        validate_entry(kind.value, pattern)  # liefert die genaue Fehlermeldung

        def mutate(data: dict[str, Any]) -> None:
            entry = {"type": kind.value, "pattern": pattern}
            if entry not in data["words"]["entries"]:
                data["words"]["entries"].append(entry)

        await self._edit(
            interaction, mutate, "automod.word_added", kind=kind.value, pattern=pattern
        )

    @word.command(name="remove", description=d("cmd.automod.word.remove.desc"))
    @app_commands.describe(pattern=d("cmd.arg.pattern.desc"))
    async def word_remove(
        self, interaction: discord.Interaction, pattern: app_commands.Range[str, 1, 200]
    ) -> None:
        def mutate(data: dict[str, Any]) -> None:
            entries = data["words"]["entries"]
            kept = [e for e in entries if e["pattern"].lower() != pattern.lower()]
            if len(kept) == len(entries):
                raise UserError("error.automod_word_not_found", pattern=pattern)
            data["words"]["entries"] = kept

        await self._edit(interaction, mutate, "automod.word_removed", pattern=pattern)

    @word.command(name="list", description=d("cmd.automod.word.list.desc"))
    async def word_list(self, interaction: discord.Interaction) -> None:
        guild = ensure_guild(interaction)
        locale = await self._locale(guild.id)
        settings = await self.bot.services.settings.get(guild.id, AutomodSettings)
        lines = [f"`{e.type}` {e.pattern}" for e in settings.words.entries]
        text = "\n".join(lines) or translator.t(locale, "automod.list_empty")
        await interaction.response.send_message(text[:MAX_LIST_CHARS], ephemeral=True)

    @word.command(name="test", description=d("cmd.automod.word.test.desc"))
    @app_commands.describe(sample=d("cmd.arg.sample.desc"))
    async def word_test(
        self, interaction: discord.Interaction, sample: app_commands.Range[str, 1, 500]
    ) -> None:
        guild = ensure_guild(interaction)
        locale = await self._locale(guild.id)
        settings = await self.bot.services.settings.get(guild.id, AutomodSettings)
        hits = find_word_matches(sample, [(e.type, e.pattern) for e in settings.words.entries])
        if hits:
            reply = (
                translator.t(locale, "automod.word_test_hit")
                + "\n"
                + "\n".join(f"• `{h}`" for h in hits)
            )
        else:
            reply = translator.t(locale, "automod.word_test_miss")
        await interaction.response.send_message(reply[:MAX_LIST_CHARS], ephemeral=True)

    # --- Anti-Raid und Konto-Alter ---

    @staticmethod
    def _join_action(action: str, timeout: str | None) -> dict[str, Any]:
        if action == "timeout" and timeout is None:
            raise UserError("error.escalation_duration_needed", action=action)
        if action != "timeout" and timeout is not None:
            raise UserError("error.escalation_duration_unexpected", action=action)
        return {"action": action, "timeout_seconds": parse_duration(timeout) if timeout else None}

    @raid.command(name="set", description=d("cmd.automod.raid.set.desc"))
    @app_commands.describe(
        joins=d("cmd.arg.joins.desc"),
        window=d("cmd.arg.window.desc"),
        action=d("cmd.arg.join_action.desc"),
        minutes=d("cmd.arg.minutes.desc"),
        timeout_duration=d("cmd.arg.timeout_duration.desc"),
    )
    @app_commands.choices(action=JOIN_ACTIONS)
    async def raid_set(
        self,
        interaction: discord.Interaction,
        joins: app_commands.Range[int, 2, 100],
        window: app_commands.Range[int, 2, 300],
        action: app_commands.Choice[str],
        minutes: app_commands.Range[int, 1, 1440] = 10,
        timeout_duration: str | None = None,
    ) -> None:
        join_action = self._join_action(action.value, timeout_duration)

        def mutate(data: dict[str, Any]) -> None:
            data["raid"].update(
                joins=joins, seconds=window, mode_minutes=minutes, action=join_action
            )

        await self._edit(
            interaction, mutate, "automod.raid_set", joins=joins, seconds=window, minutes=minutes
        )

    @raid.command(name="end", description=d("cmd.automod.raid.end.desc"))
    async def raid_end(self, interaction: discord.Interaction) -> None:
        guild = ensure_guild(interaction)
        locale = await self._locale(guild.id)
        ended = await self.bot.services.automod.end_raid(guild.id)
        key = "automod.raid_ended" if ended else "automod.raid_not_active"
        await interaction.response.send_message(translator.t(locale, key), ephemeral=True)

    @automod.command(name="accountage", description=d("cmd.automod.accountage.desc"))
    @app_commands.describe(
        days=d("cmd.arg.min_days.desc"),
        action=d("cmd.arg.join_action.desc"),
        timeout_duration=d("cmd.arg.timeout_duration.desc"),
    )
    @app_commands.choices(action=JOIN_ACTIONS)
    async def accountage(
        self,
        interaction: discord.Interaction,
        days: app_commands.Range[int, 1, 365],
        action: app_commands.Choice[str],
        timeout_duration: str | None = None,
    ) -> None:
        join_action = self._join_action(action.value, timeout_duration)

        def mutate(data: dict[str, Any]) -> None:
            data["accountage"].update(min_days=days, action=join_action)

        await self._edit(interaction, mutate, "automod.accountage_set", days=days)


def _first_error(exc: ValidationError) -> str:
    error = exc.errors()[0]
    where = ".".join(str(part) for part in error["loc"])
    return f"{where}: {error['msg']}"[:150]


async def setup(bot: GGBot) -> None:
    await bot.add_cog(AutomodConfigCog(bot))
