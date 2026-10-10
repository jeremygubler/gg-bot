import pytest
from pydantic import BaseModel, ValidationError

from ggbot.core.settings import registry
from ggbot.core.settings.schema import SettingsModel
from ggbot.core.settings.types import ChannelId, RoleId, Snowflake


def _all_models() -> set[type[SettingsModel]]:
    # Alle Module importieren, damit __subclasses__ vollständig ist.
    found: set[type[SettingsModel]] = set()
    todo = list(SettingsModel.__subclasses__())
    while todo:
        cls = todo.pop()
        found.add(cls)
        todo.extend(cls.__subclasses__())
    return found


def test_jedes_einstellungsmodell_ist_im_register() -> None:
    assert _all_models() == set(registry.MODELS.values())


def test_modulnamen_sind_eindeutig() -> None:
    assert len(registry.MODELS) == len(_all_models())


def test_jedes_modell_liefert_schema_und_standardwerte() -> None:
    for model in registry.MODELS.values():
        assert model.model_json_schema()["type"] == "object"
        assert model().model_dump(mode="json") is not None


class _M(BaseModel):
    a: Snowflake = 0
    b: ChannelId | None = None
    c: list[RoleId] = []


def test_snowflake_akzeptiert_zahl_und_ziffernstring() -> None:
    m = _M.model_validate({"a": "9007199254740993", "b": 7, "c": ["1", 2]})

    assert (m.a, m.b, m.c) == (9007199254740993, 7, [1, 2])


def test_snowflake_gibt_im_json_string_aus_und_bleibt_int_in_python() -> None:
    m = _M(a=9007199254740993, b=7, c=[1])

    assert m.model_dump(mode="json") == {"a": "9007199254740993", "b": "7", "c": ["1"]}
    assert m.model_dump() == {"a": 9007199254740993, "b": 7, "c": [1]}
    assert _M.model_validate_json(m.model_dump_json()) == m


@pytest.mark.parametrize("bad", ["", " 1", "1 ", "1\n", "-1", "１２３", "1e3", "0x10", "1" * 21])
def test_snowflake_lehnt_unsaubere_strings_ab(bad: str) -> None:
    with pytest.raises(ValidationError):
        _M.model_validate({"a": bad})
