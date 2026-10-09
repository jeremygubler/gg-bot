"""Moderationslogik ohne Discord.

Ablauf jeder Aktion: DM an das Ziel (best effort) → Discord-Aktion über den `ModerationPort`
→ Case speichern. Scheitert die Discord-Aktion, entsteht kein Case.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

from ggbot.core.db.engine import Database
from ggbot.core.db.models import ModCase, ModNote
from ggbot.core.db.repos import moderation as repo
from ggbot.core.duration import (
    MAX_TEMPBAN,
    MAX_TIMEOUT,
    MIN_TEMPBAN,
    format_duration,
)
from ggbot.core.errors import UserError
from ggbot.core.i18n import translator
from ggbot.core.logging import get_logger
from ggbot.core.scheduler import Scheduler
from ggbot.core.services.guilds import GuildService
from ggbot.core.settings.schema import ModerationSettings
from ggbot.core.settings.store import SettingsStore

log = get_logger(__name__)

CaseType = Literal["warn", "timeout", "untimeout", "kick", "ban", "tempban", "softban", "unban"]

UNBAN_JOB = "mod.unban"
MAX_REASON = 500  # Discord begrenzt Audit-Log-Gründe auf 512 Zeichen
MAX_NOTE = 1000
PAGE_SIZE = 10
SOFTBAN_DELETE_SECONDS = 86400

# Diese Aktionen betreffen den Nutzer spürbar; bei "untimeout"/"unban" gibt es keine DM.
_DM_KINDS = {"warn", "timeout", "kick", "ban", "tempban", "softban"}


class ModerationPort(Protocol):
    """Das, was der Service von Discord braucht. Der Cog implementiert es.

    Die Methoden werfen `UserError`, wenn Discord die Aktion verweigert oder das Ziel fehlt.
    """

    guild_name: str
    bot_user_id: int

    async def timeout(self, user_id: int, until: datetime | None, reason: str) -> None: ...
    async def kick(self, user_id: int, reason: str) -> None: ...
    async def ban(self, user_id: int, reason: str, delete_seconds: int) -> None: ...
    async def unban(self, user_id: int, reason: str) -> bool:
        """False, wenn der Nutzer gar nicht gebannt war."""
        ...

    async def dm(self, user_id: int, text: str) -> bool:
        """False, wenn die Nachricht nicht zugestellt werden konnte (DMs geschlossen)."""
        ...


@dataclass(frozen=True)
class CaseView:
    case_no: int
    type: str
    target_id: int
    moderator_id: int
    reason: str | None
    duration_seconds: int | None
    expires_at: datetime | None
    active: bool
    ref_case_no: int | None
    created_at: datetime

    @classmethod
    def from_model(cls, case: ModCase) -> "CaseView":
        return cls(
            case_no=case.case_no,
            type=case.type,
            target_id=case.target_id,
            moderator_id=case.moderator_id,
            reason=case.reason,
            duration_seconds=case.duration_seconds,
            expires_at=case.expires_at,
            active=case.active,
            ref_case_no=case.ref_case_no,
            created_at=case.created_at,
        )


@dataclass(frozen=True)
class ActionResult:
    case: CaseView
    # None = es wurde keine DM versucht (abgeschaltet oder nicht vorgesehen).
    dm_sent: bool | None
    escalation: CaseView | None = None
    # i18n-Schlüssel, falls die Eskalation scheiterte (die Verwarnung bleibt bestehen).
    escalation_error: str | None = None


@dataclass(frozen=True)
class HistoryPage:
    cases: list[CaseView]
    total: int
    active_warns: int
    notes: int
    page: int
    pages: int


@dataclass(frozen=True)
class NoteView:
    id: int
    target_id: int
    author_id: int
    content: str
    created_at: datetime

    @classmethod
    def from_model(cls, note: ModNote) -> "NoteView":
        return cls(note.id, note.target_id, note.author_id, note.content, note.created_at)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _clean_reason(reason: str | None) -> str | None:
    if reason is None:
        return None
    reason = reason.strip()
    if not reason:
        return None
    if len(reason) > MAX_REASON:
        raise UserError("error.reason_too_long", limit=MAX_REASON)
    return reason


class ModerationService:
    def __init__(
        self,
        db: Database,
        settings: SettingsStore,
        scheduler: Scheduler,
        guilds: GuildService,
        *,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._db = db
        self._settings = settings
        self._scheduler = scheduler
        self._guilds = guilds
        self._clock = clock

    # --- Aktionen ---

    async def warn(
        self,
        port: ModerationPort,
        guild_id: int,
        moderator_id: int,
        target_id: int,
        reason: str | None,
    ) -> ActionResult:
        result = await self._execute(
            port,
            "warn",
            guild_id=guild_id,
            moderator_id=moderator_id,
            target_id=target_id,
            reason=reason,
        )
        settings = await self._settings.get(guild_id, ModerationSettings)
        since = (
            self._clock() - timedelta(days=settings.warn_expiry_days)
            if settings.warn_expiry_days is not None
            else None
        )
        async with self._db.session() as session:
            count = await repo.count_active_warns(session, guild_id, target_id, since=since)
        rule = next((r for r in settings.escalation if r.warns == count), None)
        if rule is None:
            return result

        locale = await self._guilds.get_locale(guild_id)
        try:
            escalation = await self._execute(
                port,
                rule.action,
                guild_id=guild_id,
                moderator_id=port.bot_user_id,
                target_id=target_id,
                reason=translator.t(locale, "mod.escalation_reason", count=count),
                duration=rule.duration_seconds,
                ref_case_no=result.case.case_no,
            )
        except UserError as exc:
            # Die Verwarnung steht schon. Der Fehler geht an den Moderator, nicht in den Müll.
            log.warning("escalation_failed", guild_id=guild_id, target_id=target_id, key=exc.key)
            return ActionResult(result.case, result.dm_sent, escalation_error=exc.key)
        return ActionResult(result.case, result.dm_sent, escalation=escalation.case)

    async def timeout(
        self,
        port: ModerationPort,
        guild_id: int,
        moderator_id: int,
        target_id: int,
        duration_seconds: int,
        reason: str | None,
    ) -> ActionResult:
        return await self._execute(
            port,
            "timeout",
            guild_id=guild_id,
            moderator_id=moderator_id,
            target_id=target_id,
            reason=reason,
            duration=duration_seconds,
        )

    async def untimeout(
        self,
        port: ModerationPort,
        guild_id: int,
        moderator_id: int,
        target_id: int,
        reason: str | None,
    ) -> ActionResult:
        return await self._execute(
            port,
            "untimeout",
            guild_id=guild_id,
            moderator_id=moderator_id,
            target_id=target_id,
            reason=reason,
        )

    async def kick(
        self,
        port: ModerationPort,
        guild_id: int,
        moderator_id: int,
        target_id: int,
        reason: str | None,
    ) -> ActionResult:
        return await self._execute(
            port,
            "kick",
            guild_id=guild_id,
            moderator_id=moderator_id,
            target_id=target_id,
            reason=reason,
        )

    async def ban(
        self,
        port: ModerationPort,
        guild_id: int,
        moderator_id: int,
        target_id: int,
        reason: str | None,
        delete_seconds: int = 0,
    ) -> ActionResult:
        return await self._execute(
            port,
            "ban",
            guild_id=guild_id,
            moderator_id=moderator_id,
            target_id=target_id,
            reason=reason,
            delete_seconds=delete_seconds,
        )

    async def tempban(
        self,
        port: ModerationPort,
        guild_id: int,
        moderator_id: int,
        target_id: int,
        duration_seconds: int,
        reason: str | None,
        delete_seconds: int = 0,
    ) -> ActionResult:
        return await self._execute(
            port,
            "tempban",
            guild_id=guild_id,
            moderator_id=moderator_id,
            target_id=target_id,
            reason=reason,
            duration=duration_seconds,
            delete_seconds=delete_seconds,
        )

    async def softban(
        self,
        port: ModerationPort,
        guild_id: int,
        moderator_id: int,
        target_id: int,
        reason: str | None,
        delete_seconds: int = SOFTBAN_DELETE_SECONDS,
    ) -> ActionResult:
        return await self._execute(
            port,
            "softban",
            guild_id=guild_id,
            moderator_id=moderator_id,
            target_id=target_id,
            reason=reason,
            delete_seconds=delete_seconds,
        )

    async def unban(
        self,
        port: ModerationPort,
        guild_id: int,
        moderator_id: int,
        target_id: int,
        reason: str | None,
    ) -> ActionResult:
        return await self._execute(
            port,
            "unban",
            guild_id=guild_id,
            moderator_id=moderator_id,
            target_id=target_id,
            reason=reason,
        )

    async def unwarn(self, guild_id: int, case_no: int) -> CaseView:
        """Nimmt eine Verwarnung aus der Eskalationszählung. Der Case bleibt in der Historie."""
        async with self._db.session() as session:
            case = await repo.get_case(session, guild_id, case_no)
            if case is None:
                raise UserError("error.mod_case_not_found", case_no=case_no)
            if case.type != "warn":
                raise UserError("error.mod_not_a_warn", case_no=case_no)
            if not case.active:
                raise UserError("error.mod_warn_inactive", case_no=case_no)
            await repo.deactivate_case(session, guild_id, case_no)
            case.active = False
            return CaseView.from_model(case)

    # --- Automatischer Unban ---

    async def finish_tempban(self, port: ModerationPort, guild_id: int, case_no: int) -> bool:
        """Job-Handler: hebt einen abgelaufenen Tempban auf. False = nichts zu tun."""
        async with self._db.session() as session:
            case = await repo.get_case(session, guild_id, case_no)
            if case is None or case.type != "tempban" or not case.active:
                return False  # zwischenzeitlich manuell entbannt
            target_id = case.target_id
        locale = await self._guilds.get_locale(guild_id)
        reason = translator.t(locale, "mod.tempban_expired", case_no=case_no)
        await port.unban(target_id, reason)  # False (schon entbannt) ist hier in Ordnung
        async with self._db.session() as session:
            await repo.deactivate_case(session, guild_id, case_no)
            await repo.create_case(
                session,
                guild_id=guild_id,
                type="unban",
                target_id=target_id,
                moderator_id=port.bot_user_id,
                reason=reason,
                ref_case_no=case_no,
                active=False,
            )
        return True

    # --- Abfragen ---

    async def get_case(self, guild_id: int, case_no: int) -> CaseView:
        async with self._db.session() as session:
            case = await repo.get_case(session, guild_id, case_no)
        if case is None:
            raise UserError("error.mod_case_not_found", case_no=case_no)
        return CaseView.from_model(case)

    async def history(self, guild_id: int, target_id: int, page: int = 1) -> HistoryPage:
        settings = await self._settings.get(guild_id, ModerationSettings)
        since = (
            self._clock() - timedelta(days=settings.warn_expiry_days)
            if settings.warn_expiry_days is not None
            else None
        )
        async with self._db.session() as session:
            _, total = await repo.list_cases(session, guild_id, target_id, limit=1, offset=0)
            pages = max(1, -(-total // PAGE_SIZE))
            page = min(max(page, 1), pages)
            cases, _ = await repo.list_cases(
                session, guild_id, target_id, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE
            )
            warns = await repo.count_active_warns(session, guild_id, target_id, since=since)
            notes = len(await repo.list_notes(session, guild_id, target_id))
        return HistoryPage(
            [CaseView.from_model(c) for c in cases], total, warns, notes, page, pages
        )

    # --- Notizen ---

    async def add_note(
        self, guild_id: int, target_id: int, author_id: int, content: str
    ) -> NoteView:
        content = content.strip()
        if not content:
            raise UserError("error.note_empty")
        if len(content) > MAX_NOTE:
            raise UserError("error.note_too_long", limit=MAX_NOTE)
        async with self._db.session() as session:
            note = await repo.add_note(session, guild_id, target_id, author_id, content)
            return NoteView.from_model(note)

    async def list_notes(self, guild_id: int, target_id: int) -> list[NoteView]:
        async with self._db.session() as session:
            return [
                NoteView.from_model(n) for n in await repo.list_notes(session, guild_id, target_id)
            ]

    async def remove_note(self, guild_id: int, note_id: int) -> None:
        async with self._db.session() as session:
            if not await repo.delete_note(session, guild_id, note_id):
                raise UserError("error.note_not_found", note_id=note_id)

    # --- Kern ---

    async def _execute(
        self,
        port: ModerationPort,
        kind: CaseType,
        *,
        guild_id: int,
        moderator_id: int,
        target_id: int,
        reason: str | None,
        duration: int | None = None,
        delete_seconds: int = 0,
        ref_case_no: int | None = None,
    ) -> ActionResult:
        reason = _clean_reason(reason)
        self._check_duration(kind, duration)
        settings = await self._settings.get(guild_id, ModerationSettings)
        locale = await self._guilds.get_locale(guild_id)
        audit_reason = f"{moderator_id}: {reason}" if reason else str(moderator_id)
        now = self._clock()
        expires_at = now + timedelta(seconds=duration) if duration is not None else None

        dm_sent: bool | None = None
        if settings.dm_on_action and kind in _DM_KINDS and target_id != port.bot_user_id:
            dm_sent = await port.dm(
                target_id,
                translator.t(
                    locale,
                    f"mod.dm.{kind}",
                    guild=port.guild_name,
                    reason=reason or translator.t(locale, "mod.no_reason"),
                    duration=format_duration(duration or 0),
                ),
            )

        recorded: CaseType = kind
        failure: UserError | None = None
        match kind:
            case "warn":
                pass
            case "timeout":
                await port.timeout(target_id, expires_at, audit_reason)
            case "untimeout":
                await port.timeout(target_id, None, audit_reason)
            case "kick":
                await port.kick(target_id, audit_reason)
            case "ban" | "tempban":
                await port.ban(target_id, audit_reason, delete_seconds)
            case "softban":
                await port.ban(target_id, audit_reason, delete_seconds)
                try:
                    await port.unban(target_id, audit_reason)
                except UserError as exc:
                    # Der Nutzer ist wirklich gebannt: der Case muss das wahrheitsgemäss zeigen.
                    recorded, failure = "ban", exc
            case "unban":
                if not await port.unban(target_id, audit_reason):
                    raise UserError("error.mod_not_banned")

        async with self._db.session() as session:
            case = await repo.create_case(
                session,
                guild_id=guild_id,
                type=recorded,
                target_id=target_id,
                moderator_id=moderator_id,
                reason=reason,
                duration_seconds=duration,
                expires_at=expires_at if kind in ("timeout", "tempban") else None,
                ref_case_no=ref_case_no,
                active=recorded in ("warn", "tempban"),
            )
            if kind == "tempban":
                assert expires_at is not None
                await repo.deactivate_active_before(session, guild_id, target_id, case.case_no)
            if kind == "unban":
                await repo.deactivate_active(session, guild_id, target_id, "tempban")
            view = CaseView.from_model(case)
        if kind == "tempban":
            assert expires_at is not None
            await self._scheduler.enqueue(
                guild_id, UNBAN_JOB, expires_at, {"case_no": view.case_no, "user_id": target_id}
            )
        if failure is not None:
            raise failure
        return ActionResult(view, dm_sent)

    @staticmethod
    def _check_duration(kind: CaseType, duration: int | None) -> None:
        if kind == "timeout" and (duration is None or not 1 <= duration <= MAX_TIMEOUT):
            raise UserError("error.mod_timeout_range", max=format_duration(MAX_TIMEOUT))
        if kind == "tempban" and (duration is None or not MIN_TEMPBAN <= duration <= MAX_TEMPBAN):
            raise UserError(
                "error.mod_tempban_range",
                min=format_duration(MIN_TEMPBAN),
                max=format_duration(MAX_TEMPBAN),
            )
