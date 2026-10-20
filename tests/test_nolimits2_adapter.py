#test_nolimits2_adapter.py  ver 28
from __future__ import annotations

import logging
import math
import socket
import struct
import threading
import time

from core.types import ExperienceState
from sims.nolimits2 import config
import sims.nolimits2.nl2_protocol as nl2_protocol_module
from sims.nolimits2.nl2_protocol import NL2Protocol, NL2Telemetry
from sims.nolimits2.sim import Sim


MANUAL_MODE = 0x0002
TRAIN_IN_STATION_BIT = 0x0800
CURRENT_RIDE_TRAIN_BIT = 0x1000
TRAIN_IN_STATION_BITS = TRAIN_IN_STATION_BIT | CURRENT_RIDE_TRAIN_BIT
TRAIN_IN_STATION = MANUAL_MODE | TRAIN_IN_STATION_BITS
CAN_DISPATCH = 0x0004
PLATFORM_CAN_LOWER = 0x0100
HARNESS_CAN_CLOSE = 0x0020


class FakeNL2Client:
    def __init__(self):
        self.is_connected = False
        self.latest_telemetry = None
        self.current_coaster_index = 0
        self.current_station_index = 0
        self.current_station_received_at = time.monotonic()
        self.station_status = None
        self.station_status_received_at = 0.0
        self.last_error = ""
        self.version = "2.5.3.5"
        self.last_response_at = 0.0
        self.commands = []
        self._request_results = {}
        self._next_request_id = 100

    def connect(self):
        self.is_connected = True
        self.last_response_at = time.monotonic()
        self.commands.append(("connect",))
        return True

    def disconnect(self):
        self.is_connected = False
        self.commands.append(("disconnect",))

    def service(self):
        pass

    def response_is_fresh(self, max_age_s, now=None):
        if not self.is_connected or self.last_response_at <= 0.0:
            return False
        if now is None:
            now = time.monotonic()
        return now - self.last_response_at <= max_age_s

    def request_version(self):
        self.commands.append(("get_version",))
        self.last_response_at = time.monotonic()

    def request_telemetry(self):
        self.commands.append(("get_telemetry",))

    def request_current_coaster_and_station(self):
        self.commands.append(("get_current_station",))
        # Fake replies synchronously unless a test deliberately overrides these.
        if self.current_coaster_index is None:
            self.current_coaster_index = 0
        if self.current_station_index is None:
            self.current_station_index = 0
        self.current_station_received_at = time.monotonic()

    def invalidate_current_station(self):
        self.current_coaster_index = None
        self.current_station_index = None
        self.current_station_received_at = 0.0
        self.station_status = None
        self.station_status_received_at = 0.0

    def request_station_state(self, coaster=0, station=0):
        self.commands.append(("get_station", int(coaster), int(station)))

    def set_manual_mode(self, enabled=True, coaster=0, station=0):
        self.commands.append(("manual", bool(enabled), int(coaster), int(station)))

    def load_park(self, park_path, start_paused=True):
        request_id = self._next_request_id
        self._next_request_id += 1
        self.commands.append(("load_park", park_path, bool(start_paused)))
        return request_id

    def request_result(self, request_id):
        return self._request_results.get(request_id)

    def complete_request(self, request_id, ok=True, detail=""):
        self._request_results[request_id] = ("ok", "") if ok else ("error", detail)
        self.last_response_at = time.monotonic()

    def reset_park(self, start_paused=False):
        self.commands.append(("reset", bool(start_paused)))

    def dispatch(self, coaster=0, station=0):
        self.commands.append(("dispatch", int(coaster), int(station)))

    def set_platform(self, lowered, coaster=0, station=0):
        self.commands.append(("platform", bool(lowered), int(coaster), int(station)))

    def set_pause(self, paused):
        self.commands.append(("pause", bool(paused)))

    def recenter_vr(self):
        self.commands.append(("reset_vr",))


def telemetry(
    speed=0.0,
    state=3,
    frame=1,
    pos_y=16.0,
    gx=0.25,
    gy=1.0,
    gz=0.36,
    quaternion=(0.0, 0.0, 0.0, 1.0),
    received_at=None,
):
    return NL2Telemetry(
        state=state,
        frame=int(frame),
        view_mode=0,
        coaster_index=0,
        coaster_style=0,
        train=0,
        car=0,
        seat=0,
        speed=float(speed),
        pos_x=0.0,
        pos_y=float(pos_y),
        pos_z=0.0,
        quat_x=float(quaternion[0]),
        quat_y=float(quaternion[1]),
        quat_z=float(quaternion[2]),
        quat_w=float(quaternion[3]),
        g_force_x=float(gx),
        g_force_y=float(gy),
        g_force_z=float(gz),
        received_at=time.monotonic() if received_at is None else float(received_at),
    )


def fresh(client, **kwargs):
    client.latest_telemetry = telemetry(**kwargs)
    client.last_response_at = client.latest_telemetry.received_at


def set_station(client, status):
    client.station_status = status
    client.station_status_received_at = time.monotonic()
    client.last_response_at = client.station_status_received_at


