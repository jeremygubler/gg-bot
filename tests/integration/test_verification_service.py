import asyncio

import pytest
from redis.asyncio import Redis

from ggbot.core.db.engine import Database
from ggbot.core.errors import UserError
from ggbot.core.services.autodelete import AutodeleteService
from ggbot.core.services.verification import Outcome, VerificationService
from ggbot.core.settings.autodelete import AutodeleteSettings
from ggbot.core.settings.store import SettingsStore
from ggbot.core.settings.verification import VerificationSettings
from ggbot.core.verification import rules

G, U = 1, 2


@pytest.fixture
def service(db: Database, redis: Redis) -> VerificationService:
    return VerificationService(redis, SettingsStore(db, redis))


async def test_richtige_antwort_loest_die_aufgabe_einmal(service: VerificationService) -> None:
    await service.begin(G, U, "AB2CD")

    first = await service.answer(G, U, "ab2cd")
    second = await service.answer(G, U, "ab2cd")

    assert first.outcome is Outcome.OK
    assert second.outcome is Outcome.EXPIRED  # der Code ist verbraucht


async def test_ohne_aufgabe_ist_die_antwort_abgelaufen(service: VerificationService) -> None:
    assert (await service.answer(G, U, "AB2CD")).outcome is Outcome.EXPIRED


async def test_falsche_antworten_zaehlen_herunter_und_sperren_beim_letzten(
    service: VerificationService,
) -> None:
    await service.begin(G, U, "AB2CD")

    one = await service.answer(G, U, "xxxxx")
    two = await service.answer(G, U, "xxxxx")
    three = await service.answer(G, U, "xxxxx")

    assert (one.outcome, one.remaining) == (Outcome.WRONG, rules.MAX_ATTEMPTS - 1)
    assert (two.outcome, two.remaining) == (Outcome.WRONG, rules.MAX_ATTEMPTS - 2)
    assert three.outcome is Outcome.LOCKED
    # Auch die richtige Antwort hilft jetzt nicht mehr; und es gibt kein neues Bild.
    assert (await service.answer(G, U, "AB2CD")).outcome is Outcome.LOCKED
    with pytest.raises(UserError) as error:
        await service.begin(G, U, "ZZZZZ")
    assert error.value.key == "error.verify_locked"


async def test_versuche_gelten_ueber_mehrere_bilder_hinweg(service: VerificationService) -> None:
    """Sonst liesse sich durch ständig neue Bilder unbegrenzt raten."""
    outcomes = []
    for index in range(rules.MAX_ATTEMPTS):
        await service.begin(G, U, f"AAAA{index + 2}")
        outcomes.append((await service.answer(G, U, "xxxxx")).outcome)

    assert outcomes[-1] is Outcome.LOCKED


async def test_gleichzeitige_antworten_bekommen_keine_zusaetzlichen_versuche(
    service: VerificationService,
) -> None:
    await service.begin(G, U, "AB2CD")

    attempts = await asyncio.gather(*(service.answer(G, U, "xxxxx") for _ in range(10)))

    wrong = [a for a in attempts if a.outcome is Outcome.WRONG]
    assert (
        len(wrong) <= rules.MAX_ATTEMPTS - 1
    )  # höchstens die erlaubten Fehlversuche wurden geprüft
    assert any(a.outcome is Outcome.LOCKED for a in attempts)


async def test_nur_die_richtige_antwort_unter_paralleler_last_kommt_durch(
    service: VerificationService,
) -> None:
    await service.begin(G, U, "AB2CD")

    attempts = await asyncio.gather(
        service.answer(G, U, "AB2CD"), *(service.answer(G, U, "xxxxx") for _ in range(5))
    )

    assert sum(a.outcome is Outcome.OK for a in attempts) <= 1


async def test_nutzer_und_server_sind_getrennt(service: VerificationService) -> None:
    await service.begin(G, U, "AB2CD")
    for _ in range(rules.MAX_ATTEMPTS):
        await service.answer(G, U, "xxxxx")

    await service.begin(G, 3, "AB2CD")  # anderer Nutzer: nicht gesperrt
    await service.begin(9, U, "AB2CD")  # anderer Server: nicht gesperrt

    assert (await service.answer(G, 3, "AB2CD")).outcome is Outcome.OK
    assert (await service.answer(9, U, "AB2CD")).outcome is Outcome.OK


async def test_code_und_sperre_laufen_ab(service: VerificationService, redis: Redis) -> None:
    await service.begin(G, U, "AB2CD")
    assert 0 < await redis.ttl(f"gg:verify:code:{G}:{U}") <= rules.CODE_TTL_SECONDS
    for _ in range(rules.MAX_ATTEMPTS):
        await service.answer(G, U, "xxxxx")

    assert 0 < await redis.ttl(f"gg:verify:lock:{G}:{U}") <= rules.LOCK_SECONDS


async def test_einstellungen_standard_und_speichern(db: Database, redis: Redis) -> None:
    store = SettingsStore(db, redis)
    service = VerificationService(redis, store)
    assert (await service.settings(G)).enabled is False

    await store.save(G, VerificationSettings(enabled=True, role_id=5, mode="code"))
    service.invalidate(G)

    loaded = await service.settings(G)
    assert (loaded.enabled, loaded.role_id, loaded.mode) == (True, 5, "code")


async def test_aktiv_ohne_rolle_wird_abgelehnt() -> None:
    with pytest.raises(ValueError, match="Rolle"):
        VerificationSettings(enabled=True)


async def test_autodelete_lauf_wird_nur_einmal_uebernommen(db: Database, redis: Redis) -> None:
    service = AutodeleteService(redis, SettingsStore(db, redis))

    first = await service.claim_run(G, 70, seconds=60)
    second = await service.claim_run(G, 70, seconds=60)
    other_channel = await service.claim_run(G, 71, seconds=60)

    assert (first, second, other_channel) == (True, False, True)


async def test_autodelete_einstellungen_rundreise(db: Database, redis: Redis) -> None:
    store = SettingsStore(db, redis)
    service = AutodeleteService(redis, store)
    await store.save(
        G,
        AutodeleteSettings.model_validate(
            {"enabled": True, "rules": [{"channel_id": "70", "max_age_minutes": 60}]}
        ),
    )
    service.invalidate(G)

    loaded = await service.settings(G)

    assert loaded.enabled is True
    assert loaded.rules[0].channel_id == 70
