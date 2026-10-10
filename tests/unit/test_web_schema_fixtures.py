"""Die Oberflaeche testet gegen echte Schemas. Dieser Test haelt die Kopie aktuell.

Aendert sich ein Einstellungsmodell, schlaegt er fehl; mit `UPDATE_FIXTURES=1 pytest
tests/unit/test_web_schema_fixtures.py` wird die Datei neu geschrieben, danach laufen
die Web-Tests gegen das neue Schema.
"""

import json
import os
from pathlib import Path

from ggbot.core.settings.registry import MODELS

FIXTURE = Path(__file__).resolve().parents[2] / "web" / "src" / "test" / "schemas.json"


def _current() -> dict[str, object]:
    return {
        name: {"schema": model.model_json_schema(), "defaults": model().model_dump(mode="json")}
        for name, model in MODELS.items()
    }


def test_web_fixture_entspricht_den_modellen() -> None:
    text = json.dumps(_current(), indent=2, ensure_ascii=False) + "\n"
    if os.environ.get("UPDATE_FIXTURES"):
        FIXTURE.write_text(text, encoding="utf-8")
    assert FIXTURE.read_text(encoding="utf-8") == text, (
        "web/src/test/schemas.json ist veraltet: UPDATE_FIXTURES=1 pytest "
        "tests/unit/test_web_schema_fixtures.py"
    )
