"""Einstellungen des Automods (Modul `automod`). Alle Regeln sind standardmässig aus."""

from typing import ClassVar, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ggbot.core.automod.rules import normalize_domain, validate_entry
from ggbot.core.duration import MAX_TIMEOUT
from ggbot.core.errors import UserError
from ggbot.core.settings.schema import SettingsModel

MESSAGE_RULES = ("spam", "duplicates", "caps", "invites", "links", "mentions", "words")
JOIN_RULES = ("raid", "accountage")
MAX_LIST = 100


def _ids() -> list[int]:
    return Field(default_factory=list, max_length=MAX_LIST)


class RuleAction(BaseModel):
    """Was bei einem Treffer passiert. Die Teile lassen sich kombinieren."""

    model_config = ConfigDict(extra="ignore")

    delete: bool = True
    warn: bool = False
    # Timeout in Sekunden; None = kein Timeout.
    timeout_seconds: int | None = Field(default=None, ge=1, le=MAX_TIMEOUT)

    @model_validator(mode="after")
    def _something_happens(self) -> Self:
        if not (self.delete or self.warn or self.timeout_seconds):
            raise ValueError("eine Regel ohne Aktion tut nichts")
        return self


class MessageRule(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    action: RuleAction = Field(default_factory=RuleAction)
    exempt_channels: list[int] = _ids()
    exempt_roles: list[int] = _ids()


class SpamRule(MessageRule):
    messages: int = Field(default=5, ge=2, le=30)
    seconds: int = Field(default=5, ge=1, le=60)


class DuplicatesRule(MessageRule):
    count: int = Field(default=3, ge=2, le=20)
    seconds: int = Field(default=30, ge=5, le=300)


class CapsRule(MessageRule):
    # Erst ab so vielen Buchstaben, sonst wäre "OK!" schon ein Verstoss.
    min_length: int = Field(default=10, ge=5, le=200)
    max_percent: int = Field(default=70, ge=30, le=100)


class InvitesRule(MessageRule):
    # Einladungscodes, die erlaubt sind (z. B. der eigene Server).
    allowed_codes: list[str] = Field(default_factory=list, max_length=MAX_LIST)

    @field_validator("allowed_codes")
    @classmethod
    def _lower(cls, codes: list[str]) -> list[str]:
        return sorted({code.strip().lower() for code in codes if code.strip()})


class LinksRule(MessageRule):
    mode: Literal["blacklist", "whitelist"] = "blacklist"
    domains: list[str] = Field(default_factory=list, max_length=MAX_LIST)

    @field_validator("domains")
    @classmethod
    def _normalize(cls, domains: list[str]) -> list[str]:
        return sorted({normalize_domain(d) for d in domains if d.strip()})


class MentionsRule(MessageRule):
    max_mentions: int = Field(default=5, ge=2, le=50)


class WordEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Literal["word", "wildcard", "regex"]
    pattern: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def _valid_pattern(self) -> Self:
        try:
            validate_entry(self.type, self.pattern)
        except UserError as exc:
            raise ValueError(exc.key) from exc
        return self


class WordsRule(MessageRule):
    entries: list[WordEntry] = Field(default_factory=list, max_length=200)


class JoinAction(BaseModel):
    """Aktion für Beitritte: Kick, Bann oder Timeout."""

    model_config = ConfigDict(extra="ignore")

    action: Literal["kick", "ban", "timeout"] = "kick"
    timeout_seconds: int | None = Field(default=None, ge=1, le=MAX_TIMEOUT)

    @model_validator(mode="after")
    def _timeout_needs_duration(self) -> Self:
        if self.action == "timeout" and self.timeout_seconds is None:
            raise ValueError("timeout braucht eine Dauer")
        if self.action != "timeout" and self.timeout_seconds is not None:
            raise ValueError("nur timeout hat eine Dauer")
        return self


class RaidRule(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    joins: int = Field(default=5, ge=2, le=100)
    seconds: int = Field(default=10, ge=2, le=300)
    # So lange gilt der Raid-Modus: jeder weitere Beitritt bekommt die Aktion.
    mode_minutes: int = Field(default=10, ge=1, le=1440)
    action: JoinAction = Field(default_factory=JoinAction)


class AccountAgeRule(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    min_days: int = Field(default=7, ge=1, le=365)
    action: JoinAction = Field(default_factory=JoinAction)


class AutomodSettings(SettingsModel):
    module: ClassVar[str] = "automod"

    spam: SpamRule = Field(default_factory=SpamRule)
    duplicates: DuplicatesRule = Field(default_factory=DuplicatesRule)
    caps: CapsRule = Field(default_factory=CapsRule)
    invites: InvitesRule = Field(default_factory=InvitesRule)
    links: LinksRule = Field(default_factory=LinksRule)
    mentions: MentionsRule = Field(default_factory=MentionsRule)
    words: WordsRule = Field(default_factory=WordsRule)
    raid: RaidRule = Field(default_factory=RaidRule)
    accountage: AccountAgeRule = Field(default_factory=AccountAgeRule)

    # Gelten für alle Regeln.
    exempt_channels: list[int] = _ids()
    exempt_roles: list[int] = _ids()
    # Wer Nachrichten verwalten darf, wird nicht automatisch moderiert.
    exempt_moderators: bool = True
    log_channel_id: int | None = None

    def message_rule(self, name: str) -> MessageRule:
        if name not in MESSAGE_RULES:
            raise KeyError(name)
        rule: MessageRule = getattr(self, name)
        return rule

    def any_message_rule(self) -> bool:
        return any(self.message_rule(name).enabled for name in MESSAGE_RULES)

    def any_join_rule(self) -> bool:
        return self.raid.enabled or self.accountage.enabled
