#test_parking_workflow.py  ver 1
"""Parking lifecycle tests without valve I/O; actual hardware remains unverified."""

from core.platform_controller import PlatformController
from core.types import ExperienceState, PlatformMotionState, PlatformState, SimSample
from sims.spacecoaster.sim import Sim as SpaceCoasterSim


class FakeBackend:
    def __init__(self):
        self.enabled = False
        self.commands = []
        self.ticks = 0
        self.last_transform = None

    def enable(self): self.enabled = True
    def disable(self): self.enabled = False
    def begin_activate(self, transform):
        self.commands.append(("unpark", tuple(transform)))
        self.ticks = 0
    def begin_deactivate(self):
        self.commands.append(("park",))
        self.ticks = 0
    def service_transition(self):
        self.ticks += 1
        return self.ticks == 3
    def move(self, transform):
        self.commands.append(("move", tuple(transform)))


def test_parked_activation_and_completed_ride_do_not_disarm():
    out = FakeBackend()
    ctrl = PlatformController(out)
    assert ctrl.state is PlatformState.DEACTIVATED and ctrl.parked
    assert ctrl.request_activate()
    assert ctrl.state is PlatformState.ACTIVATED and ctrl.parked
    assert not out.commands
    assert ctrl.request_unpark((0.0,) * 6)
    for _ in range(3): ctrl.service(0.05)
    assert ctrl.parking_state is PlatformMotionState.UNPARKED
    assert ctrl.request_park()
    for _ in range(3): ctrl.service(0.05)
    assert ctrl.parked and ctrl.state is PlatformState.ACTIVATED
    assert ctrl.request_deactivate()
    assert ctrl.state is PlatformState.DEACTIVATED
    assert out.commands == [("unpark", (0.0,) * 6), ("park",)]


def test_deactivation_during_unpark_cancels_and_parks_before_rearm():
    out = FakeBackend()
    ctrl = PlatformController(out)
    assert ctrl.request_activate()
    assert ctrl.request_unpark((0.1,) * 6)
    assert ctrl.request_deactivate()
    assert ctrl.parking_state is PlatformMotionState.PARKING
    assert ctrl.state is PlatformState.DEACTIVATED
    assert not ctrl.request_activate()
    for _ in range(3): ctrl.service(0.05)
    assert ctrl.parked
    assert ctrl.request_activate()


def test_spacecoaster_starting_transform_falls_back_to_first_recording_frame():
    sim = SpaceCoasterSim()
    sample = SimSample(state=ExperienceState.READY, telemetry_valid=False)
    assert sim.starting_transform(sample) == sim._telemetry[sim.start_frame]
    live = SimSample(transform=(0.1,) * 6, state=ExperienceState.READY, telemetry_valid=True)
    assert sim.starting_transform(live) == (0.1,) * 6


def test_spacecoaster_completion_event_only_once():
    from sims.spacecoaster.sim import NativeState

    class Control:
        def lobby(self): return True
    sim = SpaceCoasterSim(control=Control())
    sim.native_state = NativeState.RUNNING
    sim._process_native_state(NativeState.COMPLETED)
    assert sim.consume_normal_completion()
    assert not sim.consume_normal_completion()
