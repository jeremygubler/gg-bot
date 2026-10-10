import pytest

from ggbot.core.leveling.rules import (
    VoiceMember,
    effective_multiplier,
    excluded,
    reward_plan,
    scaled,
    voice_eligible,
)

# --- Ausnahmen ---


def test_kein_xp_in_ausgenommenem_kanal_oder_mit_ausgenommener_rolle() -> None:
    kwargs = {"no_xp_channels": [10], "no_xp_roles": [50]}
    assert excluded(channel_ids=[10, None], role_ids=[1], **kwargs)
    assert excluded(channel_ids=[11, 10], role_ids=[1], **kwargs)  # Thread im Kanal 10
    assert excluded(channel_ids=[11], role_ids=[1, 50], **kwargs)
    assert not excluded(channel_ids=[11, None], role_ids=[1], **kwargs)
    assert not excluded(channel_ids=[None], role_ids=[], no_xp_channels=[], no_xp_roles=[])


# --- Multiplikatoren ---


def test_multiplikator_hoechste_rolle_gewinnt_und_stapelt_nicht() -> None:
    value = effective_multiplier(
        base=1.0,
        role_ids=[1, 2, 3],
        channel_ids=[],
        role_multipliers={1: 1.5, 2: 2.0, 3: 1.2},
        channel_multipliers={},
    )
    assert value == 2.0


def test_multiplikator_kombiniert_global_rolle_und_kanal() -> None:
    value = effective_multiplier(
        base=2.0,
        role_ids=[1],
        channel_ids=[10, None],
        role_multipliers={1: 1.5},
        channel_multipliers={10: 3.0},
    )
    assert value == pytest.approx(9.0)


def test_multiplikator_ohne_treffer_ist_der_globale() -> None:
    assert (
        effective_multiplier(
            base=1.25,
            role_ids=[9],
            channel_ids=[9],
            role_multipliers={1: 2},
            channel_multipliers={2: 2},
        )
        == 1.25
    )


def test_multiplikator_unter_eins_wirkt_als_bremse() -> None:
    value = effective_multiplier(
        base=1.0, role_ids=[1], channel_ids=[], role_multipliers={1: 0.5}, channel_multipliers={}
    )
    assert value == 0.5


def test_skalieren_rundet_und_bleibt_nicht_negativ() -> None:
    assert scaled(20, 1.5) == 30
    assert scaled(15, 0.5) == 8  # 7,5 rundet auf die gerade 8 (bankers rounding)
    assert scaled(15, 0.0) == 0
    assert scaled(10, -2.0) == 0


# --- Belohnungen ---

REWARDS = {5: 105, 10: 110, 20: 120}


def test_stapeln_gibt_alle_erreichten_rollen() -> None:
    plan = reward_plan(REWARDS, "stack", 12, set())
    assert plan.add == {105, 110}
    assert plan.remove == frozenset()


def test_stapeln_fasst_vorhandenes_nicht_doppelt_an() -> None:
    plan = reward_plan(REWARDS, "stack", 12, {105, 999})
    assert plan.add == {110}
    assert plan.remove == frozenset()


def test_ersetzen_behaelt_nur_die_hoechste_und_entfernt_die_alten() -> None:
    plan = reward_plan(REWARDS, "replace", 12, {105})
    assert plan.add == {110}
    assert plan.remove == {105}


def test_ersetzen_entfernt_nur_belohnungsrollen() -> None:
    plan = reward_plan(REWARDS, "replace", 25, {110, 999})
    assert plan.add == {120}
    assert plan.remove == {110}  # 999 ist keine Belohnung und bleibt


def test_unter_dem_ersten_level_gibt_es_nichts() -> None:
    assert reward_plan(REWARDS, "stack", 4, set()) == reward_plan(REWARDS, "replace", 4, set())
    assert reward_plan(REWARDS, "stack", 4, set()).add == frozenset()


def test_level_sinkt_entfernt_zu_hohe_belohnungen() -> None:
    plan = reward_plan(REWARDS, "stack", 6, {105, 110, 120})
    assert plan.remove == {110, 120}
    assert plan.add == frozenset()


def test_ohne_belohnungen_passiert_nichts() -> None:
    plan = reward_plan({}, "replace", 50, {1, 2})
    assert (plan.add, plan.remove) == (frozenset(), frozenset())


def test_gleiche_rolle_fuer_mehrere_stufen() -> None:
    plan = reward_plan({5: 100, 10: 100}, "replace", 12, {100})
    assert plan.add == frozenset()
    assert plan.remove == frozenset()  # die Rolle ist erwünscht, also bleibt sie


# --- Voice ---


def vm(user_id: int, *, bot: bool = False, muted: bool = False) -> VoiceMember:
    return VoiceMember(user_id, bot, muted)


def test_allein_im_kanal_gibt_es_nichts_mit_gesellschaftspflicht() -> None:
    assert voice_eligible([vm(1)], requires_company=True) == set()
    assert voice_eligible([vm(1)], requires_company=False) == {1}


def test_zu_zweit_bekommen_beide_xp() -> None:
    assert voice_eligible([vm(1), vm(2)], requires_company=True) == {1, 2}


def test_bots_sind_keine_gesellschaft_und_bekommen_nichts() -> None:
    assert voice_eligible([vm(1), vm(99, bot=True)], requires_company=True) == set()
    assert voice_eligible([vm(1), vm(2), vm(99, bot=True)], requires_company=True) == {1, 2}


def test_stumme_bekommen_nichts_zaehlen_aber_als_gesellschaft() -> None:
    assert voice_eligible([vm(1), vm(2, muted=True)], requires_company=True) == {1}
    assert voice_eligible([vm(1, muted=True), vm(2, muted=True)], requires_company=True) == set()


def test_leerer_kanal() -> None:
    assert voice_eligible([], requires_company=False) == set()
