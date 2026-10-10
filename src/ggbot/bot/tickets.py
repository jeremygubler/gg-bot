"""Tickets auf Discord: Komponenten, Anlegen, Schliessen. Die Regeln stehen in `core.tickets`."""

import asyncio
import io
from datetime import UTC, datetime, timedelta
from typing import Any

import discord

from ggbot.bot.panels import component_error
from ggbot.core import messages
from ggbot.core.errors import UserError
from ggbot.core.i18n import translator
from ggbot.core.logging import get_logger
from ggbot.core.services.tickets import (
    WELCOME_FIELDS,
    CategoryView,
    TicketPanelView,
    TicketView,
)
from ggbot.core.settings.schema import GeneralSettings
from ggbot.core.tickets import rules
from ggbot.core.tickets.transcript import MAX_MESSAGES, TranscriptMessage, render_transcript

log = get_logger(__name__)

OPEN_TEMPLATE = r"gg:tkopen:(?P<cat>\d+)"
CONTROL_TEMPLATE = r"gg:tk:(?P<action>close|claim)"
FEEDBACK_TEMPLATE = r"gg:tkfb:(?P<guild>\d+):(?P<ticket>\d+):(?P<rating>[1-5])"
OPEN_COOLDOWN_SECONDS = 5
DELETE_JOB = "ticket.delete_channel"
EMPTY_TRANSCRIPT_NOTE = "channel deleted"


def open_id(category_id: int) -> str:
    return f"gg:tkopen:{category_id}"


def feedback_id(guild_id: int, ticket_id: int, rating: int) -> str:
    return f"gg:tkfb:{guild_id}:{ticket_id}:{rating}"


def _t(locale: str, key: str, **params: object) -> str:
    return translator.t(locale, key, **params)


async def _locale(bot: Any, guild_id: int | None) -> str:
    return str(await bot.services.guilds.get_locale(guild_id))


# --- Komponenten ---


