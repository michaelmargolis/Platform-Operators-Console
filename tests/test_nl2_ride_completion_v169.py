#test_nl2_ride_completion_v169.py  ver 2
"""Only the ride-view train returning to station may end a running NL2 ride."""
import time

from core.types import ExperienceState
from sims.nolimits2.sim import Sim
from tests.test_nolimits2_adapter import (
    FakeNL2Client, telemetry, MANUAL_MODE,
    TRAIN_IN_STATION_BIT, CURRENT_RIDE_TRAIN_BIT,
)


def running_sim(station_status):
    client = FakeNL2Client()
    sim = Sim(client=client)
    sim.state = ExperienceState.RUNNING
    sim._initial_sync = False
    sim._ride_station = (0, 0)
    sim._ride_train = (0, 0)
    sim._ride_dispatch_at = time.monotonic() - 1.0
    sim._view_train_departed = True  # first outbound leg already confirmed
    client.station_status = station_status
    client.station_status_received_at = time.monotonic()
    return sim, client


def test_tilt_coaster_other_train_in_station_does_not_end_ride():
    # Observed 2026-10-25: 0x0856 = train in station, not ride-view train.
    sim, _ = running_sim(0x0856)
    sim._update_state(telemetry(speed=0.0))
    assert sim.state is ExperienceState.RUNNING


def test_stopped_view_train_in_station_completes_ride():
    sim, _ = running_sim(MANUAL_MODE | TRAIN_IN_STATION_BIT | CURRENT_RIDE_TRAIN_BIT)
    sim._update_state(telemetry(speed=0.0))
    assert sim.state is ExperienceState.READY


def test_view_train_in_station_while_moving_does_not_end_ride():
    sim, _ = running_sim(MANUAL_MODE | TRAIN_IN_STATION_BIT | CURRENT_RIDE_TRAIN_BIT)
    sim._update_state(telemetry(speed=2.5))
    assert sim.state is ExperienceState.RUNNING


def test_current_train_bit_without_station_bit_does_not_end_ride():
    sim, _ = running_sim(MANUAL_MODE | CURRENT_RIDE_TRAIN_BIT)
    sim._update_state(telemetry(speed=0.0))
    assert sim.state is ExperienceState.RUNNING


def test_departure_guard_prevents_early_completion():
    sim, _ = running_sim(MANUAL_MODE | TRAIN_IN_STATION_BIT | CURRENT_RIDE_TRAIN_BIT)
    sim._view_train_departed = False
    sim._update_state(telemetry(speed=0.0))
    assert sim.state is ExperienceState.RUNNING