def connect_and_service(sim):
    sim.connect()
    sim.service()
    if sim._ride_load_pending and sim._ride_load_request_id is not None:
        complete = getattr(sim.client, "complete_request", None)
        if callable(complete):
            complete(sim._ride_load_request_id, ok=True)
            # First read consumes the LOAD_PARK acknowledgement and deliberately
            # discards any cached pre-load observations.  Then provide fresh
            # post-load state so legacy tests begin from the same READY baseline.
            set_station(sim.client, TRAIN_IN_STATION)
            fresh(sim.client, speed=0.0, state=3)
            sim.read()
            set_station(sim.client, TRAIN_IN_STATION)
            fresh(sim.client, speed=0.0, state=3)
            sim.read()


def axis_quaternion(axis, angle):
    values = [0.0, 0.0, 0.0]
    values[axis] = math.sin(angle / 2.0)
    return (*values, math.cos(angle / 2.0))


def test_quaternion_conversion_uses_nl2_vehicle_axes():
    angle = 0.4

    roll, pitch, yaw = Sim._quaternion_to_rpy(*axis_quaternion(2, angle))
    assert math.isclose(roll, angle, abs_tol=1.0e-9)
    assert math.isclose(pitch, 0.0, abs_tol=1.0e-9)
    assert math.isclose(yaw, 0.0, abs_tol=1.0e-9)

    roll, pitch, yaw = Sim._quaternion_to_rpy(*axis_quaternion(0, angle))
    assert math.isclose(roll, 0.0, abs_tol=1.0e-9)
    assert math.isclose(pitch, angle, abs_tol=1.0e-9)
    assert math.isclose(yaw, 0.0, abs_tol=1.0e-9)

    roll, pitch, yaw = Sim._quaternion_to_rpy(*axis_quaternion(1, angle))
    assert math.isclose(roll, 0.0, abs_tol=1.0e-9)
    assert math.isclose(pitch, 0.0, abs_tol=1.0e-9)
    assert math.isclose(yaw, angle, abs_tol=1.0e-9)


def test_yaw_rate_uses_relative_quaternion_with_low_pass_and_no_sqrt_shaping():
    sim = Sim(client=FakeNL2Client())
    start_time = 100.0
    sim._transform_from_telemetry(
        telemetry(frame=10, quaternion=axis_quaternion(1, 0.0), received_at=start_time)
    )
    transform = sim._transform_from_telemetry(
        telemetry(
            frame=11,
            quaternion=axis_quaternion(1, 0.05),
            received_at=start_time + 0.05,
        )
    )

    alpha = 0.05 / (config.YAW_RATE_LOW_PASS_TAU_S + 0.05)
    expected = alpha * config.YAW_RATE_GAIN * config.TELEMETRY_INTENSITY_FACTOR
    assert math.isclose(transform[5], expected, rel_tol=1.0e-9)


def test_yaw_low_pass_decays_when_orientation_stops_changing():
    sim = Sim(client=FakeNL2Client())
    start_time = 300.0
    sim._transform_from_telemetry(
        telemetry(frame=40, quaternion=axis_quaternion(1, 0.0), received_at=start_time)
    )
    moving = sim._transform_from_telemetry(
        telemetry(frame=41, quaternion=axis_quaternion(1, 0.05), received_at=start_time + 0.05)
    )[5]
    stopped = sim._transform_from_telemetry(
        telemetry(frame=42, quaternion=axis_quaternion(1, 0.05), received_at=start_time + 0.10)
    )[5]

    assert 0.0 < stopped < moving


def test_quaternion_sign_flip_does_not_create_yaw_motion():
    sim = Sim(client=FakeNL2Client())
    quaternion = axis_quaternion(1, 0.5)
    sim._transform_from_telemetry(
        telemetry(frame=20, quaternion=quaternion, received_at=200.0)
    )
    transform = sim._transform_from_telemetry(
        telemetry(
            frame=21,
            quaternion=tuple(-value for value in quaternion),
            received_at=200.05,
        )
    )
    assert math.isclose(transform[5], 0.0, abs_tol=1.0e-9)


def test_repeated_nl2_frame_holds_previous_transform():
    client = FakeNL2Client()
    sim = Sim(client=client)
    client.is_connected = True
    client.last_response_at = time.monotonic()
    sim._connected_sync_started = True
    sim.state = ExperienceState.RUNNING
    set_station(client, MANUAL_MODE)

    first_time = time.monotonic()
    fresh(client, frame=30, quaternion=axis_quaternion(1, 0.0), received_at=first_time)
    first = sim.read().transform

    fresh(
        client,
        frame=31,
        quaternion=axis_quaternion(1, 0.05),
        received_at=first_time + 0.05,
    )
    second = sim.read().transform
    assert second[5] > first[5]

    # A cached packet must not create an artificial zero-rate sample.
    repeated = sim.read().transform
    assert repeated == second


