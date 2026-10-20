#test_parking_poc.py  ver 1
"""PocCore lifecycle tests, with physical output replaced by a recording backend."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytest.importorskip("PyQt5")
from core.poc_core import PocCore
from core.platform_controller import PlatformController
from core.types import ExperienceState, PlatformState, PlatformMotionState, SimSample, SimStatus
from core.sim_interface import SimInterface
from tests.test_parking_workflow import FakeBackend


class FakeRemote:
    def service(self): pass


class FakeSim:
    name = "Test Simulator"
    supports_washout = True
    def __init__(self):
        self.state = ExperienceState.READY
        self.valid = True
        self.events = []
        self.pose = (0.1, 0, 0, 0, 0, 0)
    def read(self):
        return SimSample(self.pose, self.state, self.valid)
    def get_status(self):
        return SimStatus(
            pc_connected=True, sim_connected=True, data_connected=self.valid,
            ready_to_start=self.state is ExperienceState.READY,
        )
    def starting_transform(self, sample):
        return SimInterface.starting_transform(self, sample)
    def run(self): self.events.append("run")
    def pause(self):
        self.events.append("pause")
        self.state = ExperienceState.PAUSED
    def recover_from_estop(self):
        self.events.append("recover")
        self.state = ExperienceState.RESETTING
    def prepare_for_activation(self): self.events.append("prepare")
    def cancel_pending_dispatch(self): self.events.append("cancel")


class Config:
    def get(self, name, default=None):
        return {"selected_platform": "chair_v2", "sim_host_service": {"enabled": False},
                "digital_twin": {"address": "-"}}.get(name, default)


@pytest.fixture
def poc():
    with patch("core.poc_core.SopPlatformBackend", lambda *a, **kw: FakeBackend()), \
         patch.object(PocCore, "_create_physical_control_interface", lambda self, a: FakeRemote()):
        core = PocCore(Config())
    core.sim = FakeSim()
    core.sim_key = "nolimits2"
    core._last_sample = core.sim.read()
    core._set_experience_state(ExperienceState.READY)
    core.telemetry_config = None
    return core


def service(core, n=3):
    for _ in range(n):
        core.platform.service(0.05)
        core._advance_pending_dispatch()


def test_station_switch_off_has_no_physical_motion(poc):
    assert poc.platform.parked
    poc._begin_activation("test")
    assert poc.ready_for_dispatch
    poc._begin_deactivation()
    assert poc.platform.state is PlatformState.DEACTIVATED
    assert poc.platform.parked
    assert poc.platform.backend.commands == []
    assert not poc._estop_latched


def test_unpark_before_sim_run_and_normal_completion_parks(poc):
    poc._begin_activation("test")
    poc.request_run()
    assert poc.platform.parking_state is PlatformMotionState.UNPARKING
    assert "run" not in poc.sim.events
    service(poc)
    assert poc.platform.parking_state is PlatformMotionState.UNPARKED
    assert "run" in poc.sim.events
    poc.sim.state = ExperienceState.RUNNING
    poc._handle_normal_completion(poc.sim.read())
    poc.sim.state = ExperienceState.READY
    poc._handle_normal_completion(poc.sim.read())
    assert poc.platform.parking_state is PlatformMotionState.PARKING
    assert poc.platform.state is PlatformState.ACTIVATED
    for _ in range(3): poc.platform.service(0.05)
    assert poc.ready_for_dispatch


def test_estop_during_unpark_cancels_dispatch_and_waits_for_park(poc):
    poc._begin_activation("test")
    poc.request_run()
    poc._begin_deactivation()
    assert poc._estop_latched
    assert poc.platform.state is PlatformState.DEACTIVATED
    assert poc.platform.parking_state is PlatformMotionState.PARKING
    service(poc)
    assert "run" not in poc.sim.events
    assert poc.platform.parked
    poc._begin_activation("test")
    # Already READY for NL2, so no reset is needed.
    assert not poc._estop_latched
    assert "recover" not in poc.sim.events


def test_running_estop_pauses_once_and_requires_recovery(poc):
    poc._begin_activation("test")
    poc.request_run()
    service(poc)
    poc.sim.state = ExperienceState.RUNNING
    poc._set_experience_state(ExperienceState.RUNNING)
    poc._begin_deactivation()
    assert poc._estop_latched
    assert poc.sim.events.count("pause") == 1
    assert poc.platform.parking_state is PlatformMotionState.PARKING
    service(poc)
    poc._begin_activation("test")
    assert "recover" in poc.sim.events
    assert poc._estop_latched
