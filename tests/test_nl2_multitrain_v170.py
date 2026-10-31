# test_nl2_multitrain_v170.py ver 1
"""Ridden-train state, pinned station, auxiliary dispatch, and safety gates."""
import io
import struct
import time

from core.types import ExperienceState
from sims.nolimits2.nl2_protocol import NL2Protocol
from sims.nolimits2.sim import Sim
from tests.test_nolimits2_adapter import (
    FakeNL2Client, telemetry, fresh, set_station,
    MANUAL_MODE, CAN_DISPATCH,
    TRAIN_IN_STATION_BIT, CURRENT_RIDE_TRAIN_BIT,
)
from tools.capture_nl2_rides import RideStateMonitor

VIEW = MANUAL_MODE | TRAIN_IN_STATION_BIT | CURRENT_RIDE_TRAIN_BIT
OTHER = MANUAL_MODE | TRAIN_IN_STATION_BIT | CAN_DISPATCH
EMPTY = MANUAL_MODE


def running_setup(station=(0, 0)):
    client = FakeNL2Client()
    client.is_connected = True
    client.last_response_at = time.monotonic()
    client.current_coaster_index, client.current_station_index = station
    sim = Sim(client=client)
    sim.state = ExperienceState.READY
    set_station(client, VIEW | CAN_DISPATCH)
    fresh(client, speed=0.0, frame=1)
    sim.run()
    # Make station state newer than request to authorize the actual dispatch.
    set_station(client, VIEW | CAN_DISPATCH)
    sim._service_dispatch_preparation()
    assert sim.state is ExperienceState.RUNNING
    assert sim._ride_station == station
    assert sim._ride_train == (0, 0)
    assert sim._aux_dispatch_authorized
    assert [x for x in client.commands if x[0] == 'dispatch'] == [('dispatch', *station)]
    return sim, client


def observe(sim, client, bits, speed, frame, time_stamp=True):
    set_station(client, bits)
    if not time_stamp:
        client.station_status_received_at = 0.0
    t = telemetry(speed=speed, frame=frame)
    client.latest_telemetry = t
    sim._update_state(t)


def test_another_train_stationary_at_tilt_dispatches_once_without_completing():
    sim, c = running_setup()
    observe(sim, c, OTHER, 12.0, 2)
    assert sim._view_train_departed
    assert sim.state is ExperienceState.RUNNING
    assert [x for x in c.commands if x[0] == 'dispatch'] == [('dispatch', 0, 0), ('dispatch', 0, 0)]
    observe(sim, c, OTHER, 0.0, 3)  # actual Tilt Coaster status 0x0856
    assert sim.state is ExperienceState.RUNNING
    assert len([x for x in c.commands if x[0] == 'dispatch']) == 2
    observe(sim, c, OTHER, 0.0, 4)
    assert len([x for x in c.commands if x[0] == 'dispatch']) == 2


def test_new_train_can_dispatch_after_station_observed_empty():
    sim, c = running_setup()
    observe(sim, c, OTHER, 12.0, 2)
    observe(sim, c, EMPTY, 10.0, 3)
    observe(sim, c, OTHER, 0.0, 4)
    assert len([x for x in c.commands if x[0] == 'dispatch']) == 3


def test_only_current_train_return_completes_ride():
    sim, c = running_setup()
    observe(sim, c, OTHER, 15.0, 2)
    observe(sim, c, OTHER, 0.0, 3)
    assert sim.state is ExperienceState.RUNNING
    observe(sim, c, VIEW, 0.0, 4)
    assert sim.state is ExperienceState.READY
    assert not sim._aux_dispatch_authorized
    assert sim.is_ready_to_start()


def test_does_not_complete_before_departure_confirmed():
    sim, c = running_setup()
    observe(sim, c, VIEW, 0.0, 2)
    assert not sim._view_train_departed
    assert sim.state is ExperienceState.RUNNING


def test_deactivation_revoke_prevents_auxiliary_dispatch():
    sim, c = running_setup()
    sim.cancel_pending_dispatch()
    observe(sim, c, OTHER, 15.0, 2)
    assert sim._view_train_departed
    assert len([x for x in c.commands if x[0] == 'dispatch']) == 1


def test_pause_prevents_auxiliary_dispatch():
    sim, c = running_setup()
    sim.pause()
    observe(sim, c, OTHER, 15.0, 2)
    assert len([x for x in c.commands if x[0] == 'dispatch']) == 1
    # Explicit resume after confirmed pause restores permission, unless cancelled.
    sim.state = ExperienceState.PAUSED
    sim._pause_target = None
    sim.pause()
    sim._pause_target = None
    sim.state = ExperienceState.RUNNING
    observe(sim, c, OTHER, 15.0, 3)
    assert len([x for x in c.commands if x[0] == 'dispatch']) == 2