def test_nl2_heave_mode_is_session_selectable():
    sim = Sim(client=FakeNL2Client())
    legacy = sim._transform_from_telemetry(telemetry(pos_y=7.4, gy=1.5))
    assert math.isclose(legacy[2], -0.50, abs_tol=1.0e-9)

    sim.set_legacy_heave_enabled(False)
    conventional = sim._transform_from_telemetry(telemetry(pos_y=7.4, gy=1.5))
    assert math.isclose(
        conventional[2], 0.5 * config.TELEMETRY_INTENSITY_FACTOR, abs_tol=1.0e-9
    )


def test_mdx_logo_legacy_height_profile_clamps_to_configured_endpoints():
    sim = Sim(client=FakeNL2Client())

    assert math.isclose(
        sim._transform_from_telemetry(telemetry(pos_y=0.0))[2],
        -0.50,
        abs_tol=1.0e-9,
    )
    assert math.isclose(
        sim._transform_from_telemetry(telemetry(pos_y=20.0))[2],
        -0.34,
        abs_tol=1.0e-9,
    )
    assert math.isclose(
        sim._transform_from_telemetry(telemetry(pos_y=40.0))[2],
        -0.18,
        abs_tol=1.0e-9,
    )


def test_startup_always_loads_configured_default_even_if_station_ready():
    client = FakeNL2Client()
    sim = Sim(client=client)
    sim.connect()
    sim.service()

    assert sim.state is ExperienceState.RESETTING
    loads = [cmd for cmd in client.commands if cmd[0] == "load_park"]
    assert len(loads) == 1
    assert loads[0][1] == sim._rides[sim._selected][1]
    assert loads[0][2] is True

    # No park transition is required: an OK for the exact LOAD_PARK request is
    # sufficient even if the same default park was already running.  Cached
    # old-park state is discarded; fresh state then establishes READY.
    request_id = sim._ride_load_request_id
    client.complete_request(request_id, ok=True)
    set_station(client, TRAIN_IN_STATION)
    fresh(client, speed=0.0, state=3)
    sim.read()
    assert sim._ride_load_pending is False
    assert sim.state is ExperienceState.READY
    assert sim.get_status().busy is False
    assert not any(cmd[0] == "reset" for cmd in client.commands)

    set_station(client, TRAIN_IN_STATION)
    fresh(client, speed=0.0, state=3)
    sim.read()
    assert sim.state is ExperienceState.READY
    assert not any(cmd[0] == "reset" for cmd in client.commands)


def test_activation_recovery_resets_if_train_not_in_station():
    client = FakeNL2Client()
    sim = Sim(client=client)
    connect_and_service(sim)
    client.complete_request(sim._ride_load_request_id, ok=True)
    set_station(client, TRAIN_IN_STATION)
    fresh(client, speed=0.0, state=3)
    sim.read()
    time.sleep(0.002)
    set_station(client, TRAIN_IN_STATION)
    fresh(client, speed=0.0, state=3)
    sim.read()

    set_station(client, MANUAL_MODE)
    fresh(client, speed=5.0, state=3)
    sim.state = ExperienceState.READY

    # Park reset is an activation/recovery operation, not a connection side
    # effect. Once activation preparation is requested, the same out-of-station
    # condition must trigger RESET_PARK(False).
    sim.prepare_for_activation()
    assert sim.state is ExperienceState.RESETTING
    assert ("reset", False) in client.commands


def test_simple_nl2_state_and_control_sequence():
    client = FakeNL2Client()
    sim = Sim(client=client)

    connect_and_service(sim)
    set_station(client, TRAIN_IN_STATION | CAN_DISPATCH)
    fresh(client, speed=0.0, state=3)
    sample = sim.read()
    assert sim.state is ExperienceState.READY
    assert sample.telemetry_valid
    assert len(sample.transform) == 6
    assert any(abs(v) > 0.0 for v in sample.transform[:3])

    # Dispatch preparation is non-blocking but remains READY until the actual
    # NL2 DISPATCH command is sent.
    sim.run()
    assert sim.state is ExperienceState.READY
    assert not any(cmd[0] == "dispatch" for cmd in client.commands)
    time.sleep(0.002)
    set_station(client, MANUAL_MODE | TRAIN_IN_STATION_BITS | CAN_DISPATCH)
    sim.service()
    assert any(cmd[0] == "dispatch" for cmd in client.commands)
    assert sim.state is ExperienceState.RUNNING

    # Still stationary in the station immediately after dispatch: the legacy
    # leaving-station guard must prevent a false end-of-ride transition.
    fresh(client, speed=0.0, state=3)
    sim.read()
    assert sim.state is ExperienceState.RUNNING

    # Train leaves station.
    set_station(client, MANUAL_MODE)
    fresh(client, speed=8.0, state=3)
    sim.read()
    assert sim.state is ExperienceState.RUNNING

    # Pause/unpause state is telemetry-authoritative, as in the tested coaster.
    sim.pause()
    assert sim.state is ExperienceState.RUNNING
    assert client.commands[-1] == ("pause", True)
    fresh(client, speed=8.0, state=3)
    sim.read()
    assert sim.state is ExperienceState.RUNNING
    fresh(client, speed=8.0, state=7)
    sim.read()
    assert sim.state is ExperienceState.PAUSED

    sim.pause()
    assert sim.state is ExperienceState.PAUSED
    assert client.commands[-1] == ("pause", False)
    fresh(client, speed=8.0, state=7)
    sim.read()
    assert sim.state is ExperienceState.PAUSED
    fresh(client, speed=8.0, state=3)
    sim.read()
    assert sim.state is ExperienceState.RUNNING

    # Normal ride completion: after leaving the station, stopped + returned to
    # the station maps back to READY.
    set_station(client, TRAIN_IN_STATION)
    fresh(client, speed=0.0, state=3)
    sim.read()
    assert sim.state is ExperienceState.READY

    # The operator reset action is VR recenter only.
    sim.reset_vr()
    assert client.commands[-1] == ("reset_vr",)


