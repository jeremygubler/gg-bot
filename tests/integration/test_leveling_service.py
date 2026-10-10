import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from redis.asyncio import Redis
from sqlalchemy import text

from ggbot.core.db.engine import Database
from ggbot.core.errors import UserError
from ggbot.core.leveling.importer import ImportRow
from ggbot.core.leveling.xp import MAX_XP, total_xp_for_level
from ggbot.core.services.leveling import PAGE_SIZE, LevelingService, XpContext
from ggbot.core.settings.card import CardStyle
from ggbot.core.settings.leveling import LevelingSettings, VoiceXp
from ggbot.core.settings.store import SettingsStore

G = 100
U = 42
NOW = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)


@dataclass
class Env:
    svc: LevelingService
    store: SettingsStore
    db: Database
    redis: Redis

    async def configure(self, **kwargs: object) -> None:
        settings = {"enabled": True, **kwargs}
        await self.store.save(G, LevelingSettings.model_validate(settings))
        self.svc.invalidate(G)

    def ctx(self, **overrides: object) -> XpContext:
        data: dict[str, object] = {
            "guild_id": G,
            "user_id": U,
            "channel_id": 7,
            "parent_channel_id": None,
            "role_ids": frozenset[int](),
        }
        data.update(overrides)
        return XpContext(**data)  # type: ignore[arg-type]


@pytest.fixture
async def env(db: Database, redis: Redis) -> Env:
    store = SettingsStore(db, redis)
    # rng liefert immer die untere Grenze: Ergebnisse sind vorhersagbar.
    svc = LevelingService(db, store, redis, clock=lambda: NOW, rng=lambda low, high: low)
    return Env(svc, store, db, redis)


# --- Text-XP ---


async def test_ausgeschaltet_gibt_es_nichts(env: Env) -> None:
    assert await env.svc.award_message(env.ctx()) is None
    await env.configure(enabled=False)
    assert await env.svc.award_message(env.ctx()) is None


async def test_nachricht_gibt_xp_und_zaehlt(env: Env) -> None:
    await env.configure()
    award = await env.svc.award_message(env.ctx())
    assert award is not None
    assert (award.amount, award.xp, award.old_level, award.new_level) == (15, 15, 0, 0)
    assert award.leveled_up is False
    info = await env.svc.rank(G, U)
    assert (info.progress.xp, info.msg_count, info.position) == (15, 1, 1)


async def test_cooldown_begrenzt_pro_nutzer(env: Env) -> None:
    await env.configure(cooldown_seconds=60)
    assert await env.svc.award_message(env.ctx()) is not None
    assert await env.svc.award_message(env.ctx()) is None
    assert await env.svc.award_message(env.ctx(user_id=2)) is not None  # anderer Nutzer
    assert (await env.svc.rank(G, U)).progress.xp == 15


async def test_cooldown_null_erlaubt_jede_nachricht(env: Env) -> None:
    await env.configure(cooldown_seconds=0)
    for _ in range(3):
        assert await env.svc.award_message(env.ctx()) is not None
    assert (await env.svc.rank(G, U)).progress.xp == 45


async def test_ausgenommene_nachricht_startet_keinen_cooldown(env: Env) -> None:
    await env.configure(no_xp_channels=[7])
    assert await env.svc.award_message(env.ctx(channel_id=7)) is None
    assert await env.svc.award_message(env.ctx(channel_id=8)) is not None  # sofort möglich


async def test_ausnahmen_kanal_thread_und_rolle(env: Env) -> None:
    await env.configure(no_xp_channels=[7], no_xp_roles=[50], cooldown_seconds=0)
    assert await env.svc.award_message(env.ctx(channel_id=7)) is None
    assert await env.svc.award_message(env.ctx(channel_id=99, parent_channel_id=7)) is None
    assert await env.svc.award_message(env.ctx(channel_id=8, role_ids=frozenset({1, 50}))) is None
    assert await env.svc.award_message(env.ctx(channel_id=8, role_ids=frozenset({1}))) is not None


async def test_multiplikatoren_wirken(env: Env) -> None:
    await env.configure(
        cooldown_seconds=0,
        xp_min=20,
        xp_max=20,
        multiplier=1.5,
        role_multipliers={5: 2.0, 6: 3.0},
        channel_multipliers={9: 2.0},
    )
    plain = await env.svc.award_message(env.ctx())
    assert plain is not None
    assert plain.amount == 30  # 20 × 1,5
    boosted = await env.svc.award_message(env.ctx(role_ids=frozenset({5, 6}), channel_id=9))
    assert boosted is not None
    assert boosted.amount == 180  # 20 × 1,5 × 3 (höchste Rolle) × 2 (Kanal)


