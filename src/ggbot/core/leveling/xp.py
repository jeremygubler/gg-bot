"""XP-Kurve, kompatibel zu MEE6: von Level n zu n+1 braucht man 5·n² + 50·n + 100 XP.

Die Kurve ist bewusst fest. Importierte MEE6-Level behalten so dieselbe Bedeutung.
"""

from dataclasses import dataclass

MAX_LEVEL = 1000  # darüber wird nicht mehr gerechnet (das wären über 1,6 Mrd. XP)


def xp_to_next(level: int) -> int:
    """XP, die von `level` bis `level + 1` fehlen."""
    if level < 0:
        raise ValueError("level muss >= 0 sein")
    return 5 * level * level + 50 * level + 100


def total_xp_for_level(level: int) -> int:
    """Gesamt-XP, ab der man `level` erreicht hat (Summe über alle Stufen davor)."""
    if level < 0:
        raise ValueError("level muss >= 0 sein")
    # Σ_{i=0}^{L-1} (5i² + 50i + 100), geschlossen gerechnet und exakt in ganzen Zahlen.
    return 5 * ((level - 1) * level * (2 * level - 1) // 6) + 25 * level * (level - 1) + 100 * level


MAX_XP = total_xp_for_level(MAX_LEVEL)


@dataclass(frozen=True)
class LevelProgress:
    level: int
    xp: int  # Gesamt-XP
    into_level: int  # XP seit Beginn des aktuellen Levels
    needed: int  # XP von diesem Level bis zum nächsten

    @property
    def fraction(self) -> float:
        return self.into_level / self.needed if self.needed else 0.0


def level_from_xp(xp: int) -> int:
    """Das Level zu Gesamt-XP. Negative XP gelten als 0."""
    xp = max(0, min(xp, MAX_XP))
    low, high = 0, MAX_LEVEL
    while low < high:  # grösstes Level mit total_xp_for_level(level) <= xp
        mid = (low + high + 1) // 2
        if total_xp_for_level(mid) <= xp:
            low = mid
        else:
            high = mid - 1
    return low


def progress(xp: int) -> LevelProgress:
    xp = max(0, min(xp, MAX_XP))
    level = level_from_xp(xp)
    return LevelProgress(level, xp, xp - total_xp_for_level(level), xp_to_next(level))