def test_aborted_recovery_only_resets_when_needed():
    client = FakeNL2Client()
    sim = Sim(client=client)
    connect_and_service(sim)

    # Already stopped in station: activation preparation is immediate and does
    # not reset the park.
    set_station(client, TRAIN_IN_STATION)
    fresh(client, speed=0.0, state=3)
    sim.read()
    reset_count = sum(cmd[0] == "reset" for cmd in client.commands)
    sim.state = ExperienceState.RUNNING
    sim.prepare_for_activation()
    assert sim.state is ExperienceState.READY
    assert sum(cmd[0] == "reset" for cmd in client.commands) == reset_count

    # Out of station: activation recovery sends reset and stays RESETTING until
    # fresh post-reset telemetry confirms return to station.
    set_station(client, MANUAL_MODE)
    fresh(client, speed=6.0, state=3)
    sim.state = ExperienceState.RUNNING
    sim.prepare_for_activation()
    assert sim.state is ExperienceState.RESETTING
    assert any(cmd == ("reset", False) for cmd in client.commands)

    time.sleep(0.002)
    set_station(client, TRAIN_IN_STATION)
    fresh(client, speed=0.0, state=3)
    sim.read()
    assert sim.state is ExperienceState.READY


def test_no_telemetry_during_reset_is_valid_contract():
    client = FakeNL2Client()
    sim = Sim(client=client)
    connect_and_service(sim)
    sim.state = ExperienceState.RESETTING
    client.latest_telemetry = None
    sample = sim.read()
    assert sim.state is ExperienceState.RESETTING
    assert sample.telemetry_valid is False


def test_resetting_state_is_busy_even_between_pending_substates():
    client = FakeNL2Client()
    sim = Sim(client=client)
    connect_and_service(sim)
    sim.state = ExperienceState.RESETTING
    sim._ride_load_pending = False
    sim._park_reset_pending = False
    sim._reset_requested_at = 0.0
    sim._manual_mode_requested = False
    sim._dispatch_pending = False

    assert sim.get_status().busy is True


def test_ready_never_auto_transitions_to_running_from_telemetry():
    client = FakeNL2Client()
    sim = Sim(client=client)
    connect_and_service(sim)

    # Startup synchronization establishes READY while stopped in station.
    set_station(client, TRAIN_IN_STATION)
    fresh(client, speed=0.0, state=3)
    sim.read()
    assert sim.state is ExperienceState.READY

    # Activation is a POC/platform operation and must not start the ride.
    # Even if NL2 subsequently reports moving telemetry, READY remains READY
    # until POC explicitly calls run()/dispatch().
    set_station(client, MANUAL_MODE)
    fresh(client, speed=5.0, state=3)
    sim.read()
    assert sim.state is ExperienceState.READY
    assert not any(cmd[0] == "dispatch" for cmd in client.commands)

    # The same rule applies after reset completion.
    sim.state = ExperienceState.RESETTING
    sim._reset_requested_at = time.monotonic() - 0.01
    set_station(client, TRAIN_IN_STATION)
    fresh(client, speed=0.0, state=3)
    sim.read()
    assert sim.state is ExperienceState.READY

    set_station(client, MANUAL_MODE)
    fresh(client, speed=5.0, state=3)
    sim.read()
    assert sim.state is ExperienceState.READY

    # Explicit dispatch starts preparation, but RUNNING begins only when the
    # actual NL2 dispatch is sent after fresh CAN_DISPATCH confirmation.
    sim.run()
    assert sim.state is ExperienceState.READY
    time.sleep(0.002)
    set_station(client, MANUAL_MODE | TRAIN_IN_STATION_BITS | CAN_DISPATCH)
    sim.service()
    assert any(cmd[0] == "dispatch" for cmd in client.commands)
    assert sim.state is ExperienceState.RUNNING


