# test_sim_registry.py ver 1

from core.sim_registry import OPERATOR_SIMS, SIMS, definition_for


def test_xplane_is_retained_but_not_operator_selectable():
    assert definition_for("xplane").module == "sims.xplane.sim"
    assert "xplane" in {item.key for item in SIMS}
    assert "xplane" not in {item.key for item in OPERATOR_SIMS}


def test_deployed_operator_simulators_remain_available():
    assert [item.key for item in OPERATOR_SIMS] == ["nolimits2", "spacecoaster"]
