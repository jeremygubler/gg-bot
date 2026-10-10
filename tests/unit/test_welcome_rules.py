import pytest

from ggbot.core.settings.welcome import WelcomeSettings
from ggbot.core.welcome.rules import JoinPlan, plan_join, plan_leave

FULL = WelcomeSettings(
    join_enabled=True,
    join_channel_id=1,
    dm_enabled=True,
    leave_enabled=True,
    leave_channel_id=2,
    autoroles=[10, 11],
    bot_autoroles=[20],
)


def join(settings: WelcomeSettings = FULL, **kw: bool) -> JoinPlan:
    args = {"is_bot": False, "pending": False, "raid": False} | kw
    return plan_join(settings, **args)


def test_normales_mitglied_bekommt_alles() -> None:
    assert join() == JoinPlan(message=True, dm=True, roles=(10, 11))


def test_ausgeschaltet_ist_nichts() -> None:
    plan = join(WelcomeSettings())
    assert plan.nothing
    assert plan == JoinPlan()


def test_nur_einzelne_teile() -> None:
    assert join(WelcomeSettings(dm_enabled=True)) == JoinPlan(dm=True)
    assert join(WelcomeSettings(autoroles=[5])) == JoinPlan(roles=(5,))
    assert join(WelcomeSettings(join_enabled=True, join_channel_id=1)) == JoinPlan(message=True)


def test_screening_haelt_alles_zurueck() -> None:
    # Weder Nachricht noch DM noch Rolle: die Rolle würde das Screening umgehen.
    assert join(pending=True).nothing


def test_raid_modus_ist_still() -> None:
    assert join(raid=True).nothing


def test_bots_bekommen_nur_ihre_autorollen() -> None:
    assert join(is_bot=True) == JoinPlan(roles=(20,))
    assert join(is_bot=True, raid=True) == JoinPlan(roles=(20,))
    assert join(WelcomeSettings(join_enabled=True, join_channel_id=1), is_bot=True).nothing


def test_bot_pending_ist_nicht_moeglich_aber_harmlos() -> None:
    assert join(is_bot=True, pending=True) == JoinPlan(roles=(20,))


@pytest.mark.parametrize("flag", ["is_bot", "pending", "raid"])
def test_abschied_faellt_in_sonderfaellen_weg(flag: str) -> None:
    args = {"is_bot": False, "pending": False, "raid": False} | {flag: True}
    assert plan_leave(FULL, **args) is False


def test_abschied_normal_und_aus() -> None:
    args = {"is_bot": False, "pending": False, "raid": False}
    assert plan_leave(FULL, **args) is True
    assert plan_leave(WelcomeSettings(), **args) is False
