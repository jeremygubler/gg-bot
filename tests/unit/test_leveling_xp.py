import pytest

from ggbot.core.leveling.xp import (
    MAX_LEVEL,
    MAX_XP,
    level_from_xp,
    progress,
    total_xp_for_level,
    xp_to_next,
)


@pytest.mark.parametrize(
    ("level", "needed"), [(0, 100), (1, 155), (2, 220), (5, 475), (10, 1100), (50, 15100)]
)
def test_xp_pro_stufe_nach_mee6_formel(level: int, needed: int) -> None:
    assert xp_to_next(level) == 5 * level**2 + 50 * level + 100 == needed


@pytest.mark.parametrize(
    ("level", "total"), [(0, 0), (1, 100), (2, 255), (3, 475), (5, 1150), (10, 4675)]
)
def test_gesamt_xp_von_hand_nachgerechnet(level: int, total: int) -> None:
    # 5 → 100 + 155 + 220 + 295 + 380; 10 → Σ(5i² + 50i + 100) für i = 0..9 = 1425 + 2250 + 1000.
    assert total_xp_for_level(level) == total


def test_gesamt_xp_ist_die_summe_der_stufen() -> None:
    running = 0
    for level in range(0, 120):
        assert total_xp_for_level(level) == running
        running += xp_to_next(level)


def test_level_aus_xp_an_den_grenzen() -> None:
    assert level_from_xp(0) == 0
    assert level_from_xp(99) == 0
    assert level_from_xp(100) == 1
    assert level_from_xp(254) == 1
    assert level_from_xp(255) == 2
    assert level_from_xp(4674) == 9
    assert level_from_xp(4675) == 10


def test_level_roundtrip_fuer_viele_werte() -> None:
    for level in range(0, MAX_LEVEL + 1, 7):
        total = total_xp_for_level(level)
        assert level_from_xp(total) == level
        if level:
            assert level_from_xp(total - 1) == level - 1


def test_extremwerte() -> None:
    assert level_from_xp(-50) == 0
    assert level_from_xp(MAX_XP) == MAX_LEVEL
    assert level_from_xp(MAX_XP * 10) == MAX_LEVEL  # nicht darüber hinaus, keine Endlosschleife
    assert total_xp_for_level(MAX_LEVEL) == MAX_XP


def test_negative_level_sind_fehler() -> None:
    with pytest.raises(ValueError, match="level"):
        xp_to_next(-1)
    with pytest.raises(ValueError, match="level"):
        total_xp_for_level(-1)


def test_fortschritt() -> None:
    p = progress(150)  # Level 1 ab 100, bis 255 → 50 von 155
    assert (p.level, p.xp, p.into_level, p.needed) == (1, 150, 50, 155)
    assert p.fraction == pytest.approx(50 / 155)
    start = progress(100)
    assert (start.level, start.into_level) == (1, 0)
    assert start.fraction == 0.0
    assert progress(-5).level == 0
    assert progress(0).fraction == 0.0
