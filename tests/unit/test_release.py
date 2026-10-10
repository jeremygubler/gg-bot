"""Versionen, Changelog und Release-Ablauf müssen zusammenpassen; sonst scheitert erst das Tag."""

import json
import os
import re
import subprocess
import tomllib
from pathlib import Path

import pytest

from ggbot import __version__

ROOT = Path(__file__).resolve().parents[2]


def notes(version: str, changelog: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, **({"CHANGELOG": str(changelog)} if changelog else {})}
    return subprocess.run(  # noqa: S603  # feste Argumente aus dem Test
        [str(ROOT / "scripts" / "release-notes.sh"), version],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        cwd=ROOT,
    )


def test_alle_versionsangaben_sind_gleich() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    web = json.loads((ROOT / "web" / "package.json").read_text())["version"]
    lock = json.loads((ROOT / "web" / "package-lock.json").read_text())
    uv_lock = re.search(r'name = "gg-bot"\nversion = "([^"]+)"', (ROOT / "uv.lock").read_text())

    assert uv_lock is not None
    assert {
        __version__,
        pyproject,
        web,
        lock["version"],
        lock["packages"][""]["version"],
        uv_lock[1],
    } == {__version__}


def test_changelog_hat_einen_abschnitt_fuer_die_aktuelle_version() -> None:
    result = notes(__version__)

    assert result.returncode == 0, result.stderr
    assert len(result.stdout.strip()) > 100


def test_release_notes_enthalten_nur_den_gewuenschten_abschnitt(tmp_path: Path) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "# Changelog\n\n## [2.0.0] - 2030-01-01\nNeu in zwei.\n\n"
        "## [1.0.0] - 2029-01-01\nAlt in eins.\n"
    )

    assert notes("2.0.0", changelog).stdout.strip() == "Neu in zwei."
    assert notes("1.0.0", changelog).stdout.strip() == "Alt in eins."


@pytest.mark.parametrize("version", ["9.9.9", "0.11", "", "[0.11.0]x"])
def test_unbekannte_version_gibt_kein_leeres_release(version: str) -> None:
    result = notes(version)

    assert result.returncode != 0
    assert result.stdout == ""


def test_leerer_abschnitt_zaehlt_als_fehlend(tmp_path: Path) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("## [3.0.0] - 2030-01-01\n\n\n## [2.0.0] - 2029-01-01\nText\n")

    assert notes("3.0.0", changelog).returncode != 0


def test_release_workflow_laeuft_nur_auf_tags_und_prueft_die_version() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text()

    assert 'tags: ["v*"]' in workflow
    assert "release-notes.sh" in workflow
    assert "pyproject" in workflow
    assert "contents: write" in workflow
    assert "packages: write" in workflow
    assert "branches" not in workflow  # nie auf Branch-Pushes


def test_audit_laeuft_woechentlich() -> None:
    workflow = (ROOT / ".github" / "workflows" / "audit.yml").read_text()

    assert "schedule:" in workflow
    assert "pip-audit" in workflow
    assert "npm audit" in workflow
