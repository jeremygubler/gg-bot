"""Giveaways: wer teilnehmen darf und wie Gewinner gezogen werden. Rein, Zufall kommt herein."""

from collections.abc import Callable, Collection, Sequence

MAX_WINNERS = 20
MAX_ACTIVE = 10


def may_enter(required_role_id: int | None, member_role_ids: Collection[int]) -> bool:
    return required_role_id is None or required_role_id in set(member_role_ids)


def pick_winners(
    entrants: Sequence[int],
    count: int,
    *,
    exclude: Collection[int] = (),
    sample: Callable[[list[int], int], list[int]],
) -> list[int]:
    """Bis zu `count` verschiedene Gewinner aus den Teilnehmern ohne `exclude`.

    `sample(population, k)` zieht k verschiedene Elemente (z. B. `random.sample`). Gibt es weniger
    Teilnehmer als Plätze, gewinnen alle; gibt es keine, bleibt die Liste leer.
    """
    skipped = set(exclude)
    pool = sorted(set(entrants) - skipped)  # sortiert: gleiche Eingabe, gleiche Ziehung
    if count <= 0 or not pool:
        return []
    return sample(pool, min(count, len(pool)))
