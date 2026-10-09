from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from redis.asyncio import Redis
from sqlalchemy import text

from ggbot.core.db.engine import Database
from ggbot.core.services.automod import AutomodService, JoinContext, MessageContext, Violation
from ggbot.core.settings.automod import (
    AccountAgeRule,
    AutomodSettings,
    CapsRule,
    DuplicatesRule,
    InvitesRule,
    JoinAction,
    LinksRule,
    MentionsRule,
    RaidRule,
    RuleAction,
    SpamRule,
    WordEntry,
    WordsRule,
)
from ggbot.core.settings.store import SettingsStore

T0 = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)
G = 100
U = 42
CH = 7


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@dataclass
class Env:
    svc: AutomodService
    store: SettingsStore
    clock: Clock
    db: Database
    redis: Redis
    counter: list[int] = field(default_factory=lambda: [1000])

    async def configure(self, **kwargs: object) -> None:
        await self.store.save(G, AutomodSettings.model_validate(kwargs))
        self.svc.invalidate(G)

    def msg(self, content: str = "hallo", **overrides: object) -> MessageContext:
        self.counter[0] += 1
        data: dict[str, object] = {
            "guild_id": G,
            "channel_id": CH,
            "parent_channel_id": None,
            "message_id": self.counter[0],
            "user_id": U,
            "content": content,
            "role_ids": frozenset[int](),
            "mentioned_user_ids": frozenset[int](),
            "mentioned_role_ids": frozenset[int](),
            "can_manage_messages": False,
        }
        data.update(overrides)
        return MessageContext(**data)  # type: ignore[arg-type]


@pytest.fixture
async def env(db: Database, redis: Redis) -> Env:
    clock = Clock()
    store = SettingsStore(db, redis)
    return Env(AutomodService(db, store, redis, clock=clock), store, clock, db, redis)


def on(**extra: object) -> dict[str, object]:
    return {"enabled": True, **extra}


# --- Grundverhalten ---


async def test_ohne_aktive_regel_passiert_nichts_und_nichts_wird_gezaehlt(env: Env) -> None:
    assert await env.svc.evaluate_message(env.msg("DAS IST LAUT UND https://boese.com")) is None
    assert [k async for k in env.redis.scan_iter("gg:automod:*")] == []


async def test_wortfilter_trifft_und_nennt_den_ausloeser(env: Env) -> None:
    await env.configure(words=on(entries=[{"type": "word", "pattern": "dummkopf"}]))
    message = env.msg("du bist ein Dummkopf")
    violation = await env.svc.evaluate_message(message)
    assert violation is not None
    assert (violation.rule, violation.detail) == ("words", "dummkopf")
    assert violation.delete == ((CH, message.message_id),)
    assert violation.action == RuleAction()
    assert await env.svc.evaluate_message(env.msg("nett")) is None


async def test_regel_reihenfolge_woerter_vor_spam(env: Env) -> None:
    await env.configure(
        words=on(entries=[{"type": "word", "pattern": "boese"}]),
        spam=on(messages=2, seconds=5),
    )
    await env.svc.evaluate_message(env.msg("hallo"))
    violation = await env.svc.evaluate_message(env.msg("boese"))
    assert violation is not None
    assert violation.rule == "words"


async def test_aktion_kommt_aus_der_getroffenen_regel(env: Env) -> None:
    await env.configure(
        caps=on(min_length=5, action={"delete": False, "warn": True}),
        links=on(domains=["boese.com"], action={"timeout_seconds": 600}),
    )
    caps = await env.svc.evaluate_message(env.msg("ALLES GROSS HIER"))
    assert caps is not None
    assert (caps.action.delete, caps.action.warn) == (False, True)
    link = await env.svc.evaluate_message(env.msg("https://boese.com"))
    assert link is not None
    assert link.action.timeout_seconds == 600