def test_auto_dispatch_disabled_before_ready_and_no_dispatch_sent():
    client = FakeNL2Client()
    sim = Sim(client=client)
    connect_and_service(sim)

    # NL2 reports the train stopped in station, but station manual-mode bit is
    # not set.  The adapter stays in its stable operator-waiting READY state,
    # requests manual mode, and must not become ready_to_start or dispatch.
    set_station(client, TRAIN_IN_STATION_BITS)
    fresh(client, speed=0.0, state=3)
    sim.read()
    assert sim.state is ExperienceState.READY
    assert sim.is_ready_to_start() is False
    assert any(cmd[:2] == ("manual", True) for cmd in client.commands)
    assert not any(cmd[0] == "dispatch" for cmd in client.commands)

    # Once station state confirms manual mode, READY is allowed.
    set_station(client, TRAIN_IN_STATION)
    fresh(client, speed=0.0, state=3)
    sim.read()
    assert sim.state is ExperienceState.READY
    assert not any(cmd[0] == "dispatch" for cmd in client.commands)


def test_manual_mode_is_confirmed_before_park_reset():
    client = FakeNL2Client()
    sim = Sim(client=client)
    connect_and_service(sim)

    # Moving/out of station and auto mode: request manual mode first without
    # treating the normal pre-activation wait as a reset operation.
    set_station(client, 0)
    fresh(client, speed=5.0, state=3)
    sim.read()
    assert sim.state is ExperienceState.READY
    assert any(cmd[:2] == ("manual", True) for cmd in client.commands)
    assert not any(cmd[0] == "reset" for cmd in client.commands)

    # Confirm manual mode while still out of station. Connection synchronization
    # must still not reset the park. Activation preparation is the operation
    # that requests RESET_PARK(False).
    set_station(client, MANUAL_MODE)
    fresh(client, speed=5.0, state=3)
    sim.read()
    assert sim.state is ExperienceState.READY
    assert not any(cmd[0] == "reset" for cmd in client.commands)
    sim.prepare_for_activation()
    manual_index = next(i for i, cmd in enumerate(client.commands) if cmd[:2] == ("manual", True))
    reset_index = client.commands.index(("reset", False))
    assert manual_index < reset_index
    assert not any(cmd[0] == "dispatch" for cmd in client.commands)


def test_dispatch_preparation_matches_old_station_logic():
    client = FakeNL2Client()
    sim = Sim(client=client)
    connect_and_service(sim)
    set_station(client, TRAIN_IN_STATION | CAN_DISPATCH)
    fresh(client, speed=0.0, state=3)
    sim.read()
    assert sim.state is ExperienceState.READY

    sim.run()
    assert sim.state is ExperienceState.READY

    # First fresh station state says the floor can lower.
    time.sleep(0.002)
    set_station(client, MANUAL_MODE | TRAIN_IN_STATION_BITS | PLATFORM_CAN_LOWER)
    sim.service()
    assert any(cmd[:2] == ("platform", True) for cmd in client.commands)
    assert not any(cmd[0] == "dispatch" for cmd in client.commands)

    # The old close_harness() was intentionally a no-op; wait for the station
    # state to change rather than inventing an unsupported command.
    time.sleep(0.002)
    set_station(client, MANUAL_MODE | TRAIN_IN_STATION_BITS | HARNESS_CAN_CLOSE)
    sim.service()
    assert not any(cmd[0] == "dispatch" for cmd in client.commands)

    # Once CAN_DISPATCH is reported, send the actual dispatch command.
    time.sleep(0.002)
    set_station(client, MANUAL_MODE | TRAIN_IN_STATION_BITS | CAN_DISPATCH)
    sim.service()
    assert any(cmd[0] == "dispatch" for cmd in client.commands)
    assert sim.state is ExperienceState.RUNNING


class CaptureSocket:
    def __init__(self):
        self.packets = []

    def sendall(self, data):
        self.packets.append(data)


def test_protocol_command_ids_payloads_and_telemetry_decode():
    protocol = NL2Protocol("127.0.0.1")
    protocol.is_connected = True
    protocol._sock = CaptureSocket()

    protocol.set_manual_mode(True)
    protocol.dispatch()
    protocol.set_platform(True)
    protocol.set_pause(True)
    protocol.set_pause(False)
    protocol.reset_park(False)
    protocol.recenter_vr()
    ids = [struct.unpack(">H", packet[1:3])[0] for packet in protocol._sock.packets]
    assert ids == [16, 17, 20, 27, 27, 28, 31]
    assert protocol._sock.packets[0][9:-1] == struct.pack(">ii?", 0, 0, True)
    assert protocol._sock.packets[2][9:-1] == struct.pack(">ii?", 0, 0, True)
    assert protocol._sock.packets[3][-2:-1] == struct.pack(">?", True)
    assert protocol._sock.packets[4][-2:-1] == struct.pack(">?", False)

    values = (
        3, 42, 0, 0, 0, 0, 0, 0,
        12.5, 1.0, 20.0, 3.0,
        0.0, 0.0, 0.0, 1.0,
        0.2, 1.0, 0.3,
    )
    raw = struct.pack(NL2Protocol.TELEMETRY_FORMAT, *values)
    protocol._process_message(NL2Protocol.N_MSG_TELEMETRY, 1, raw)
    assert protocol.latest_telemetry is not None
    assert protocol.latest_telemetry.speed == 12.5
    assert protocol.latest_telemetry.in_play_mode

    before = time.monotonic()
    protocol._process_message(NL2Protocol.N_MSG_STATION_STATE, 2, struct.pack(">I", TRAIN_IN_STATION))
    assert protocol.station_status == TRAIN_IN_STATION
    assert protocol.station_status_received_at >= before


