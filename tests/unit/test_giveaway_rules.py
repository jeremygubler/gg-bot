import random
from collections.abc import Callable

import pytest

from ggbot.core.giveaway.rules import may_enter, pick_winners


def seeded(seed: int = 1) -> Callable[[list[int], int], list[int]]:
    return random.Random(seed).sample  # noqa: S311 - Test mit festem Seed


def test_teilnahme_ohne_rollenpflicht() -> None:
    assert may_enter(None, set())
    assert may_enter(None, {1, 2})


def test_teilnahme_mit_rollenpflicht() -> None:
    assert may_enter(5, {5, 6})
    assert not may_enter(5, {6})
    assert not may_enter(5, set())


def test_gewinner_sind_verschieden_und_teilnehmer() -> None:
    entrants = list(range(1, 11))
    winners = pick_winners(entrants, 3, sample=seeded())
    assert len(winners) == 3
    assert len(set(winners)) == 3
    assert set(winners) <= set(entrants)


def test_weniger_teilnehmer_als_plaetze() -> None:
    assert sorted(pick_winners([4, 5], 5, sample=seeded())) == [4, 5]


def test_niemand_oder_keine_plaetze() -> None:
    assert pick_winners([], 3, sample=seeded()) == []
    assert pick_winners([1, 2], 0, sample=seeded()) == []
    assert pick_winners([1, 2], -1, sample=seeded()) == []


def test_ausgeschlossene_gewinnen_nie() -> None:
    for seed in range(30):
        assert 7 not in pick_winners([5, 6, 7, 8], 3, exclude=[7], sample=seeded(seed))


def test_alle_ausgeschlossen() -> None:
    assert pick_winners([1, 2], 2, exclude=[1, 2], sample=seeded()) == []


def test_doppelte_teilnehmer_zaehlen_einmal() -> None:
    for seed in range(30):
        winners = pick_winners([1, 1, 1, 2], 2, sample=seeded(seed))
        assert sorted(winners) == [1, 2]


def test_gleiche_eingabe_gleiche_ziehung() -> None:
    assert pick_winners([3, 1, 2, 9, 8], 2, sample=seeded(4)) == pick_winners(
        [9, 8, 3, 2, 1], 2, sample=seeded(4)
    )  # Reihenfolge der Eingabe spielt keine Rolle


def test_jeder_teilnehmer_kann_gewinnen() -> None:
    seen: set[int] = set()
    for seed in range(200):
        seen.update(pick_winners([1, 2, 3, 4], 1, sample=seeded(seed)))
    assert seen == {1, 2, 3, 4}


def test_sample_wird_mit_zahl_aufgerufen() -> None:
    calls: list[tuple[list[int], int]] = []

    def spy(population: list[int], k: int) -> list[int]:
        calls.append((population, k))
        return population[:k]

    assert pick_winners([5, 3, 4], 2, sample=spy) == [3, 4]
    assert calls == [([3, 4, 5], 2)]


@pytest.mark.parametrize("count", [1, 2, 3])
def test_anzahl_ist_hoechstens_count(count: int) -> None:
    assert len(pick_winners(list(range(10)), count, sample=seeded())) == count
