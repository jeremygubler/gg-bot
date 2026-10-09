"""Automod-Entscheidungen ohne Discord.

Der Service sagt, ob eine Nachricht oder ein Beitritt gegen eine Regel verstösst und was daraus
folgen soll. Ausführen (löschen, verwarnen, kicken) ist Sache des Cogs.
"""

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis

from ggbot.core.automod import rules
from ggbot.core.automod.text import collapse
from ggbot.core.db.engine import Database
from ggbot.core.db.repos import automod as repo
from ggbot.core.logging import get_logger
from ggbot.core.redis import key
from ggbot.core.settings.automod import (
    AutomodSettings,
    JoinAction,
    MessageRule,
    RuleAction,
)
from ggbot.core.settings.store import SettingsStore

log = get_logger(__name__)

# Wie oft dieselbe Regel denselben Nutzer *bestrafen* (verwarnen/timeouten) darf. Löschen passiert
# bei jeder Nachricht; sonst gäbe eine Spam-Welle von 20 Nachrichten 20 Verwarnungen.
PUNISH_COOLDOWN_SECONDS = 30
HITS_RETENTION = timedelta(days=30)
CLEANUP_INTERVAL_SECONDS = 3600
SETTINGS_TTL_SECONDS = 5.0
RAID_KEEP_SECONDS = 600

# Bei mehreren Treffern entscheidet die spezifischste Regel: "Wortfilter" sagt dem Moderator mehr
# als "Spam" und hat meist die schärfere Aktion.
PRIORITY = ("words", "invites", "links", "mentions", "caps", "duplicates", "spam")

MessageRef = tuple[int, int]  # (channel_id, message_id)


@dataclass(frozen=True)
class MessageContext:
    guild_id: int
    channel_id: int
    # Kategorie bzw. Eltern-Kanal bei Threads, damit Ausnahmen dafür greifen.
    parent_channel_id: int | None
    message_id: int
    user_id: int
    content: str
    role_ids: frozenset[int]
    mentioned_user_ids: frozenset[int]
    mentioned_role_ids: frozenset[int]
    can_manage_messages: bool
    # Bei Bearbeitungen zählt der Inhalt, aber nicht die Nachrichtenrate.
    is_edit: bool = False


@dataclass(frozen=True)
class Violation:
    rule: str
    action: RuleAction
    # Auslöser (Domain, Muster, Einladungscode …) für den Mod-Log, nicht für Nutzer.
    detail: str | None = None
    # Alle Nachrichten, die zum Verstoss gehören (bei Spam die ganze Welle), inkl. der aktuellen.
    delete: tuple[MessageRef, ...] = ()
    # False, wenn derselbe Nutzer für diese Regel gerade erst bestraft wurde.
    punish: bool = True


@dataclass(frozen=True)
class JoinContext:
    guild_id: int
    user_id: int
    is_bot: bool
    account_created_at: datetime


@dataclass(frozen=True)
class JoinDecision:
    rule: str  # "raid" | "accountage"
    action: JoinAction
    # Alle Nutzer, die jetzt die Aktion bekommen (beim Raid-Auslöser die ganze Welle).
    targets: tuple[int, ...]
    reason_params: dict[str, int] = field(default_factory=dict)
    # True nur beim Moment, in dem der Raid-Modus beginnt: dann gibt es eine Warnung.
    raid_started: bool = False