def test_api_response_begins_initial_sync_after_tcp_connect():
    client = FakeNL2Client()
    sim = Sim(client=client)
    sim.connect()
    sim.service()
    assert sim.state is ExperienceState.RESETTING
    assert sim._ride_load_pending is True
    assert ("get_telemetry",) in client.commands
    assert ("get_current_station",) in client.commands


def test_protocol_discovers_current_coaster_and_station_and_uses_pair():
    protocol = NL2Protocol("127.0.0.1")
    protocol.is_connected = True
    protocol._sock = CaptureSocket()

    protocol.request_current_coaster_and_station()
    packet = protocol._sock.packets[-1]
    assert struct.unpack(">H", packet[1:3])[0] == protocol.N_MSG_GET_CURRENT_COASTER_AND_NEAREST_STATION

    protocol._process_message(protocol.N_MSG_INT_VALUE_PAIR, 1, struct.pack(">ii", 3, 2))
    assert protocol.current_coaster_index == 3
    assert protocol.current_station_index == 2

    protocol.request_station_state(3, 2)
    protocol.set_manual_mode(True, 3, 2)
    protocol.dispatch(3, 2)
    assert protocol._sock.packets[-3][9:-1] == struct.pack(">ii", 3, 2)
    assert protocol._sock.packets[-2][9:-1] == struct.pack(">ii?", 3, 2, True)
    assert protocol._sock.packets[-1][9:-1] == struct.pack(">ii", 3, 2)


def test_train_in_station_does_not_require_current_ride_train_bit():
    client = FakeNL2Client()
    sim = Sim(client=client)
    client.station_status = MANUAL_MODE | TRAIN_IN_STATION_BIT
    assert sim._train_in_station() is True


def test_adapter_station_commands_use_discovered_indices():
    client = FakeNL2Client()
    sim = Sim(client=client)
    client.current_coaster_index = 4
    client.current_station_index = 3
    sim._request_station_state()
    sim._set_manual_mode(True)
    sim._set_platform(True)
    sim._dispatch()
    assert ("get_station", 4, 3) in client.commands
    assert ("manual", True, 4, 3) in client.commands
    assert ("platform", True, 4, 3) in client.commands
    assert ("dispatch", 4, 3) in client.commands


def test_protocol_version_response_marks_api_ready():
    protocol = NL2Protocol("127.0.0.1")
    assert protocol.version is None
    protocol._process_message(protocol.N_MSG_VERSION, 1, bytes((2, 5, 3, 5)))
    assert protocol.version == "2.5.3.5"


def test_explicit_connection_states_localhost():
    client = FakeNL2Client()
    sim = Sim(address="127.0.0.1", client=client)

    status = sim.get_status()
    assert status.pc_connected is True
    assert status.sim_connected is False
    assert status.data_connected is False
    assert sim.state is ExperienceState.DISCONNECTED

    sim.connect()
    status = sim.get_status()
    assert status.pc_connected is True
    assert status.sim_connected is True
    assert status.data_connected is False
    assert sim.state is ExperienceState.DISCONNECTED

    sim.service()
    assert sim.state is ExperienceState.RESETTING
    client.complete_request(sim._ride_load_request_id, ok=True)

    set_station(client, TRAIN_IN_STATION)
    fresh(client, speed=0.0, state=3)
    sim.read()  # consume LOAD_PARK OK and discard cached state
    set_station(client, TRAIN_IN_STATION)
    fresh(client, speed=0.0, state=3)
    sim.read()
    status = sim.get_status()
    assert status.pc_connected is True
    assert status.sim_connected is True
    assert status.data_connected is True
    assert sim.state is ExperienceState.READY


def test_remote_pc_not_connected_until_heartbeat_exists():
    client = FakeNL2Client()
    sim = Sim(address="192.168.1.50", client=client)
    sim.connect()
    sim.service()
    status = sim.get_status()
    assert status.pc_connected is False
    assert status.sim_connected is True
    assert status.data_connected is False
    # PC connectivity is overridden by SHS in core; the adapter must not emit
    # stale legacy-heartbeat text that contradicts the green PC indicator.
    assert "heartbeat" not in status.text.lower()


def test_api_response_stale_maps_sim_to_disconnected():
    client = FakeNL2Client()
    sim = Sim(client=client)
    connect_and_service(sim)
    client.last_response_at = time.monotonic() - 10.0
    sample = sim.read()
    assert sample.state is ExperienceState.DISCONNECTED
    assert sample.telemetry_valid is False
    assert sim.get_status().sim_connected is False