class OpenButton(discord.ui.DynamicItem[discord.ui.Button[Any]], template=OPEN_TEMPLATE):
    def __init__(
        self, category_id: int, *, label: str = "Ticket", emoji: str | None = None
    ) -> None:
        super().__init__(
            discord.ui.Button(
                style=discord.ButtonStyle.primary,
                label=label[:80],
                emoji=discord.PartialEmoji.from_str(emoji) if emoji else None,
                custom_id=open_id(category_id),
            )
        )
        self.category_id = category_id

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: Any, /
    ) -> "OpenButton":
        return cls(int(match["cat"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        await handle_open_click(interaction, self.category_id)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await component_error(interaction, error)


class TicketControl(discord.ui.DynamicItem[discord.ui.Button[Any]], template=CONTROL_TEMPLATE):
    def __init__(self, action: str, *, label: str = "") -> None:
        super().__init__(
            discord.ui.Button(
                style=discord.ButtonStyle.danger
                if action == "close"
                else discord.ButtonStyle.success,
                label=label or action,
                custom_id=f"gg:tk:{action}",
            )
        )
        self.action = action

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: Any, /
    ) -> "TicketControl":
        return cls(match["action"])

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.action == "close":
            await handle_close_click(interaction)
        else:
            await handle_claim(interaction)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await component_error(interaction, error)


class FeedbackButton(discord.ui.DynamicItem[discord.ui.Button[Any]], template=FEEDBACK_TEMPLATE):
    def __init__(self, guild_id: int, ticket_id: int, rating: int) -> None:
        super().__init__(
            discord.ui.Button(
                style=discord.ButtonStyle.secondary,
                label="★" * rating,
                custom_id=feedback_id(guild_id, ticket_id, rating),
            )
        )
        self.guild_id, self.ticket_id, self.rating = guild_id, ticket_id, rating

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Item[Any], match: Any, /
    ) -> "FeedbackButton":
        return cls(int(match["guild"]), int(match["ticket"]), int(match["rating"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        bot: Any = interaction.client
        await bot.services.tickets.rate(
            self.guild_id, self.ticket_id, interaction.user.id, self.rating
        )
        locale = await _locale(bot, self.guild_id)
        # Die Knöpfe verschwinden, damit niemand zweimal bewertet.
        await interaction.response.edit_message(
            content=_t(locale, "ticket.feedback_thanks", rating="★" * self.rating), view=None
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await component_error(interaction, error)


class QuestionsModal(discord.ui.Modal):
    def __init__(self, category: CategoryView, title: str) -> None:
        super().__init__(title=title[:45])
        self.category_id = category.id
        self.questions = category.questions
        self.inputs = [
            discord.ui.TextInput[Any](
                label=q, style=discord.TextStyle.paragraph, max_length=rules.MAX_ANSWER_LENGTH
            )
            for q in self.questions
        ]
        for field in self.inputs:
            self.add_item(field)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        answers = [(q, field.value) for q, field in zip(self.questions, self.inputs, strict=True)]
        await open_ticket(interaction, self.category_id, answers)

    async def on_error(  # type: ignore[override]  # Modal ruft on_error(interaction, error) auf
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await component_error(interaction, error)


class CloseModal(discord.ui.Modal):
    def __init__(self, title: str, label: str) -> None:
        super().__init__(title=title[:45])
        self.reason = discord.ui.TextInput[Any](
            label=label[:45], required=False, max_length=300, style=discord.TextStyle.short
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await close_from_interaction(interaction, self.reason.value or None)

    async def on_error(  # type: ignore[override]  # Modal ruft on_error(interaction, error) auf
        self, interaction: discord.Interaction, error: Exception
    ) -> None:
        await component_error(interaction, error)


# --- Panel-Nachricht ---


def panel_embed(panel: TicketPanelView, color: int) -> discord.Embed:
    lines = [
        f"{c.emoji + ' ' if c.emoji else ''}**{c.name}**"
        + (f" – {c.description}" if c.description else "")
        for c in panel.categories
    ]
    parts = [p for p in (panel.description, "\n".join(lines)) if p]
    return discord.Embed(title=panel.title, description="\n\n".join(parts)[:4000], color=color)


def panel_view(panel: TicketPanelView) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for category in panel.categories:
        view.add_item(OpenButton(category.id, label=category.name, emoji=category.emoji))
    return view


async def embed_color(bot: Any, guild_id: int) -> int:
    return int((await bot.services.settings.get(guild_id, GeneralSettings)).embed_color)


# --- Anlegen ---


def _allowed(
    roles: list[discord.Role], user: discord.Member | None = None
) -> discord.AllowedMentions:
    return discord.AllowedMentions(
        everyone=False, roles=roles, users=[user] if user else False, replied_user=False
    )


async def handle_open_click(interaction: discord.Interaction, category_id: int) -> None:
    bot: Any = interaction.client
    guild = interaction.guild
    if guild is None or not isinstance(interaction.user, discord.Member):
        raise UserError("error.guild_only")
    category = await bot.services.tickets.get_category(guild.id, category_id)
    # Früh prüfen, damit das Formular nicht umsonst ausgefüllt wird.
    settings = await bot.services.tickets.settings(guild.id)
    open_now = [
        t for t in await bot.services.tickets.open_tickets(guild.id)
        if t.creator_id == interaction.user.id
    ]  # fmt: skip
    if len(open_now) >= settings.max_open_per_user:
        raise UserError("error.ticket_limit", limit=settings.max_open_per_user)
    if category.questions:
        locale = await _locale(bot, guild.id)
        await interaction.response.send_modal(
            QuestionsModal(category, _t(locale, "ticket.modal_title", category=category.name))
        )
        return
    await open_ticket(interaction, category_id, [])


async def open_ticket(
    interaction: discord.Interaction, category_id: int, answers: list[tuple[str, str]]
) -> None:
    bot: Any = interaction.client
    services = bot.services
    guild = interaction.guild
    member = interaction.user
    if guild is None or not isinstance(member, discord.Member):
        raise UserError("error.guild_only")
    await services.cooldown.ensure(
        "ticketopen", f"{guild.id}:{member.id}", seconds=OPEN_COOLDOWN_SECONDS
    )
    await interaction.response.defer(ephemeral=True)
    locale = await _locale(bot, guild.id)
    settings = await services.tickets.settings(guild.id)
    reservation = await services.tickets.reserve(guild.id, member.id, category_id)
    category = reservation.category

    support_roles = [r for rid in category.support_role_ids if (r := guild.get_role(rid))]
    visible = discord.PermissionOverwrite(
        view_channel=True,
        send_messages=True,
        read_message_history=True,
        attach_files=True,
        embed_links=True,
    )
    overwrites: dict[
        discord.Role | discord.Member | discord.Object, discord.PermissionOverwrite
    ] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        member: visible,
        guild.me: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            embed_links=True,
            attach_files=True,
            manage_channels=True,
            manage_messages=True,
        ),
    }
    for role in support_roles:
        overwrites[role] = visible
    parent = guild.get_channel(category.parent_id) if category.parent_id else None
    name = rules.channel_name(
        settings.name_template,
        number=reservation.number,
        category=category.name,
        user=member.name,
    )
    try:
        channel = await guild.create_text_channel(
            name,
            category=parent if isinstance(parent, discord.CategoryChannel) else None,
            overwrites=overwrites,
            topic=f"Ticket #{reservation.number} · {member.id}",
            reason=f"Ticket #{reservation.number}",
        )
    except discord.Forbidden as exc:
        raise UserError("error.ticket_create_forbidden") from exc
    except discord.HTTPException as exc:
        log.warning("ticket_channel_failed", guild_id=guild.id, status=exc.status)
        raise UserError("error.ticket_create_failed") from exc

    try:
        ticket = await services.tickets.register(
            guild.id,
            number=reservation.number,
            creator_id=member.id,
            channel_id=channel.id,
            category=category,
            answers=answers,
        )
    except Exception:
        # Ohne Eintrag wäre der Kanal ein Ticket, das niemand schliessen kann.
        try:
            await channel.delete(reason="Ticket konnte nicht angelegt werden")
        except discord.HTTPException:
            log.exception("ticket_cleanup_failed", guild_id=guild.id, channel_id=channel.id)
        raise

    await _send_welcome(
        bot, channel, member, ticket, category, support_roles, settings.ping_support, locale
    )
    await interaction.followup.send(
        _t(locale, "ticket.created", channel=channel.mention), ephemeral=True
    )


async def _send_welcome(
    bot: Any,
    channel: discord.TextChannel,
    member: discord.Member,
    ticket: TicketView,
    category: CategoryView,
    support_roles: list[discord.Role],
    ping: bool,
    locale: str,
) -> None:
    template = category.welcome_message or _t(locale, "ticket.default_welcome")
    text = messages.render_template(
        template, WELCOME_FIELDS, user=member.mention, category=category.name, number=ticket.number
    )
    embed = discord.Embed(
        title=_t(locale, "ticket.welcome_title", number=ticket.number, category=category.name),
        description=text,
        color=await embed_color(bot, ticket.guild_id),
    )
    for question, answer in ticket.answers:
        embed.add_field(name=question[:256], value=(answer or "—")[:1024], inline=False)
    view = discord.ui.View(timeout=None)
    view.add_item(TicketControl("close", label=_t(locale, "ticket.btn_close")))
    view.add_item(TicketControl("claim", label=_t(locale, "ticket.btn_claim")))
    mentions = " ".join(
        [member.mention, *(r.mention for r in support_roles)] if ping else [member.mention]
    )
    try:
        await channel.send(
            mentions,
            embed=embed,
            view=view,
            allowed_mentions=_allowed(support_roles if ping else [], member),
        )
    except discord.HTTPException:
        log.warning("ticket_welcome_failed", channel_id=channel.id)


# --- Berechtigung im Ticket ---


def _roles(member: discord.Member) -> set[int]:
    return {r.id for r in member.roles}


def is_manager(member: discord.Member) -> bool:
    return bool(member.guild_permissions.manage_guild or member.guild_permissions.administrator)


def ensure_support(member: discord.Member, ticket: TicketView) -> None:
    if not rules.can_manage(_roles(member), ticket.support_role_ids, is_manager=is_manager(member)):
        raise UserError("error.ticket_support_only")


# --- Aktionen aus Interaktionen ---


async def _ticket_here(
    interaction: discord.Interaction,
) -> tuple[Any, discord.Guild, discord.Member, TicketView]:
    bot: Any = interaction.client
    guild = interaction.guild
    member = interaction.user
    if guild is None or not isinstance(member, discord.Member):
        raise UserError("error.guild_only")
    ticket = await bot.services.tickets.require_channel(guild.id, interaction.channel_id or 0)
    return bot, guild, member, ticket


async def handle_claim(interaction: discord.Interaction) -> None:
    bot, guild, member, ticket = await _ticket_here(interaction)
    ensure_support(member, ticket)
    await bot.services.tickets.claim(guild.id, ticket.channel_id, member.id)
    locale = await _locale(bot, guild.id)
    await interaction.response.send_message(
        _t(locale, "ticket.claimed", user=member.mention),
        allowed_mentions=discord.AllowedMentions.none(),
    )


async def handle_close_click(interaction: discord.Interaction) -> None:
    bot, guild, member, ticket = await _ticket_here(interaction)
    await _ensure_may_close(bot, guild, member, ticket)
    locale = await _locale(bot, guild.id)
    await interaction.response.send_modal(
        CloseModal(_t(locale, "ticket.close_modal"), _t(locale, "ticket.close_reason"))
    )


async def _ensure_may_close(
    bot: Any, guild: discord.Guild, member: discord.Member, ticket: TicketView
) -> None:
    if not ticket.is_open:
        raise UserError("error.ticket_closed")
    settings = await bot.services.tickets.settings(guild.id)
    if not rules.can_close(
        member.id,
        ticket.creator_id,
        _roles(member),
        ticket.support_role_ids,
        is_manager=is_manager(member),
        creators_may_close=settings.creators_may_close,
    ):
        raise UserError("error.ticket_cannot_close")


async def close_from_interaction(interaction: discord.Interaction, reason: str | None) -> None:
    bot, guild, member, ticket = await _ticket_here(interaction)
    await _ensure_may_close(bot, guild, member, ticket)
    await interaction.response.defer(ephemeral=True)
    await close_ticket(bot, guild, ticket, member.id, reason)
    locale = await _locale(bot, guild.id)
    await interaction.followup.send(_t(locale, "ticket.closing"), ephemeral=True)


# --- Schliessen ---


async def collect_messages(channel: discord.TextChannel) -> tuple[list[TranscriptMessage], bool]:
    """Der Verlauf, älteste zuerst; `True`, wenn er über der Grenze lag und gekürzt wurde."""
    collected: list[TranscriptMessage] = []
    truncated = False
    async for message in channel.history(limit=MAX_MESSAGES + 1, oldest_first=True):
        if len(collected) >= MAX_MESSAGES:
            truncated = True
            break
        collected.append(
            TranscriptMessage(
                author_id=message.author.id,
                author_name=getattr(message.author, "display_name", str(message.author)),
                at=message.created_at,
                content=message.content or "",
                attachments=tuple((a.filename, a.url) for a in message.attachments),
                embed_count=len(message.embeds),
                is_bot=message.author.bot,
            )
        )
    return collected, truncated


async def close_ticket(
    bot: Any, guild: discord.Guild, ticket: TicketView, closer_id: int, reason: str | None
) -> TicketView:
    services = bot.services
    locale = await _locale(bot, guild.id)
    channel = guild.get_channel(ticket.channel_id)
    history: list[TranscriptMessage] = []
    truncated = False
    if isinstance(channel, discord.TextChannel):
        history, truncated = await collect_messages(channel)
    title = _t(
        locale, "ticket.transcript_title", number=ticket.number, category=ticket.category_name
    )
    meta = _t(
        locale,
        "ticket.transcript_meta",
        creator=str(ticket.creator_id),
        created=f"{ticket.created_at:%Y-%m-%d %H:%M} UTC",
        count=len(history),
    )
    html = await asyncio.to_thread(
        render_transcript,
        history,
        title=title,
        meta=meta,
        truncated_label=_t(locale, "ticket.transcript_truncated", limit=MAX_MESSAGES),
        attachment_label=_t(locale, "ticket.transcript_attachment"),
        embed_label=_t(locale, "ticket.transcript_embeds"),
        truncated=truncated,
    )
    closed: TicketView = await services.tickets.close(
        guild.id,
        ticket.channel_id,
        closed_by=closer_id,
        reason=reason,
        html=html,
        message_count=len(history),
    )
    settings = await services.tickets.settings(guild.id)
    filename = f"ticket-{closed.number:04d}.html"
    await _announce_closed(bot, guild, channel, closed, html, filename, settings, locale)
    await services.scheduler.enqueue(
        guild.id,
        DELETE_JOB,
        datetime.now(UTC) + timedelta(seconds=settings.close_delay_seconds),
        {"channel_id": closed.channel_id},
    )
    return closed


def html_file(html: str, filename: str) -> discord.File:
    return discord.File(io.BytesIO(html.encode("utf-8")), filename=filename)


async def _announce_closed(
    bot: Any,
    guild: discord.Guild,
    channel: object,
    ticket: TicketView,
    html: str,
    filename: str,
    settings: Any,
    locale: str,
) -> None:
    color = await embed_color(bot, guild.id)
    none = discord.AllowedMentions.none()
    reason = ticket.close_reason or _t(locale, "ticket.no_reason")
    if isinstance(channel, discord.TextChannel):
        try:
            await channel.send(
                _t(
                    locale,
                    "ticket.closed_notice",
                    user=f"<@{ticket.closed_by}>",
                    seconds=settings.close_delay_seconds,
                ),
                allowed_mentions=none,
            )
        except discord.HTTPException:
            log.warning("ticket_notice_failed", channel_id=ticket.channel_id)

    embed = discord.Embed(
        title=_t(locale, "ticket.log_title", number=ticket.number, category=ticket.category_name),
        color=color,
    )
    embed.add_field(name=_t(locale, "ticket.log_creator"), value=f"<@{ticket.creator_id}>")
    embed.add_field(name=_t(locale, "ticket.log_closed_by"), value=f"<@{ticket.closed_by}>")
    if ticket.claimed_by:
        embed.add_field(name=_t(locale, "ticket.log_claimed"), value=f"<@{ticket.claimed_by}>")
    embed.add_field(name=_t(locale, "ticket.log_reason"), value=reason[:1000], inline=False)

    log_channel = guild.get_channel(settings.log_channel_id) if settings.log_channel_id else None
    if isinstance(log_channel, discord.TextChannel):
        try:
            await log_channel.send(
                embed=embed, file=html_file(html, filename), allowed_mentions=none
            )
        except discord.HTTPException:
            log.warning("ticket_log_failed", guild_id=guild.id, channel_id=log_channel.id)

    if settings.dm_transcript or settings.feedback:
        await _dm_creator(bot, guild, ticket, embed, html, filename, settings, locale)


async def _dm_creator(
    bot: Any,
    guild: discord.Guild,
    ticket: TicketView,
    embed: discord.Embed,
    html: str,
    filename: str,
    settings: Any,
    locale: str,
) -> None:
    view: discord.ui.View | Any = discord.utils.MISSING
    if settings.feedback:
        view = discord.ui.View(timeout=None)
        for rating in range(1, 6):
            view.add_item(FeedbackButton(guild.id, ticket.id, rating))
    try:
        user = bot.get_user(ticket.creator_id) or await bot.fetch_user(ticket.creator_id)
        await user.send(
            _t(locale, "ticket.dm_closed", number=ticket.number, server=guild.name)
            + ("\n" + _t(locale, "ticket.dm_rate") if settings.feedback else ""),
            embed=embed,
            file=html_file(html, filename) if settings.dm_transcript else discord.utils.MISSING,
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
        log.debug("ticket_dm_closed", guild_id=guild.id, user_id=ticket.creator_id)
