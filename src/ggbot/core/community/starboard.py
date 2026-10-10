"""Starboard: welche Sterne zählen und was bei einem Stand zu tun ist. Rein."""

from collections.abc import Collection
from typing import Literal

Action = Literal["post", "update", "remove", "none"]


def counts(
    *,
    voter_id: int,
    voter_is_bot: bool,
    author_id: int,
    channel_id: int,
    starboard_channel_id: int,
    ignored_channels: Collection[int],
    self_star: bool,
    nsfw: bool,
    ignore_nsfw: bool,
) -> bool:
    """Zählt dieser Stern?

    Nicht: Sterne von Bots, im Starboard-Kanal selbst (sonst sammelten Kopien wieder Sterne und
    kopierten sich endlos), in ignorierten oder (auf Wunsch) NSFW-Kanälen, und nicht der eigene
    Stern des Autors, solange `self_star` aus ist.
    """
    if voter_is_bot or channel_id == starboard_channel_id or channel_id in set(ignored_channels):
        return False
    if nsfw and ignore_nsfw:
        return False
    return self_star or voter_id != author_id


def action(stars: int, threshold: int, posted: bool) -> Action:
    """Posten ab der Schwelle; danach nur aktualisieren; fällt der Stand darunter, entfernen."""
    if stars >= threshold:
        return "update" if posted else "post"
    return "remove" if posted else "none"


def star_line(emoji: str, stars: int, channel_mention: str) -> str:
    return f"{emoji} **{stars}** | {channel_mention}"