def test_protocol_connect_returns_before_version_and_version_arrives_later():
    ready = threading.Event()
    got_version_request = threading.Event()
    release = threading.Event()
    server_info = {}

    def server():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            server_info["port"] = listener.getsockname()[1]
            ready.set()
            conn, _ = listener.accept()
            with conn:
                header = conn.recv(10)
                assert len(header) == 10
                assert header[0:1] == b"N"
                msg_id, request_id, size = struct.unpack(">HIH", header[1:9])
                assert msg_id == NL2Protocol.N_MSG_GET_VERSION
                assert size == 0
                assert header[9:10] == b"L"
                got_version_request.set()
                reply = struct.pack(
                    ">cHIH",
                    b"N",
                    NL2Protocol.N_MSG_VERSION,
                    request_id,
                    4,
                ) + bytes((2, 5, 3, 5)) + b"L"
                conn.sendall(reply)
                release.wait(1.0)

    thread = threading.Thread(target=server, daemon=True)
    thread.start()
    assert ready.wait(1.0)

    protocol = NL2Protocol(
        "127.0.0.1",
        port=server_info["port"],
        connect_timeout=0.5,
    )
    try:
        assert protocol.connect() is True
        assert protocol.is_connected is True
        assert protocol.version is None

        protocol.request_version()
        assert got_version_request.wait(0.5)
        deadline = time.monotonic() + 0.5
        while protocol.version is None and time.monotonic() < deadline:
            protocol.service()
            time.sleep(0.01)

        assert protocol.version == "2.5.3.5"
        assert protocol.response_is_fresh(1.0) is True
    finally:
        protocol.disconnect()
        release.set()
        thread.join(timeout=1.0)



def test_protocol_comms_debug_logs_tx_and_rx(caplog):
    protocol = NL2Protocol("127.0.0.1")
    protocol.is_connected = True
    protocol._sock = CaptureSocket()

    previous = nl2_protocol_module.NL2_COMMS_DEBUG
    nl2_protocol_module.NL2_COMMS_DEBUG = True
    try:
        with caplog.at_level(logging.DEBUG, logger="sims.nolimits2.nl2_protocol"):
            protocol.request_version()
            packet = protocol._sock.packets[-1]
            request_id = struct.unpack(">I", packet[3:7])[0]
            protocol._log_fields("RX", protocol.N_MSG_VERSION, request_id, bytes((2, 5, 3, 5)))
    finally:
        nl2_protocol_module.NL2_COMMS_DEBUG = previous

    text = caplog.text
    assert "NL2 TX" in text
    assert "GET_VERSION" in text
    assert "NL2 RX" in text
    assert "VERSION" in text
    assert "version=2.5.3.5" in text

def test_activation_preparation_resets_if_train_is_running_out_of_station():
    client = FakeNL2Client()
    sim = Sim(client=client)
    connect_and_service(sim)

    # Establish normal READY state first.
    set_station(client, TRAIN_IN_STATION)
    fresh(client, speed=0.0, state=3)
    sim.read()
    assert sim.state is ExperienceState.READY

    # NL2 is then operated independently of POC.  Generic POC state remains
    # READY, but native telemetry/station data show a running train.
    set_station(client, MANUAL_MODE)
    fresh(client, speed=7.0, state=3)
    sim.read()
    assert sim.state is ExperienceState.READY

    reset_count = sum(cmd[0] == "reset" for cmd in client.commands)
    sim.prepare_for_activation()

    assert sim.state is ExperienceState.RESETTING
    assert sum(cmd[0] == "reset" for cmd in client.commands) == reset_count + 1
    assert ("reset", False) in client.commands


def test_activation_preparation_does_not_reset_if_stopped_in_station():
    client = FakeNL2Client()
    sim = Sim(client=client)
    connect_and_service(sim)
    set_station(client, TRAIN_IN_STATION)
    fresh(client, speed=0.0, state=3)
    sim.read()
    assert sim.state is ExperienceState.READY

    reset_count = sum(cmd[0] == "reset" for cmd in client.commands)
    sim.prepare_for_activation()

    assert sim.state is ExperienceState.READY
    assert sum(cmd[0] == "reset" for cmd in client.commands) == reset_count



def test_nl2_experiences_include_configured_default():
    sim = Sim(address="127.0.0.1", client=FakeNL2Client())
    assert "Mdx Logo Coaster" in sim.get_experiences()



def test_select_experience_sends_load_park_and_enters_resetting():
    client = FakeNL2Client()
    sim = Sim(address="127.0.0.1", client=client)
    client.is_connected = True
    client.last_response_at = time.monotonic()
    sim._rides = (("Ride A", r"C:\Rides\A.nl2park"), ("Ride B", r"C:\Rides\B.nl2park"))
    sim._experiences = tuple(name for name, _ in sim._rides)
    sim._selected = 0
    sim.select_experience(1)
    assert sim.state is ExperienceState.RESETTING
    assert sim._ride_load_pending is True
    assert ("load_park", r"C:\Rides\B.nl2park", True) in client.commands
    request_id = sim._ride_load_request_id

    # Existing in-play telemetry must not advance the load before its response.
    set_station(client, TRAIN_IN_STATION)
    fresh(client, speed=0.0, state=3)
    sim.read()
    assert sim._ride_load_pending is True
    assert not any(cmd[0] == "reset" for cmd in client.commands)

    client.complete_request(request_id, ok=True)
    fresh(client, speed=0.0, state=3)
    sim.read()
    assert sim._ride_load_pending is False
    assert not any(cmd[0] == "reset" for cmd in client.commands)

    set_station(client, TRAIN_IN_STATION)
    fresh(client, speed=0.0, state=3)
    sim.read()
    assert sim.state is ExperienceState.READY