async def test_level_aufstieg_wird_erkannt(env: Env) -> None:
    await env.configure(cooldown_seconds=0, xp_min=100, xp_max=100)
    award = await env.svc.award_message(env.ctx())
    assert award is not None
    assert (award.old_level, award.new_level, award.leveled_up) == (0, 1, True)
    second = await env.svc.award_message(env.ctx())
    assert second is not None
    assert second.leveled_up is False  # 200 XP sind immer noch Level 1


async def test_parallele_vergaben_gehen_nicht_verloren(env: Env) -> None:
    await env.configure(cooldown_seconds=0, xp_min=10, xp_max=10)
    await asyncio.gather(*(env.svc.award_message(env.ctx()) for _ in range(25)))
    info = await env.svc.rank(G, U)
    assert info.progress.xp == 250
    assert info.msg_count == 25


async def test_server_sind_getrennt(env: Env) -> None:
    await env.configure()
    await env.svc.award_message(env.ctx())
    assert (await env.svc.rank(G + 1, U)).progress.xp == 0


# --- Voice ---


async def test_voice_braucht_beide_schalter(env: Env) -> None:
    await env.configure(voice=VoiceXp(enabled=False).model_dump())
    assert await env.svc.award_voice(env.ctx()) is None
    await env.configure(enabled=False, voice=VoiceXp(enabled=True).model_dump())
    assert await env.svc.award_voice(env.ctx()) is None


async def test_voice_vergibt_xp_und_zaehlt_sekunden(env: Env) -> None:
    await env.configure(voice={"enabled": True, "xp_min": 12, "xp_max": 12})
    award = await env.svc.award_voice(env.ctx())
    assert award is not None
    assert award.amount == 12
    await env.svc.award_voice(env.ctx())
    info = await env.svc.rank(G, U)
    assert (info.progress.xp, info.voice_seconds, info.msg_count) == (24, 120, 0)


async def test_voice_hat_keinen_cooldown_aber_ausnahmen_und_faktoren(env: Env) -> None:
    await env.configure(
        voice={"enabled": True, "xp_min": 10, "xp_max": 10},
        no_xp_channels=[7],
        channel_multipliers={9: 2.0},
    )
    assert await env.svc.award_voice(env.ctx(channel_id=7)) is None
    first = await env.svc.award_voice(env.ctx(channel_id=9))
    second = await env.svc.award_voice(env.ctx(channel_id=9))
    assert first is not None
    assert second is not None
    assert (first.amount, second.amount) == (20, 20)


# --- Rang und Bestenliste ---


async def fill(env: Env, values: dict[int, int]) -> None:
    for user_id, xp in values.items():
        await env.svc.give(G, user_id, xp)


async def test_rang_und_bestenliste_sind_konsistent(env: Env) -> None:
    await fill(env, {1: 500, 2: 900, 3: 100, 4: 900})
    board = await env.svc.leaderboard(G)
    # Gleichstand bei 900: kleinere ID zuerst, überall gleich.
    assert [(r.position, r.user_id, r.xp) for r in board.rows] == [
        (1, 2, 900),
        (2, 4, 900),
        (3, 1, 500),
        (4, 3, 100),
    ]
    for row in board.rows:
        assert (await env.svc.rank(G, row.user_id)).position == row.position
    assert (await env.svc.rank(G, 4)).total == 4


async def test_bestenliste_seiten_und_klemmen(env: Env) -> None:
    await fill(env, {i: 1000 - i for i in range(1, 26)})
    page1 = await env.svc.leaderboard(G, 1)
    assert (page1.pages, page1.total, len(page1.rows)) == (3, 25, PAGE_SIZE)
    page3 = await env.svc.leaderboard(G, 3)
    assert (len(page3.rows), page3.rows[0].position) == (5, 21)
    assert (await env.svc.leaderboard(G, 99)).page == 3
    assert (await env.svc.leaderboard(G, -4)).page == 1


async def test_leere_bestenliste_und_nutzer_ohne_xp(env: Env) -> None:
    board = await env.svc.leaderboard(G)
    assert (board.rows, board.pages, board.total) == ([], 1, 0)
    info = await env.svc.rank(G, U)
    assert (info.progress.xp, info.position, info.total) == (0, 1, 0)


async def test_nutzer_mit_null_xp_stehen_nicht_in_der_bestenliste(env: Env) -> None:
    await env.svc.give(G, 1, 100)
    await env.svc.give(G, 2, 100)
    await env.svc.give(G, 2, -100)
    board = await env.svc.leaderboard(G)
    assert [r.user_id for r in board.rows] == [1]


async def test_bestenliste_ist_serverscoped(env: Env) -> None:
    await env.svc.give(G, 1, 100)
    await env.svc.give(G + 1, 2, 999)
    assert [r.user_id for r in (await env.svc.leaderboard(G)).rows] == [1]


