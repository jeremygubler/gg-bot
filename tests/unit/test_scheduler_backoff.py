from datetime import timedelta

from ggbot.core.scheduler import backoff


def test_backoff_verdoppelt_und_ist_gedeckelt() -> None:
    assert backoff(1) == timedelta(seconds=30)
    assert backoff(2) == timedelta(seconds=60)
    assert backoff(3) == timedelta(seconds=120)
    assert backoff(50) == timedelta(hours=1)