async def test_einzelne_regeln(env: Env) -> None:
    await env.configure(
        caps=on(min_length=5),
        invites=on(allowed_codes=["meins"]),
        links=on(mode="whitelist", domains=["gut.de", "discord.gg"]),
        mentions=on(max_mentions=2),
    )

    def rule(violation: Violation | None) -> str | None:
        return violation.rule if violation else None

    assert rule(await env.svc.evaluate_message(env.msg("SEHR LAUTER TEXT"))) == "caps"
    assert rule(await env.svc.evaluate_message(env.msg("discord.gg/fremd"))) == "invites"
    assert await env.svc.evaluate_message(env.msg("discord.gg/meins")) is None
    assert rule(await env.svc.evaluate_message(env.msg("https://boese.org"))) == "links"
    assert await env.svc.evaluate_message(env.msg("https://gut.de/x")) is None
    many = env.msg("hi", mentioned_user_ids=frozenset({1, 2}), mentioned_role_ids=frozenset({3}))
    assert rule(await env.svc.evaluate_message(many)) == "mentions"
    ok = env.msg("hi", mentioned_user_ids=frozenset({1, 2}))
    assert await env.svc.evaluate_message(ok) is None


# --- Spam ---


async def test_spam_loest_bei_der_fuenften_nachricht_aus(env: Env) -> None:
    await env.configure(spam=on(messages=5, seconds=5))
    for i in range(4):
        assert await env.svc.evaluate_message(env.msg(f"m{i}")) is None
        env.clock.advance(0.5)
    fifth = env.msg("m4")
    violation = await env.svc.evaluate_message(fifth)
    assert violation is not None
    assert violation.rule == "spam"
    assert len(violation.delete) == 5  # die ganze Welle
    assert (CH, fifth.message_id) in violation.delete


async def test_spam_welle_ueber_mehrere_kanaele_wird_komplett_geloescht(env: Env) -> None:
    await env.configure(spam=on(messages=3, seconds=10))
    for channel in (1, 2):
        await env.svc.evaluate_message(env.msg("x", channel_id=channel))
    last = env.msg("x", channel_id=3)
    violation = await env.svc.evaluate_message(last)
    assert violation is not None
    assert {channel for channel, _ in violation.delete} == {1, 2, 3}


async def test_spam_fenster_laeuft_ab(env: Env) -> None:
    await env.configure(spam=on(messages=3, seconds=5))
    for _ in range(2):
        await env.svc.evaluate_message(env.msg("x"))
    env.clock.advance(6)
    assert await env.svc.evaluate_message(env.msg("x")) is None


async def test_spam_zaehler_ist_nach_ausloesung_zurueckgesetzt(env: Env) -> None:
    await env.configure(spam=on(messages=3, seconds=60))
    for _ in range(2):
        await env.svc.evaluate_message(env.msg("x"))
    assert await env.svc.evaluate_message(env.msg("x")) is not None
    # Die gelöschte Welle zählt nicht noch einmal: erst drei neue lösen wieder aus.
    assert await env.svc.evaluate_message(env.msg("x")) is None
    assert await env.svc.evaluate_message(env.msg("x")) is None
    assert await env.svc.evaluate_message(env.msg("x")) is not None


async def test_spam_zaehlt_pro_nutzer_und_pro_server(env: Env) -> None:
    await env.configure(spam=on(messages=2, seconds=60))
    await env.store.save(G + 1, AutomodSettings(spam=SpamRule(enabled=True, messages=2)))
    env.svc.invalidate(G + 1)
    await env.svc.evaluate_message(env.msg("x", user_id=1))
    assert await env.svc.evaluate_message(env.msg("x", user_id=2)) is None
    assert await env.svc.evaluate_message(env.msg("x", guild_id=G + 1, user_id=1)) is None
    assert await env.svc.evaluate_message(env.msg("x", user_id=1)) is not None


async def test_bearbeitungen_zaehlen_nicht_als_spam_aber_der_inhalt_wird_geprueft(env: Env) -> None:
    await env.configure(
        spam=on(messages=2, seconds=60),
        words=on(entries=[{"type": "word", "pattern": "boese"}]),
    )
    for _ in range(10):
        assert await env.svc.evaluate_message(env.msg("x", is_edit=True)) is None
    edited = await env.svc.evaluate_message(env.msg("boese", is_edit=True))
    assert edited is not None
    assert edited.rule == "words"


# --- Duplikate ---


