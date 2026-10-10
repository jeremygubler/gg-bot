import json

import pytest

from ggbot.core.errors import UserError
from ggbot.core.leveling.importer import MAX_BYTES, MAX_ROWS, parse_import
from ggbot.core.leveling.xp import MAX_XP, total_xp_for_level


def xp_map(raw: bytes) -> dict[int, int]:
    return {row.user_id: row.xp for row in parse_import(raw).rows}


def test_mee6_antwort_mit_players() -> None:
    data = {
        "guild": {"id": "1"},
        "players": [
            {"id": "111", "level": 10, "username": "alice", "xp": 4675, "message_count": 5},
            {"id": "222", "level": 3, "xp": 500},
        ],
    }
    assert xp_map(json.dumps(data).encode()) == {111: 4675, 222: 500}


def test_liste_mehrerer_mee6_seiten() -> None:
    pages = [
        {"players": [{"id": "1", "xp": 100}]},
        {"players": [{"id": "2", "xp": 200}, {"id": "3", "xp": 300}]},
    ]
    assert xp_map(json.dumps(pages).encode()) == {1: 100, 2: 200, 3: 300}


def test_einfache_liste_mit_id_oder_user_id() -> None:
    data = [{"id": 1, "xp": 50}, {"user_id": "2", "xp": "75"}]
    assert xp_map(json.dumps(data).encode()) == {1: 50, 2: 75}


def test_nur_level_wird_in_xp_umgerechnet() -> None:
    assert xp_map(b'[{"id": 1, "level": 10}]') == {1: total_xp_for_level(10)}
    # xp hat Vorrang, wenn beides da ist
    assert xp_map(b'[{"id": 1, "level": 10, "xp": 4700}]') == {1: 4700}


def test_csv_mit_komma_semikolon_und_tab() -> None:
    assert xp_map(b"id,xp\n1,100\n2,200\n") == {1: 100, 2: 200}
    assert xp_map(b"id;xp\n1;100\n") == {1: 100}
    assert xp_map(b"user_id\tlevel\n1\t5\n") == {1: total_xp_for_level(5)}


def test_csv_gross_geschriebene_spalten_und_bom() -> None:
    assert xp_map("﻿ID,XP\n1,100\n".encode()) == {1: 100}


def test_doppelte_nutzer_nehmen_den_hoechsten_wert() -> None:
    assert xp_map(b'[{"id": 1, "xp": 10}, {"id": 1, "xp": 90}, {"id": 1, "xp": 50}]') == {1: 90}


def test_unbrauchbare_zeilen_werden_gezaehlt_nicht_verschluckt() -> None:
    data = [
        {"id": 1, "xp": 10},
        {"id": "abc", "xp": 10},
        {"id": -5, "xp": 10},
        {"id": 2},
        {"id": 3, "xp": -10},
        {"id": 4, "xp": 1.5},
        {"id": True, "xp": 5},
    ]
    parsed = parse_import(json.dumps(data).encode())
    assert [r.user_id for r in parsed.rows] == [1]
    assert parsed.skipped == 6


def test_gigantische_werte_werden_gedeckelt() -> None:
    assert xp_map(b'[{"id": 1, "xp": 99999999999999}]') == {1: MAX_XP}
    assert xp_map(b'[{"id": 1, "level": 99999}]') == {1: total_xp_for_level(1000)}


@pytest.mark.parametrize(
    ("raw", "key"),
    [
        (b"", "error.import_empty"),
        (b"   \n ", "error.import_empty"),
        (b"[]", "error.import_empty"),
        (b'[{"id": "x"}]', "error.import_empty"),
        (b"id,xp\n", "error.import_empty"),
        (b"{kaputt", "error.import_unknown_format"),
        (b'{"nichts": 1}', "error.import_unknown_format"),
        (b'"text"', "error.import_empty"),
        (b"\xff\xfe\x00binaer", "error.import_unknown_format"),
    ],
)
def test_ungueltige_dateien(raw: bytes, key: str) -> None:
    with pytest.raises(UserError) as exc:
        parse_import(raw)
    assert exc.value.key == key


def test_zu_gross_und_zu_viele_zeilen() -> None:
    with pytest.raises(UserError) as exc:
        parse_import(b"x" * (MAX_BYTES + 1))
    assert exc.value.key == "error.import_too_large"
    many = json.dumps([{"id": i + 1, "xp": 1} for i in range(MAX_ROWS + 1)]).encode()
    with pytest.raises(UserError) as exc:
        parse_import(many)
    assert exc.value.key == "error.import_too_many"