# --- Verwaltung ---


async def test_xp_geben_und_abziehen(env: Env) -> None:
    up = await env.svc.give(G, U, 300)
    assert (up.xp, up.old_level, up.new_level) == (300, 0, 2)
    down = await env.svc.give(G, U, -200)
    assert (down.xp, down.old_level, down.new_level) == (100, 2, 1)
    assert down.level_changed is True
    floor = await env.svc.give(G, U, -9999)
    assert floor.xp == 0  # nie unter 0


async def test_xp_geben_hat_grenzen(env: Env) -> None:
    with pytest.raises(UserError) as exc:
        await env.svc.give(G, U, 10**12)
    assert exc.value.key == "error.leveling_amount_range"
    big = await env.svc.give(G, U, 100_000_000)
    for _ in range(100):
        await env.svc.give(G, U, 100_000_000)
    assert big.xp <= MAX_XP
    assert (await env.svc.rank(G, U)).progress.xp == MAX_XP  # gedeckelt, kein Überlauf


async def test_level_setzen(env: Env) -> None:
    award = await env.svc.set_level(G, U, 10)
    assert (award.xp, award.new_level) == (total_xp_for_level(10), 10)
    lower = await env.svc.set_level(G, U, 3)
    assert (lower.old_level, lower.new_level) == (10, 3)
    for bad in (-1, 1001):
        with pytest.raises(UserError) as exc:
            await env.svc.set_level(G, U, bad)
        assert exc.value.key == "error.leveling_level_range"


async def test_zuruecksetzen(env: Env) -> None:
    await fill(env, {1: 100, 2: 200})
    await env.svc.give(G + 1, 1, 50)
    assert await env.svc.reset_user(G, 1) is True
    assert await env.svc.reset_user(G, 1) is False
    assert await env.svc.reset_all(G) == 1
    assert (await env.svc.leaderboard(G)).total == 0
    assert (await env.svc.rank(G + 1, 1)).progress.xp == 50  # anderer Server unberührt


# --- Import ---


async def test_import_modi(env: Env) -> None:
    await fill(env, {1: 500, 2: 100})
    rows = [ImportRow(1, 300), ImportRow(2, 900), ImportRow(3, 50)]

    result = await env.svc.import_rows(G, rows, "max")
    assert (result.created, result.updated) == (1, 2)
    xp = {r.user_id: r.xp for r in (await env.svc.leaderboard(G)).rows}
    assert xp == {1: 500, 2: 900, 3: 50}  # der höhere Wert bleibt

    await env.svc.import_rows(G, rows, "replace")
    xp = {r.user_id: r.xp for r in (await env.svc.leaderboard(G)).rows}
    assert xp == {1: 300, 2: 900, 3: 50}

    await env.svc.import_rows(G, rows, "add")
    xp = {r.user_id: r.xp for r in (await env.svc.leaderboard(G)).rows}
    assert xp == {1: 600, 2: 1800, 3: 100}


async def test_import_ist_serverscoped_und_deckelt(env: Env) -> None:
    await env.svc.import_rows(G, [ImportRow(1, MAX_XP)], "add")
    await env.svc.import_rows(G, [ImportRow(1, MAX_XP)], "add")
    assert (await env.svc.rank(G, 1)).progress.xp == MAX_XP
    assert (await env.svc.rank(G + 1, 1)).progress.xp == 0


async def test_grosser_import_in_bloecken(env: Env) -> None:
    rows = [ImportRow(i, i) for i in range(1, 2501)]
    result = await env.svc.import_rows(G, rows, "replace")
    assert (result.created, result.updated) == (2500, 0)
    assert (await env.svc.leaderboard(G)).total == 2500
    async with env.db.session() as session:
        count = (await session.execute(text("SELECT count(*) FROM xp_members"))).scalar()
    assert count == 2500


# --- Rank-Card-Stil ---


async def test_kartenstil_persoenlich_oder_serverstandard(env: Env) -> None:
    await env.configure(card={"accent": 0x112233, "template": "dots"})
    default = await env.svc.card_style(G, U)
    assert (default.accent, default.template) == (0x112233, "dots")

    mine = CardStyle(accent=0xFF0000, background=0x000000, template="solid")
    await env.svc.set_card_style(G, U, mine)
    assert await env.svc.card_style(G, U) == mine
    assert (await env.svc.card_style(G, U + 1)).accent == 0x112233  # andere Person: Standard
    assert (await env.svc.card_style(G + 1, U)).accent == 0x5865F2  # anderer Server

    await env.svc.reset_card_style(G, U)
    assert (await env.svc.card_style(G, U)).accent == 0x112233
