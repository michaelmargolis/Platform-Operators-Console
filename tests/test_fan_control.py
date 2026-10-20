#test_fan_control.py  ver 4
from types import SimpleNamespace

from core.poc_core import PocCore
from core.types import ExperienceState, PlatformState, SimSample
from sims.nolimits2 import config as nl2_config
from sims.spacecoaster import config as spacecoaster_config
from sims.xplane import config as xplane_config


class FakeHost:
    def __init__(self):
        self.commands = []

    def send_fan_power(self, speed, power):
        self.commands.append((speed, power))
        return True


class FakeCore:
    FAN_SEND_PERIOD_S = 0.1
    _calculate_fan_power = staticmethod(PocCore._calculate_fan_power)
    _update_fan = PocCore._update_fan

    def __init__(self):
        self.host_service_enabled = True
        self.host_service = FakeHost()
        self.platform = SimpleNamespace(state=PlatformState.ACTIVATED)
        self.sim = SimpleNamespace(fan_config={
            "speed_at_100": 40.0,
            "threshold_speed": 1.4,
            "threshold_power": 13.0,
            "max_power": 70.0,
            "speed_power_table": (
                (0.0, 8.0), (2.5, 14.0), (5.0, 25.0), (10.0, 39.0),
                (20.0, 50.0), (30.0, 75.0), (40.0, 100.0),
            ),
        })
        self._last_fan_power = None
        self._last_fan_send_time = -1e9


def test_fan_power_mapping_reproduces_legacy_threshold_curve_and_scaling():
    config = FakeCore().sim.fan_config
    assert PocCore._calculate_fan_power(None, config) == 0
    assert PocCore._calculate_fan_power(1.4, config) == 0
    assert PocCore._calculate_fan_power(1.5, config) == 19
    assert PocCore._calculate_fan_power(2.5, config) == 20
    assert PocCore._calculate_fan_power(5.0, config) == 27
    assert PocCore._calculate_fan_power(10.0, config) == 35
    assert PocCore._calculate_fan_power(20.0, config) == 41
    assert PocCore._calculate_fan_power(30.0, config) == 55
    assert PocCore._calculate_fan_power(40.0, config) == 70
    assert PocCore._calculate_fan_power(50.0, config) == 70


def test_initial_simulator_specific_fan_configs():
    spacecoaster_shaping = {
        key: value
        for key, value in spacecoaster_config.FAN_CONFIG.items()
        if not key.startswith("position_speed_")
    }
    assert spacecoaster_shaping == nl2_config.FAN_CONFIG
    assert spacecoaster_config.FAN_CONFIG["position_speed_scale"] == 1.0 / 9.0
    assert spacecoaster_config.FAN_CONFIG["position_speed_median_samples"] == 5
    assert spacecoaster_config.FAN_CONFIG["position_speed_average_s"] == 1.0
    assert PocCore._calculate_fan_power(25.0, xplane_config.FAN_CONFIG) == 50


def test_fan_is_forced_off_unless_platform_activated_and_sim_running(monkeypatch):
    monkeypatch.setattr("core.poc_core.time.monotonic", lambda: 10.0)
    core = FakeCore()
    running = SimSample((0.0,) * 6, ExperienceState.RUNNING, True, 10.0)
    core._update_fan(running)
    assert core.host_service.commands[-1] == (10.0, 35)

    core.platform.state = PlatformState.DEACTIVATED
    core._update_fan(running)
    assert core.host_service.commands[-1] == (10.0, 0)

    core.platform.state = PlatformState.ACTIVATED
    paused = SimSample((0.0,) * 6, ExperienceState.PAUSED, True, 10.0)
    core._update_fan(paused)
    assert core.host_service.commands[-1] == (10.0, 0)