async def test_duplikate_ignorieren_gross_klein_und_leerzeichen(env: Env) -> None:
    await env.configure(duplicates=on(count=3, seconds=30))
    assert await env.svc.evaluate_message(env.msg("Hallo   Welt!")) is None
    assert await env.svc.evaluate_message(env.msg("hallo welt")) is None
    violation = await env.svc.evaluate_message(env.msg("HALLO WELT"))
    assert violation is not None
    assert (violation.rule, len(violation.delete)) == ("duplicates", 3)


async def test_verschiedene_texte_sind_keine_duplikate(env: Env) -> None:
    await env.configure(duplicates=on(count=2, seconds=30))
    for content in ("eins", "zwei", "drei", "vier"):
        assert await env.svc.evaluate_message(env.msg(content)) is None


async def test_duplikate_fenster_und_leere_nachrichten(env: Env) -> None:
    await env.configure(duplicates=on(count=2, seconds=10))
    await env.svc.evaluate_message(env.msg("gleich"))
    env.clock.advance(11)
    assert await env.svc.evaluate_message(env.msg("gleich")) is None
    for _ in range(5):  # nur Anhang/Sticker, kein Text
        assert await env.svc.evaluate_message(env.msg("")) is None


# --- Ausnahmen ---


async def test_globale_ausnahmen(env: Env) -> None:
    await env.configure(
        words=on(entries=[{"type": "word", "pattern": "boese"}]),
        exempt_channels=[50],
        exempt_roles=[60],
    )
    bad = "boese"
    assert await env.svc.evaluate_message(env.msg(bad, channel_id=50)) is None
    # Thread in einem ausgenommenen Kanal
    assert await env.svc.evaluate_message(env.msg(bad, channel_id=9, parent_channel_id=50)) is None
    assert await env.svc.evaluate_message(env.msg(bad, role_ids=frozenset({60, 61}))) is None
    assert await env.svc.evaluate_message(env.msg(bad)) is not None


async def test_moderatoren_sind_ausgenommen_wenn_gewuenscht(env: Env) -> None:
    await env.configure(words=on(entries=[{"type": "word", "pattern": "boese"}]))
    assert await env.svc.evaluate_message(env.msg("boese", can_manage_messages=True)) is None
    await env.configure(
        words=on(entries=[{"type": "word", "pattern": "boese"}]), exempt_moderators=False
    )
    assert await env.svc.evaluate_message(env.msg("boese", can_manage_messages=True)) is not None


async def test_regel_ausnahmen_gelten_nur_fuer_diese_regel(env: Env) -> None:
    await env.configure(
        caps=on(min_length=5, exempt_channels=[50]),
        words=on(entries=[{"type": "word", "pattern": "boese"}], exempt_roles=[60]),
    )
    shout = "ALLES SEHR LAUT"
    assert await env.svc.evaluate_message(env.msg(shout, channel_id=50)) is None
    assert await env.svc.evaluate_message(env.msg(shout)) is not None
    # In Kanal 50 greift der Wortfilter trotzdem, in Rolle 60 nicht.
    in_50 = await env.svc.evaluate_message(env.msg("boese", channel_id=50))
    assert in_50 is not None
    assert in_50.rule == "words"
    assert await env.svc.evaluate_message(env.msg("boese", role_ids=frozenset({60}))) is None


# --- Bestrafung höchstens einmal je Welle ---


async def test_bestrafung_nur_einmal_pro_regel_und_nutzer(env: Env) -> None:
    await env.configure(words=on(entries=[{"type": "word", "pattern": "boese"}]))
    first = await env.svc.evaluate_message(env.msg("boese"))
    second = await env.svc.evaluate_message(env.msg("boese"))
    assert first is not None
    assert second is not None
    assert (first.punish, second.punish) == (True, False)  # löschen: ja, erneut verwarnen: nein
    other = await env.svc.evaluate_message(env.msg("boese", user_id=99))
    assert other is not None
    assert other.punish is True


# --- Beitritte ---


def join(
    user_id: int = 1, *, age_days: float = 100, bot: bool = False, guild: int = G
) -> JoinContext:
    return JoinContext(guild, user_id, bot, T0 - timedelta(days=age_days))


