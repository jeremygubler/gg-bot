"""Vorschläge, Sticky und temporäre Sprachkanäle gegen die echte Datenbank."""

import asyncio

import pytest
from redis.asyncio import Redis

from ggbot.core.db.engine import Database
from ggbot.core.errors import UserError
from ggbot.core.services.sticky import StickyService
from ggbot.core.services.suggestions import SuggestionService
from ggbot.core.services.tempvoice import TempVoiceService
from ggbot.core.settings.store import SettingsStore
from ggbot.core.settings.suggestions import SuggestionSettings
from ggbot.core.sticky import rules as sticky_rules
from ggbot.core.suggestions import rules

G, OTHER_G = 1, 2
AUTHOR, A, B = 10, 11, 12


@pytest.fixture
def store(db: Database, redis: Redis) -> SettingsStore:
    return SettingsStore(db, redis)


@pytest.fixture
def suggestions(db: Database, store: SettingsStore) -> SuggestionService:
    return SuggestionService(db, store)


# ======================= Vorschläge =======================


async def test_nummern_zaehlen_je_server(suggestions: SuggestionService) -> None:
    first = await suggestions.create(G, AUTHOR, "eins")
    second = await suggestions.create(G, AUTHOR, "zwei")
    other = await suggestions.create(OTHER_G, AUTHOR, "fremd")

    assert (first.number, second.number, other.number) == (1, 2, 1)


async def test_gleichzeitige_vorschlaege_bekommen_verschiedene_nummern(
    suggestions: SuggestionService,
) -> None:
    created = await asyncio.gather(*(suggestions.create(G, AUTHOR, f"v{i}") for i in range(10)))

    assert sorted(c.number for c in created) == list(range(1, 11))


async def test_stimmen_wechseln_und_zuruecknehmen(suggestions: SuggestionService) -> None:
    s = await suggestions.create(G, AUTHOR, "idee")

    assert await suggestions.vote(G, s.id, A, rules.UP) == rules.Tally(1, 0)
    assert await suggestions.vote(G, s.id, B, rules.UP) == rules.Tally(2, 0)
    assert await suggestions.vote(G, s.id, A, rules.DOWN) == rules.Tally(1, 1)  # wechseln
    assert await suggestions.vote(G, s.id, A, rules.DOWN) == rules.Tally(1, 0)  # zurücknehmen


async def test_gleichzeitige_klicks_derselben_person_ergeben_hoechstens_eine_stimme(
    suggestions: SuggestionService,
) -> None:
    s = await suggestions.create(G, AUTHOR, "idee")

    await asyncio.gather(*(suggestions.vote(G, s.id, A, rules.UP) for _ in range(6)))

    tally = await suggestions.tally(s.id)
    assert tally.down == 0
    assert tally.up <= 1  # je nach Reihenfolge 0 (zurückgenommen) oder 1, nie mehr


async def test_eigener_vorschlag_nur_mit_self_vote(
    suggestions: SuggestionService, store: SettingsStore
) -> None:
    s = await suggestions.create(G, AUTHOR, "idee")

    with pytest.raises(UserError) as denied:
        await suggestions.vote(G, s.id, AUTHOR, rules.UP)
    assert denied.value.key == "error.suggest_self_vote"

    await store.save(G, SuggestionSettings(self_vote=True))
    suggestions.invalidate(G)
    assert (await suggestions.vote(G, s.id, AUTHOR, rules.UP)).up == 1


async def test_abstimmen_ueber_fremden_server_findet_nichts(
    suggestions: SuggestionService,
) -> None:
    s = await suggestions.create(G, AUTHOR, "idee")

    with pytest.raises(UserError) as unknown:
        await suggestions.vote(OTHER_G, s.id, A, rules.UP)
    assert unknown.value.key == "error.suggest_unknown"


async def test_entscheiden_geht_nur_einmal(suggestions: SuggestionService) -> None:
    s = await suggestions.create(G, AUTHOR, "idee")

    done = await suggestions.decide(G, s.number, approve=True, decided_by=A, reason=" ok ")
    assert (done.status, done.decided_by, done.decision_reason) == ("approved", A, "ok")

    with pytest.raises(UserError) as again:
        await suggestions.decide(G, s.number, approve=False, decided_by=B, reason=None)
    assert again.value.key == "error.suggest_decided"
    assert (await suggestions.get(G, s.id)).status == "approved"  # type: ignore[union-attr]


async def test_gleichzeitige_entscheidungen_gewinnt_eine(suggestions: SuggestionService) -> None:
    s = await suggestions.create(G, AUTHOR, "idee")

    results = await asyncio.gather(
        suggestions.decide(G, s.number, approve=True, decided_by=A, reason=None),
        suggestions.decide(G, s.number, approve=False, decided_by=B, reason=None),
        return_exceptions=True,
    )

    assert sum(isinstance(r, UserError) for r in results) == 1
    assert sum(not isinstance(r, BaseException) for r in results) == 1