def test_estop_recovery_stays_resetting_until_fresh_dispatch_ready_state():
    client = FakeNL2Client()
    sim = Sim(client=client)
    connect_and_service(sim)

    # Establish a connected sim and start explicit E-STOP recovery.
    sim.state = ExperienceState.READY
    sim.recover_from_estop()
    assert sim.state is ExperienceState.RESETTING
    assert sim._estop_recovery_pending is True

    # Recovery LOAD_PARK is intentionally paused.  Its acknowledgement must
    # enter the same RESET_PARK(False) preparation path as normal activation,
    # rather than waiting for a train-in-station bit from the paused park.
    client.complete_request(sim._ride_load_request_id, ok=True)
    sim.read()
    assert sim.state is ExperienceState.RESETTING
    assert sim._park_reset_pending is True
    assert not any(cmd[0] == "reset" for cmd in client.commands)

    # Fresh play-mode telemetry plus manual-mode station state is sufficient
    # to send RESET_PARK(False); a train-in-station bit is not required before
    # starting the reset.
    set_station(client, MANUAL_MODE)
    fresh(client, speed=0.0, state=3)
    sim.read()
    assert ("reset", False) in client.commands
    assert sim.state is ExperienceState.RESETTING
    assert sim._reset_requested_at > 0.0

    # Completion still requires fresh post-reset station + telemetry proving
    # the same dispatch-ready condition used everywhere else.
    set_station(client, TRAIN_IN_STATION)
    fresh(client, speed=0.0, state=3)
    sim.read()
    assert sim.state is ExperienceState.READY
    assert sim.get_status().ready_to_start is True
    assert sim._estop_recovery_pending is False


def test_estop_recovery_clears_stale_manual_request_and_can_reissue_manual_mode():
    client = FakeNL2Client()
    sim = Sim(client=client)
    connect_and_service(sim)

    # Simulate a request flag left over from the pre-recovery park.
    sim._manual_mode_requested = True
    client.commands.clear()

    sim.recover_from_estop()
    assert sim._manual_mode_requested is False
    request_id = sim._ride_load_request_id
    client.complete_request(request_id, ok=True)

    # New park initially reports station without manual-mode bit.  Recovery
    # must send a NEW manual-mode command rather than being blocked by stale
    # bookkeeping from the old park.
    # First read consumes LOAD_PARK completion and deliberately clears cached
    # pre-load observations.
    sim.read()
    set_station(client, TRAIN_IN_STATION_BITS)
    fresh(client, speed=0.0, state=3)
    sim.read()
    assert any(cmd[:2] == ("manual", True) for cmd in client.commands)
    assert sim.state is ExperienceState.RESETTING

    # Once fresh station state confirms manual mode, recovery sends the same
    # unpaused park reset used by normal activation.
    set_station(client, TRAIN_IN_STATION)
    fresh(client, speed=0.0, state=3)
    sim.read()
    assert ("reset", False) in client.commands
    assert sim.state is ExperienceState.RESETTING

    # Fresh post-reset readiness completes recovery.
    set_station(client, TRAIN_IN_STATION)
    fresh(client, speed=0.0, state=3)
    sim.read()
    assert sim.state is ExperienceState.READY
    assert sim.is_ready_to_start() is True
    assert sim._estop_recovery_pending is False


def test_estop_recovery_completes_with_documented_train_in_station_bit_only():
    client = FakeNL2Client()
    sim = Sim(client=client)
    connect_and_service(sim)

    # Begin a genuine E-stop recovery park reload.
    sim.recover_from_estop()
    request_id = sim._ride_load_request_id
    client.complete_request(request_id, ok=True)

    # Consume LOAD_PARK acknowledgement; the adapter must remain RESETTING
    # until fresh post-load station/telemetry proves dispatch readiness.
    sim.read()
    assert sim.state is ExperienceState.RESETTING

    # Recovery first enters the normal unpaused reset path.  NL2 documentation
    # defines bit 11 alone as "a train is inside station"; bit 12 is not needed.
    set_station(client, MANUAL_MODE | TRAIN_IN_STATION_BIT)
    fresh(client, speed=0.0, state=3)
    sim.read()
    assert ("reset", False) in client.commands
    assert sim.state is ExperienceState.RESETTING

    set_station(client, MANUAL_MODE | TRAIN_IN_STATION_BIT)
    fresh(client, speed=0.0, state=3)
    sim.read()
    assert sim.state is ExperienceState.READY
    assert sim.is_ready_to_start() is True
    assert sim._estop_recovery_pending is False
