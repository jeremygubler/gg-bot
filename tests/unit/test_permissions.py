import pytest

from ggbot.core.errors import UserError
from ggbot.core.permissions import Principal, ensure_can_target

BOT = Principal(user_id=1, top_role_position=10)


def p(user_id: int, pos: int, owner: bool = False) -> Principal:
    return Principal(user_id=user_id, top_role_position=pos, is_owner=owner)


def test_hoehere_rolle_darf() -> None:
    ensure_can_target(p(2, 5), p(3, 3), BOT)


@pytest.mark.parametrize(
    ("actor", "target", "bot", "key"),
    [
        (p(2, 5), p(2, 5), BOT, "error.hierarchy_self"),
        (p(2, 5), p(3, 1, owner=True), BOT, "error.hierarchy_owner"),
        (p(2, 3), p(3, 3), BOT, "error.hierarchy_actor"),  # gleiche Position reicht nicht
        (p(2, 2), p(3, 3), BOT, "error.hierarchy_actor"),
        (p(2, 9), p(3, 5), p(1, 5), "error.hierarchy_bot"),
        (p(2, 9), p(3, 5), p(1, 4), "error.hierarchy_bot"),
    ],
)
def test_verletzungen(actor: Principal, target: Principal, bot: Principal, key: str) -> None:
    with pytest.raises(UserError) as exc:
        ensure_can_target(actor, target, bot)
    assert exc.value.key == key


def test_besitzer_darf_trotz_niedriger_rolle() -> None:
    ensure_can_target(p(2, 0, owner=True), p(3, 5), BOT)


def test_besitzer_kann_trotzdem_nicht_ueber_den_bot_hinweg() -> None:
    with pytest.raises(UserError) as exc:
        ensure_can_target(p(2, 0, owner=True), p(3, 10), BOT)
    assert exc.value.key == "error.hierarchy_bot"
