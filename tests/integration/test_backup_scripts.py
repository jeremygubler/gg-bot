"""Backup und Restore mit echtem pg_dump/pg_restore gegen eine eigene Wegwerf-Datenbank.

Ein Backup-Skript, das nie zurückgespielt wurde, ist ein Wunsch, kein Backup: dieser Test spielt
den ganzen Weg durch (sichern, Daten kaputtmachen, zurückspielen) und prüft die Randfälle.
"""

import os
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlparse

import pytest
from alembic import command
from alembic.config import Config

from tests.conftest import ROOT, TEST_DB_URL

DB = "ggbot_backup_test"
URL = urlparse(TEST_DB_URL.replace("+asyncpg", ""))
ENV = {
    **os.environ,
    "PGHOST": URL.hostname or "localhost",
    "PGPORT": str(URL.port or 5432),
    "PGUSER": URL.username or "ggbot",
    "PGPASSWORD": URL.password or "",
    "POSTGRES_USER": URL.username or "ggbot",
    "POSTGRES_DB": DB,
    "PG_EXEC": "",
    "STOP_CMD": "true",
    "FLUSH_CMD": "true",
    "START_CMD": "true",
}


def run(
    args: list[str], *, env: dict[str, str] | None = None, input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603  # feste Argumente aus dem Test
        args,
        env=env or ENV,
        input=input_text,
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
    )


def sql(statement: str, db: str = DB) -> str:
    result = run(["psql", "-d", db, "-tA", "-c", statement])
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.fixture
def database() -> Iterator[None]:
    run(["dropdb", "--if-exists", DB])
    assert run(["createdb", DB]).returncode == 0
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", TEST_DB_URL.rsplit("/", 1)[0] + f"/{DB}")
    command.upgrade(cfg, "head")
    sql(
        "INSERT INTO guild_settings (guild_id, module, data, version) "
        "VALUES (1, 'general', '{\"a\": 1}', 1)"
    )
    yield
    run(["dropdb", "--if-exists", DB])


def backup(directory: Path, **extra: str) -> subprocess.CompletedProcess[str]:
    return run(["scripts/backup.sh"], env={**ENV, "BACKUP_DIR": str(directory), **extra})


def test_sichern_zerstoeren_zurueckspielen(database: None, tmp_path: Path) -> None:
    result = backup(tmp_path)
    assert result.returncode == 0, result.stderr
    file = Path(result.stdout.strip())
    assert file.parent == tmp_path
    assert file.stat().st_size > 0
    assert oct(file.stat().st_mode & 0o777) == "0o600"  # Backups enthalten alle Daten
    assert not list(tmp_path.glob("*.partial"))

    sql("DELETE FROM guild_settings")
    sql("INSERT INTO guild_settings (guild_id, module, data, version) VALUES (2, 'neu', '{}', 1)")
    restored = run(["scripts/restore.sh", "--yes", str(file)])

    assert restored.returncode == 0, restored.stderr
    assert sql("SELECT guild_id || ':' || module FROM guild_settings ORDER BY 1") == "1:general"
    assert sql("SELECT data->>'a' FROM guild_settings") == "1"
    # Das Schema stimmt: Alembic-Stand ist mit zurückgekommen.
    assert sql("SELECT count(*) FROM alembic_version") == "1"


def test_ungueltige_datei_wird_vor_dem_eingriff_abgelehnt(database: None, tmp_path: Path) -> None:
    junk = tmp_path / "kaputt.dump"
    junk.write_text("das ist kein Backup")

    result = run(["scripts/restore.sh", "--yes", str(junk)])

    assert result.returncode != 0
    assert sql("SELECT count(*) FROM guild_settings") == "1"  # unberührt


def test_fehlende_datei_und_aufruf(database: None) -> None:
    assert run(["scripts/restore.sh", "--yes", "gibt-es-nicht.dump"]).returncode == 2
    assert run(["scripts/restore.sh"]).returncode == 2


def test_ohne_ausdrueckliche_bestaetigung_passiert_nichts(database: None, tmp_path: Path) -> None:
    file = Path(backup(tmp_path).stdout.strip())
    sql("DELETE FROM guild_settings")

    result = run(["scripts/restore.sh", str(file)], input_text="ja\n")

    assert result.returncode != 0
    assert sql("SELECT count(*) FROM guild_settings") == "0"  # nicht zurückgespielt
    confirmed = run(["scripts/restore.sh", str(file)], input_text="RESTORE\n")
    assert confirmed.returncode == 0, confirmed.stderr
    assert sql("SELECT count(*) FROM guild_settings") == "1"


def test_restore_haelt_dienste_an_leert_redis_und_startet_neu(
    database: None, tmp_path: Path
) -> None:
    file = Path(backup(tmp_path).stdout.strip())
    log = tmp_path / "ablauf.txt"
    env = {
        **ENV,
        "STOP_CMD": f"sh -c 'echo stop >> {log}'",
        "FLUSH_CMD": f"sh -c 'echo flush >> {log}'",
        "START_CMD": f"sh -c 'echo start >> {log}'",
    }

    result = run(["scripts/restore.sh", "--yes", str(file)], env=env)

    assert result.returncode == 0, result.stderr
    assert log.read_text().split() == ["stop", "flush", "start"]


def test_fehlgeschlagener_restore_startet_nichts_und_leert_nichts(
    database: None, tmp_path: Path
) -> None:
    file = Path(backup(tmp_path).stdout.strip())
    log = tmp_path / "ablauf.txt"
    env = {
        **ENV,
        "POSTGRES_DB": "gibt_es_nicht",
        "STOP_CMD": f"sh -c 'echo stop >> {log}'",
        "FLUSH_CMD": f"sh -c 'echo flush >> {log}'",
        "START_CMD": f"sh -c 'echo start >> {log}'",
    }

    result = run(["scripts/restore.sh", "--yes", str(file)], env=env)

    assert result.returncode != 0
    assert "start" not in log.read_text().split()  # kaputte Datenbank nicht ans Netz
    assert "flush" not in log.read_text().split()


def test_fehlgeschlagenes_backup_hinterlaesst_keine_datei(database: None, tmp_path: Path) -> None:
    result = backup(tmp_path, POSTGRES_DB="gibt_es_nicht")

    assert result.returncode != 0
    assert list(tmp_path.iterdir()) == []


def test_aeltere_backups_werden_gedreht(database: None, tmp_path: Path) -> None:
    old = []
    for index in range(4):
        path = tmp_path / f"ggbot-2020010{index}T000000Z.dump"
        path.write_bytes(b"alt")
        stamp = time.time() - (10 - index) * 86400
        os.utime(path, (stamp, stamp))
        old.append(path)
    keep_other = tmp_path / "notizen.txt"
    keep_other.write_text("gehört dem Betreiber")

    result = backup(tmp_path, BACKUP_KEEP="2")

    assert result.returncode == 0, result.stderr
    remaining = sorted(p.name for p in tmp_path.glob("ggbot-*.dump"))
    assert len(remaining) == 2
    assert Path(result.stdout.strip()).name in remaining  # das frische bleibt
    assert old[3].name in remaining  # und das jüngste alte
    assert keep_other.exists()  # fremde Dateien bleiben unberührt
