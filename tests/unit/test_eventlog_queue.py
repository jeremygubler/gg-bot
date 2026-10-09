import pytest

from ggbot.core.logs.queue import ChannelBacklog


def test_reihenfolge_bleibt_erhalten() -> None:
    backlog: ChannelBacklog[int] = ChannelBacklog(limit=5)
    for i in range(3):
        assert backlog.push(i)
    assert [backlog.pop(), backlog.pop(), backlog.pop(), backlog.pop()] == [0, 1, 2, None]


def test_ueberlauf_wird_gezaehlt_statt_gepuffert() -> None:
    backlog: ChannelBacklog[int] = ChannelBacklog(limit=2)
    results = [backlog.push(i) for i in range(5)]
    assert results == [True, True, False, False, False]
    assert len(backlog) == 2
    assert backlog.take_dropped() == 3
    assert backlog.take_dropped() == 0  # zurückgesetzt


def test_nach_dem_abarbeiten_ist_wieder_platz() -> None:
    backlog: ChannelBacklog[int] = ChannelBacklog(limit=1)
    backlog.push(1)
    assert backlog.push(2) is False
    backlog.pop()
    assert backlog.push(3) is True
    assert backlog.pop() == 3


def test_ungueltiges_limit() -> None:
    with pytest.raises(ValueError, match="limit"):
        ChannelBacklog(limit=0)
