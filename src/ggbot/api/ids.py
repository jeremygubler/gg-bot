"""ID-Parameter in Pfaden, auf den Bereich der Datenbank begrenzt.

Ohne Grenze reicht Postgres eine zu grosse Zahl nicht still durch, sondern wirft einen Fehler
(`value out of int64 range`): aus einer beliebigen URL würde ein 500, auch ohne Anmeldung.
"""

from typing import Annotated

from fastapi import Path

from ggbot.core.settings.types import MAX_SNOWFLAKE

Id = Annotated[int, Path(ge=0, le=MAX_SNOWFLAKE)]