def test_stale_station_replies_do_not_dispatch_or_complete():
    sim, c = running_setup()
    observe(sim, c, OTHER, 15.0, 2, time_stamp=False)
    # Simulated fake client without request-identity metadata has fallback for
    # timestamp=0, so install production-like identity validation here.
    c.station_status_station = (0, 0)
    observe(sim, c, OTHER, 15.0, 3, time_stamp=False)
    assert len([x for x in c.commands if x[0] == 'dispatch']) == 1
    observe(sim, c, OTHER, 15.0, 4)
    assert len([x for x in c.commands if x[0] == 'dispatch']) == 2
    c.station_status_station = (0, 1)
    observe(sim, c, VIEW, 0.0, 5)
    assert sim.state is ExperienceState.RUNNING


def test_view_train_switch_disables_auxiliary_dispatch_and_completion():
    sim, c = running_setup()
    set_station(c, OTHER)
    tm = telemetry(speed=15.0, frame=2)
    tm = type(tm)(**{**tm.__dict__, 'train': 2})
    sim._update_state(tm)
    assert len([x for x in c.commands if x[0] == 'dispatch']) == 1
    assert sim.state is ExperienceState.RUNNING


def test_station_status_reply_matches_requested_identity():
    class Sock:
        def __init__(self): self.packets = []
        def sendall(self, p): self.packets.append(p)

    protocol = NL2Protocol('127.0.0.1')
    protocol.is_connected = True
    protocol._sock = Sock()
    protocol.request_station_state(1, 2)
    reqid = struct.unpack('>I', protocol._sock.packets[-1][3:7])[0]
    protocol._process_message(protocol.N_MSG_STATION_STATE, reqid + 3,
                              struct.pack('>I', OTHER))
    assert protocol.station_status is None
    protocol._process_message(protocol.N_MSG_STATION_STATE, reqid,
                              struct.pack('>I', OTHER))
    assert protocol.station_status == OTHER
    assert protocol.station_status_station == (1, 2)
    protocol.invalidate_current_station()
    assert protocol.station_status_station is None


def test_state_monitor_reports_only_changes():
    sim, c = running_setup()
    stream = io.StringIO()
    monitor = RideStateMonitor(stream, 'Test Coaster')
    monitor.observe(sim, telemetry(speed=0.0))
    baseline = len(stream.getvalue().splitlines())
    monitor.observe(sim, telemetry(speed=0.0))
    assert len(stream.getvalue().splitlines()) == baseline
    observe(sim, c, OTHER, 12.0, 2)
    monitor.observe(sim, telemetry(speed=12.0))
    assert 'aux_dispatch_sent' in stream.getvalue()
    assert 'view_train_departed' in stream.getvalue()


def test_startup_does_not_claim_other_train_ready_to_dispatch():
    c = FakeNL2Client()
    c.is_connected = True
    c.last_response_at = time.monotonic()
    sim = Sim(client=c)
    sim.state = ExperienceState.READY
    set_station(c, OTHER)
    fresh(c, speed=0.0)
    assert sim._stopped_in_station() is False
    assert sim.is_ready_to_start() is False
    assert not any(cmd[0] == 'dispatch' for cmd in c.commands)


def test_dispatch_preparation_refuses_other_train_or_wrong_station():
    c = FakeNL2Client()
    c.is_connected = True
    c.last_response_at = time.monotonic()
    sim = Sim(client=c)
    sim.state = ExperienceState.READY
    fresh(c, speed=0.0)
    set_station(c, OTHER)
    sim.run()
    set_station(c, OTHER)
    sim._service_dispatch_preparation()
    assert sim._dispatch_pending
    assert not any(cmd[0] == 'dispatch' for cmd in c.commands)
    # A station-state reply addressed to a different station is invalid.
    c.station_status_station = (0, 2)
    set_station(c, VIEW | CAN_DISPATCH)
    sim._service_dispatch_preparation()
    assert sim._dispatch_pending
    assert not any(cmd[0] == 'dispatch' for cmd in c.commands)


def test_station_polling_stays_pinned_during_multitrain_ride():
    sim, c = running_setup((0, 0))
    c.commands.clear()
    # Nearest-station information can change as ridden train moves away.
    c.current_station_index = 3
    sim._request_station_state()
    assert ('get_station', 0, 0) in c.commands
    assert ('get_station', 0, 3) not in c.commands