class AutomodService:
    def __init__(
        self,
        db: Database,
        settings: SettingsStore,
        redis: Redis,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._db = db
        self._settings_store = settings
        self._redis = redis
        self._clock = clock
        self._cache: dict[int, tuple[float, AutomodSettings]] = {}

    # --- Einstellungen mit kurzem Prozess-Cache ---

    async def settings(self, guild_id: int) -> AutomodSettings:
        """Pro Nachricht gefragt, deshalb zusätzlich zum Redis-Cache ein kurzer Prozess-Cache."""
        now = time.monotonic()
        cached = self._cache.get(guild_id)
        if cached is not None and cached[0] > now:
            return cached[1]
        settings = await self._settings_store.get(guild_id, AutomodSettings)
        self._cache[guild_id] = (now + SETTINGS_TTL_SECONDS, settings)
        return settings

    def invalidate(self, guild_id: int) -> None:
        self._cache.pop(guild_id, None)

    # --- Nachrichten ---

    async def evaluate_message(self, ctx: MessageContext) -> Violation | None:
        settings = await self.settings(ctx.guild_id)
        if not settings.any_message_rule():
            return None
        if self._globally_exempt(settings, ctx):
            return None

        # Die Zähler laufen für jede Nachricht, auch wenn eine andere Regel zuerst greift.
        counting = not ctx.is_edit
        burst = (
            await self._record_spam(settings, ctx) if counting and settings.spam.enabled else None
        )
        repeats = (
            await self._record_duplicate(settings, ctx)
            if counting and settings.duplicates.enabled
            else None
        )
        here: MessageRef = (ctx.channel_id, ctx.message_id)

        checks: list[tuple[str, str | None, tuple[MessageRef, ...]]] = []
        for name in PRIORITY:
            rule = settings.message_rule(name)
            if not rule.enabled or self._rule_exempt(rule, ctx):
                continue
            detail, refs = self._check(name, settings, ctx, burst, repeats)
            if detail is not None:
                checks.append((name, detail or None, refs or (here,)))
        if not checks:
            return None

        name, detail, refs = checks[0]
        action = settings.message_rule(name).action
        delete = tuple(dict.fromkeys((*refs, here)))
        if name in ("spam", "duplicates"):
            await self._forget(name, ctx, delete)
        punish = await self._may_punish(ctx.guild_id, ctx.user_id, name)
        return Violation(name, action, detail, delete, punish)

    def _check(
        self,
        name: str,
        settings: AutomodSettings,
        ctx: MessageContext,
        burst: list[MessageRef] | None,
        repeats: list[MessageRef] | None,
    ) -> tuple[str | None, tuple[MessageRef, ...]]:
        """(Auslöser oder None, zugehörige Nachrichten). Leerer Auslöser "" heisst: kein Detail."""
        content = ctx.content
        match name:
            case "words":
                matched = rules.find_word_matches(
                    content, [(e.type, e.pattern) for e in settings.words.entries]
                )
                return (matched[0], ()) if matched else (None, ())
            case "invites":
                bad = rules.disallowed_invites(content, settings.invites.allowed_codes)
                return (bad[0], ()) if bad else (None, ())
            case "links":
                bad = rules.blocked_domains(
                    content, mode=settings.links.mode, listed=settings.links.domains
                )
                return (bad[0], ()) if bad else (None, ())
            case "mentions":
                if rules.too_many_mentions(
                    set(ctx.mentioned_user_ids),
                    set(ctx.mentioned_role_ids),
                    maximum=settings.mentions.max_mentions,
                ):
                    count = len(ctx.mentioned_user_ids) + len(ctx.mentioned_role_ids)
                    return (str(count), ())
                return None, ()
            case "caps":
                if rules.too_many_caps(
                    content,
                    min_length=settings.caps.min_length,
                    max_percent=settings.caps.max_percent,
                ):
                    return "", ()
                return None, ()
            case "duplicates":
                if repeats is not None and len(repeats) >= settings.duplicates.count:
                    return "", tuple(repeats)
                return None, ()
            case "spam":
                if burst is not None and len(burst) >= settings.spam.messages:
                    return "", tuple(burst)
                return None, ()
        return None, ()

    @staticmethod
    def _globally_exempt(settings: AutomodSettings, ctx: MessageContext) -> bool:
        if settings.exempt_moderators and ctx.can_manage_messages:
            return True
        channels = {ctx.channel_id, ctx.parent_channel_id}
        if channels & set(settings.exempt_channels):
            return True
        return bool(ctx.role_ids & set(settings.exempt_roles))

    @staticmethod
    def _rule_exempt(rule: MessageRule, ctx: MessageContext) -> bool:
        if {ctx.channel_id, ctx.parent_channel_id} & set(rule.exempt_channels):
            return True
        return bool(ctx.role_ids & set(rule.exempt_roles))

    async def _window(self, redis_key: str, member: str, *, seconds: int) -> list[MessageRef]:
        """Fügt `member` ein und liefert alle Nachrichten im Zeitfenster (inkl. der neuen)."""
        now = self._clock().timestamp()
        pipe = self._redis.pipeline()
        pipe.zremrangebyscore(redis_key, 0, now - seconds)
        pipe.zadd(redis_key, {member: now})
        pipe.expire(redis_key, seconds + 5)
        pipe.zrange(redis_key, 0, -1)
        *_, members = await pipe.execute()
        refs: list[MessageRef] = []
        for raw in members:
            text = raw.decode() if isinstance(raw, bytes) else str(raw)
            channel, _, message = text.partition(":")
            refs.append((int(channel), int(message)))
        return refs

    @staticmethod
    def _spam_key(ctx: MessageContext) -> str:
        return key("automod", "spam", ctx.guild_id, ctx.user_id)

    @staticmethod
    def _dup_key(ctx: MessageContext) -> str | None:
        text = collapse(ctx.content)
        if not text:
            return None  # reine Anhänge/Sticker: nichts zu vergleichen
        digest = hashlib.sha1(text.encode(), usedforsecurity=False).hexdigest()[:16]
        return key("automod", "dup", ctx.guild_id, ctx.user_id, digest)

    async def _record_spam(
        self, settings: AutomodSettings, ctx: MessageContext
    ) -> list[MessageRef]:
        return await self._window(
            self._spam_key(ctx),
            f"{ctx.channel_id}:{ctx.message_id}",
            seconds=settings.spam.seconds,
        )

    async def _record_duplicate(
        self, settings: AutomodSettings, ctx: MessageContext
    ) -> list[MessageRef]:
        redis_key = self._dup_key(ctx)
        if redis_key is None:
            return []
        return await self._window(
            redis_key, f"{ctx.channel_id}:{ctx.message_id}", seconds=settings.duplicates.seconds
        )

    async def _forget(self, name: str, ctx: MessageContext, refs: tuple[MessageRef, ...]) -> None:
        """Gelöschte Wellen aus dem Zähler nehmen, damit sie nicht erneut ausgelöst werden."""
        redis_key = self._spam_key(ctx) if name == "spam" else self._dup_key(ctx)
        if redis_key is not None and refs:
            await self._redis.zrem(redis_key, *(f"{c}:{m}" for c, m in refs))

    async def _may_punish(self, guild_id: int, user_id: int, rule: str) -> bool:
        return bool(
            await self._redis.set(
                key("automod", "punished", guild_id, user_id, rule),
                "1",
                ex=PUNISH_COOLDOWN_SECONDS,
                nx=True,
            )
        )

    # --- Beitritte ---

    async def evaluate_join(self, ctx: JoinContext) -> JoinDecision | None:
        settings = await self.settings(ctx.guild_id)
        if ctx.is_bot or not settings.any_join_rule():
            return None
        if settings.raid.enabled:
            decision = await self._raid(settings, ctx)
            if decision is not None:
                return decision
        if settings.accountage.enabled:
            age = self._clock() - ctx.account_created_at
            if age < timedelta(days=settings.accountage.min_days):
                return JoinDecision(
                    "accountage",
                    settings.accountage.action,
                    (ctx.user_id,),
                    {"days": age.days, "min_days": settings.accountage.min_days},
                )
        return None

    async def _raid(self, settings: AutomodSettings, ctx: JoinContext) -> JoinDecision | None:
        raid = settings.raid
        joins_key = key("automod", "joins", ctx.guild_id)
        active_key = key("automod", "raid", ctx.guild_id)
        now = self._clock().timestamp()

        pipe = self._redis.pipeline()
        pipe.zremrangebyscore(joins_key, 0, now - max(raid.seconds, RAID_KEEP_SECONDS))
        pipe.zadd(joins_key, {str(ctx.user_id): now})
        pipe.expire(joins_key, RAID_KEEP_SECONDS + 5)
        pipe.zrangebyscore(joins_key, now - raid.seconds, "+inf")
        pipe.exists(active_key)
        *_, recent_raw, active = await pipe.execute()
        recent = [int(x.decode() if isinstance(x, bytes) else x) for x in recent_raw]

        if active:
            return JoinDecision("raid", raid.action, (ctx.user_id,), {"joins": len(recent)})
        if len(recent) < raid.joins:
            return None
        # `nx` entscheidet, welcher von mehreren gleichzeitigen Beitritten den Modus auslöst.
        started = await self._redis.set(active_key, "1", ex=raid.mode_minutes * 60, nx=True)
        if not started:
            return JoinDecision("raid", raid.action, (ctx.user_id,), {"joins": len(recent)})
        return JoinDecision(
            "raid", raid.action, tuple(recent), {"joins": len(recent)}, raid_started=True
        )

    async def raid_active(self, guild_id: int) -> bool:
        return bool(await self._redis.exists(key("automod", "raid", guild_id)))

    async def end_raid(self, guild_id: int) -> bool:
        """Beendet den Raid-Modus von Hand. False, wenn keiner lief."""
        removed = await self._redis.delete(key("automod", "raid", guild_id))
        await self._redis.delete(key("automod", "joins", guild_id))
        return bool(removed)

    # --- Statistik ---

    async def record_hit(
        self, guild_id: int, user_id: int, channel_id: int | None, rule: str, actions: list[str]
    ) -> None:
        async with self._db.session() as session:
            await repo.add_hit(
                session,
                guild_id=guild_id,
                user_id=user_id,
                channel_id=channel_id,
                rule=rule,
                actions=",".join(actions),
            )
        await self._cleanup_sometimes()

    async def _cleanup_sometimes(self) -> None:
        """Alte Treffer löschen, höchstens einmal pro Stunde (über alle Prozesse)."""
        due = await self._redis.set(
            key("automod", "cleanup"), "1", ex=CLEANUP_INTERVAL_SECONDS, nx=True
        )
        if due:
            async with self._db.session() as session:
                removed = await repo.purge_older_than(session, self._clock() - HITS_RETENTION)
            if removed:
                log.info("automod_hits_cleaned", removed=removed)

    async def stats(self, guild_id: int, days: int) -> dict[str, int]:
        async with self._db.session() as session:
            return await repo.count_by_rule(session, guild_id, self._clock() - timedelta(days=days))