async def test_nach_der_entscheidung_sind_stimmen_zu(suggestions: SuggestionService) -> None:
    s = await suggestions.create(G, AUTHOR, "idee")
    await suggestions.decide(G, s.number, approve=False, decided_by=A, reason=None)

    with pytest.raises(UserError) as closed:
        await suggestions.vote(G, s.id, B, rules.UP)
    assert closed.value.key == "error.suggest_closed"


async def test_unbekannte_nummer(suggestions: SuggestionService) -> None:
    with pytest.raises(UserError) as unknown:
        await suggestions.decide(G, 99, approve=True, decided_by=A, reason=None)
    assert unknown.value.key == "error.suggest_unknown"


# ======================= Sticky =======================


@pytest.fixture
def sticky(db: Database, redis: Redis) -> StickyService:
    return StickyService(db, redis)


async def test_sticky_setzen_ersetzen_entfernen(sticky: StickyService) -> None:
    await sticky.set(G, 100, "erste", A)
    await sticky.set(G, 100, "zweite", A)

    assert [s.text for s in await sticky.list(G)] == ["zweite"]
    gone = await sticky.remove(G, 100)
    assert gone.text == "zweite"
    assert await sticky.list(G) == []
    with pytest.raises(UserError) as unknown:
        await sticky.remove(G, 100)
    assert unknown.value.key == "error.sticky_unknown"


async def test_sticky_obergrenze_je_server(sticky: StickyService) -> None:
    for channel in range(sticky_rules.MAX_STICKY_PER_GUILD):
        await sticky.set(G, 100 + channel, "x", A)

    with pytest.raises(UserError) as full:
        await sticky.set(G, 999, "x", A)
    assert full.value.key == "error.sticky_limit"
    await sticky.set(G, 100, "neu", A)  # Ersetzen zählt nicht mit
    await sticky.set(OTHER_G, 999, "x", A)  # anderer Server hat sein eigenes Kontingent


async def test_sticky_entfernen_trifft_nur_den_eigenen_server(sticky: StickyService) -> None:
    await sticky.set(G, 100, "x", A)

    with pytest.raises(UserError):
        await sticky.remove(OTHER_G, 100)
    assert len(await sticky.list(G)) == 1


async def test_kanalliste_ist_zwischengespeichert_und_wird_ungueltig(
    db: Database, redis: Redis
) -> None:
    now = [0.0]
    service = StickyService(db, redis, clock=lambda: now[0])
    assert await service.channel_ids(G) == frozenset()

    other = StickyService(db, redis)  # anderer Prozess legt eine an
    await other.set(G, 100, "x", A)
    assert await service.channel_ids(G) == frozenset()  # noch aus dem Zwischenspeicher
    now[0] = 31.0
    assert await service.channel_ids(G) == {100}
    await service.set(G, 101, "y", A)
    assert await service.channel_ids(G) == {100, 101}  # eigene Änderung gilt sofort


async def test_neupost_wird_gebuendelt(sticky: StickyService) -> None:
    assert await sticky.claim_repost(100) is True
    assert await sticky.claim_repost(100) is False
    assert await sticky.claim_repost(101) is True  # anderer Kanal


async def test_letzte_nachricht_merken(sticky: StickyService) -> None:
    await sticky.set(G, 100, "x", A)
    await sticky.set_last(100, 555)

    got = await sticky.get(100)
    assert got is not None
    assert got.last_message_id == 555


# ======================= Temporäre Sprachkanäle =======================


@pytest.fixture
def tempvoice(db: Database, store: SettingsStore) -> TempVoiceService:
    return TempVoiceService(db, store)


async def test_besitzer_und_zaehlung(tempvoice: TempVoiceService) -> None:
    await tempvoice.register(G, 200, A)
    await tempvoice.register(G, 201, B)
    await tempvoice.register(OTHER_G, 300, A)

    assert await tempvoice.owner(200) == A
    assert await tempvoice.owner(999) is None
    assert await tempvoice.count(G) == 2
    assert sorted(await tempvoice.channel_ids(G)) == [200, 201]


async def test_nur_ein_aufruf_entfernt_den_eintrag(tempvoice: TempVoiceService) -> None:
    await tempvoice.register(G, 200, A)

    results = await asyncio.gather(*(tempvoice.remove(200) for _ in range(5)))

    assert results.count(True) == 1
    assert await tempvoice.owner(200) is None


async def test_doppelt_anmelden_aendert_den_besitzer_nicht(tempvoice: TempVoiceService) -> None:
    await tempvoice.register(G, 200, A)
    await tempvoice.register(G, 200, B)

    assert await tempvoice.owner(200) == A