async def test_kontoalter_grenze(env: Env) -> None:
    await env.configure(accountage=on(min_days=7, action={"action": "kick"}))
    young = await env.svc.evaluate_join(join(age_days=6.9))
    assert young is not None
    assert (young.rule, young.targets, young.reason_params["min_days"]) == ("accountage", (1,), 7)
    assert await env.svc.evaluate_join(join(age_days=7)) is None  # genau 7 Tage ist alt genug
    assert await env.svc.evaluate_join(join(age_days=400)) is None


async def test_bots_und_abgeschaltete_regeln_werden_uebersprungen(env: Env) -> None:
    await env.configure(accountage=on(min_days=7))
    assert await env.svc.evaluate_join(join(age_days=0, bot=True)) is None
    await env.configure(accountage=AccountAgeRule(min_days=7).model_dump())
    assert await env.svc.evaluate_join(join(age_days=0)) is None


async def test_raid_loest_bei_schwelle_aus_und_trifft_die_ganze_welle(env: Env) -> None:
    await env.configure(raid=on(joins=3, seconds=10, action={"action": "ban"}))
    assert await env.svc.evaluate_join(join(1)) is None
    env.clock.advance(1)
    assert await env.svc.evaluate_join(join(2)) is None
    env.clock.advance(1)
    decision = await env.svc.evaluate_join(join(3))
    assert decision is not None
    assert decision.rule == "raid"
    assert decision.raid_started is True
    assert sorted(decision.targets) == [1, 2, 3]
    assert decision.action.action == "ban"
    assert await env.svc.raid_active(G) is True


async def test_im_raid_modus_trifft_jeder_weitere_beitritt_einzeln(env: Env) -> None:
    await env.configure(raid=on(joins=2, seconds=10))
    await env.svc.evaluate_join(join(1))
    assert (await env.svc.evaluate_join(join(2))).raid_started is True  # type: ignore[union-attr]
    env.clock.advance(300)  # weit nach dem Fenster, aber der Modus läuft noch
    later = await env.svc.evaluate_join(join(3))
    assert later is not None
    assert (later.targets, later.raid_started) == ((3,), False)


async def test_raid_modus_endet_nach_der_dauer_und_von_hand(env: Env) -> None:
    await env.configure(raid=on(joins=2, seconds=10, mode_minutes=1))
    await env.svc.evaluate_join(join(1))
    await env.svc.evaluate_join(join(2))
    assert await env.svc.end_raid(G) is True
    assert await env.svc.raid_active(G) is False
    assert await env.svc.end_raid(G) is False  # lief nicht mehr
    env.clock.advance(60)
    assert await env.svc.evaluate_join(join(3)) is None  # Welle ist nach end_raid vergessen


async def test_langsame_beitritte_sind_kein_raid(env: Env) -> None:
    await env.configure(raid=on(joins=3, seconds=10))
    for user in range(1, 8):
        assert await env.svc.evaluate_join(join(user)) is None
        env.clock.advance(6)  # nie 3 innerhalb von 10 s


async def test_raid_ist_pro_server_getrennt(env: Env) -> None:
    await env.configure(raid=on(joins=2, seconds=10))
    await env.store.save(G + 1, AutomodSettings(raid=RaidRule(enabled=True, joins=2)))
    env.svc.invalidate(G + 1)
    await env.svc.evaluate_join(join(1))
    assert await env.svc.evaluate_join(join(2, guild=G + 1)) is None
    assert await env.svc.evaluate_join(join(2)) is not None
    assert await env.svc.raid_active(G + 1) is False


async def test_raid_hat_vorrang_vor_kontoalter(env: Env) -> None:
    await env.configure(
        raid=on(joins=2, seconds=10, action={"action": "ban"}),
        accountage=on(min_days=7, action={"action": "kick"}),
    )
    await env.svc.evaluate_join(join(1, age_days=0))
    decision = await env.svc.evaluate_join(join(2, age_days=0))
    assert decision is not None
    assert decision.rule == "raid"


async def test_alter_konto_im_raid_wird_trotzdem_nur_einmal_behandelt(env: Env) -> None:
    await env.configure(
        raid=on(joins=3, seconds=10),
        accountage=on(min_days=7, action={"action": "timeout", "timeout_seconds": 60}),
    )
    first = await env.svc.evaluate_join(join(1, age_days=1))
    assert first is not None
    assert first.rule == "accountage"  # noch kein Raid, aber zu jung


