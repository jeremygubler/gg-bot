import pytest

from ggbot.core.bulk import BulkGate, chunked
from ggbot.core.errors import UserError


def test_chunked_teilt_in_bloecke() -> None:
    assert [list(c) for c in chunked(list(range(5)), 2)] == [[0, 1], [2, 3], [4]]
    assert list(chunked([], 3)) == []


def test_chunked_lehnt_ungueltige_groesse_ab() -> None:
    with pytest.raises(ValueError, match="size"):
        list(chunked([1], 0))


def test_gate_lässt_pro_server_nur_eine_aktion_zu() -> None:
    gate = BulkGate()
    with gate.acquire(1):
        with pytest.raises(UserError) as exc, gate.acquire(1):
            pass
        assert exc.value.key == "error.bulk_busy"
        with gate.acquire(2):  # anderer Server ist nicht betroffen
            pass


def test_gate_gibt_auch_nach_fehler_frei() -> None:
    gate = BulkGate()
    with pytest.raises(RuntimeError), gate.acquire(1):
        raise RuntimeError("boom")
    with gate.acquire(1):
        pass