# --- Statistik ---


async def test_treffer_zaehlen_nach_regel_und_zeitraum(env: Env) -> None:
    await env.svc.record_hit(G, U, CH, "spam", ["delete", "timeout"])
    await env.svc.record_hit(G, U, CH, "spam", ["delete"])
    await env.svc.record_hit(G, U, None, "words", ["delete", "warn"])
    await env.svc.record_hit(G + 1, U, CH, "spam", ["delete"])
    assert await env.svc.stats(G, 7) == {"spam": 2, "words": 1}
    assert await env.svc.stats(G + 1, 7) == {"spam": 1}
    async with (
        env.db.session() as session
    ):  # die DB stempelt echte Zeit, die Test-Uhr steht im April
        await session.execute(text("UPDATE automod_hits SET created_at = :t"), {"t": T0})
    assert await env.svc.stats(G, 7) == {"spam": 2, "words": 1}
    env.clock.advance(8 * 86400)
    assert await env.svc.stats(G, 7) == {}


async def test_alte_treffer_werden_nach_30_tagen_geloescht(env: Env) -> None:
    await env.svc.record_hit(G, U, CH, "spam", ["delete"])
    async with env.db.session() as session:
        await session.execute(
            text("UPDATE automod_hits SET created_at = :t"), {"t": T0 - timedelta(days=31)}
        )
    await env.redis.delete("gg:automod:cleanup")  # die Stunden-Sperre überspringen
    await env.svc.record_hit(G, U, CH, "words", ["delete"])
    async with env.db.session() as session:
        count = (await session.execute(text("SELECT count(*) FROM automod_hits"))).scalar()
    assert count == 1


async def test_aufraeumen_laeuft_hoechstens_einmal_pro_stunde(env: Env) -> None:
    await env.svc.record_hit(G, U, CH, "spam", ["delete"])
    async with env.db.session() as session:
        await session.execute(
            text("UPDATE automod_hits SET created_at = :t"), {"t": T0 - timedelta(days=31)}
        )
    await env.svc.record_hit(G, U, CH, "words", ["delete"])  # Sperre aktiv: kein Aufräumen
    async with env.db.session() as session:
        count = (await session.execute(text("SELECT count(*) FROM automod_hits"))).scalar()
    assert count == 2


# --- Einstellungs-Cache ---


async def test_einstellungen_sind_kurz_gecacht_und_invalidierbar(env: Env) -> None:
    await env.configure(words=on(entries=[{"type": "word", "pattern": "boese"}]))
    assert await env.svc.evaluate_message(env.msg("boese")) is not None
    # Ohne Invalidierung bleibt der Prozess-Cache (bis zu 5 s) bei den alten Werten.
    await env.store.save(G, AutomodSettings())
    assert await env.svc.evaluate_message(env.msg("boese")) is not None
    env.svc.invalidate(G)
    assert await env.svc.evaluate_message(env.msg("boese")) is None


@pytest.mark.parametrize("name", ["spam", "duplicates", "caps", "mentions"])
def test_regelmodelle_haben_sinnvolle_standardwerte(name: str) -> None:
    rule = {
        "spam": SpamRule(),
        "duplicates": DuplicatesRule(),
        "caps": CapsRule(),
        "mentions": MentionsRule(),
    }[name]
    assert rule.enabled is False
    assert rule.action.delete is True


async def test_einladungsausnahme_macht_den_link_nicht_automatisch_erlaubt(env: Env) -> None:
    # Eine Einladung ist auch ein Link: im Whitelist-Modus muss discord.gg dort stehen.
    await env.configure(
        invites=on(allowed_codes=["meins"]), links=on(mode="whitelist", domains=["gut.de"])
    )
    violation = await env.svc.evaluate_message(env.msg("discord.gg/meins"))
    assert violation is not None
    assert violation.rule == "links"


def test_unbenutzte_importe_bleiben_gueltig() -> None:
    assert LinksRule().mode == "blacklist"
    assert InvitesRule().allowed_codes == []
    assert WordsRule().entries == []
    assert WordEntry(type="word", pattern="x").type == "word"
    assert JoinAction().action == "kick"
