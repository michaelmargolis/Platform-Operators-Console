#test_xplane_adapter.py  ver 7
import json

import pytest

from core.types import ExperienceState
from sims.xplane import config
from sims.xplane.sim import Sim


class FakeClock:
    def __init__(self):
        self.now = 10.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeTransport:
    def __init__(self):
        self.is_open = False
        self.commands = []
        self.telemetry = []
        self.heartbeats = []
        self.heartbeat_pings = 0
        self.last_error = ""

    def open(self):
        self.is_open = True

    def close(self):
        self.is_open = False

    def send_command(self, message):
        self.commands.append(message)

    def send_heartbeat_ping(self):
        self.heartbeat_pings += 1

    def drain_telemetry(self):
        msgs = list(self.telemetry)
        self.telemetry.clear()
        return msgs

    def drain_heartbeat(self):
        msgs = list(self.heartbeats)
        self.heartbeats.clear()
        return msgs


def telemetry(paused=0, **overrides):
    payload = {
        "header": "xplane_telemetry",
        "g_axil": 0.10,
        "g_side": 0.20,
        "g_nrml": 0.30,
        "phi": 0.40,
        "theta": 0.50,
        "Rrad": 0.60,
        "speed": 20.0,
        "paused": paused,
        "icao": "C172",
    }
    payload.update(overrides)
    return json.dumps(payload)


def complete_initial_load(sim, transport):
    """Deliver enough telemetry to load and confirm the default flight."""
    transport.telemetry.append(telemetry(paused=0))
    sample = sim.read()
    assert sample.state is ExperienceState.RESETTING
    assert transport.commands[-2:] == ["Pause", "FlightMode,0"]

    transport.telemetry.append(telemetry(paused=1))
    sample = sim.read()
    assert sample.state is ExperienceState.READY
    return sample


def test_xplane_initial_connection_loads_default_predefined_flight():
    clock = FakeClock()
    transport = FakeTransport()
    sim = Sim(address="127.0.0.1", transport=transport, time_func=clock)

    sim.connect()
    assert transport.commands == ["InitComs"]
    assert sim.get_experiences() == (
        "calm straight and level",
        "beginner takeoff",
        "easy approach",
    )

    sample = complete_initial_load(sim, transport)
    assert sample.telemetry_valid is True
    assert sample.speed == 20.0
    assert sample.transform == pytest.approx((0.10, 0.24, 0.15, -0.80, 1.00, 1.20))
    assert sim.get_status().entity_name == "calm straight and level"


def test_xplane_start_pause_unpause_are_confirmed_by_paused_dataref():
    clock = FakeClock()
    transport = FakeTransport()
    sim = Sim(address="127.0.0.1", transport=transport, time_func=clock)
    sim.connect()
    complete_initial_load(sim, transport)

    sim.run()
    assert transport.commands[-1] == "Run"
    assert sim.state is ExperienceState.READY

    transport.telemetry.append(telemetry(paused=0))
    assert sim.read().state is ExperienceState.RUNNING

    sim.pause()
    assert transport.commands[-1] == "Pause"
    assert sim.state is ExperienceState.RUNNING

    transport.telemetry.append(telemetry(paused=1))
    assert sim.read().state is ExperienceState.PAUSED

    sim.pause()
    assert transport.commands[-1] == "Run"
    assert sim.state is ExperienceState.PAUSED

    transport.telemetry.append(telemetry(paused=0))
    assert sim.read().state is ExperienceState.RUNNING


def test_xplane_activation_does_not_reload_flight_that_was_never_started():
    clock = FakeClock()
    transport = FakeTransport()
    sim = Sim(address="127.0.0.1", transport=transport, time_func=clock)
    sim.connect()
    complete_initial_load(sim, transport)

    transport.commands.clear()
    sim.prepare_for_activation()
    assert sim.state is ExperienceState.READY
    assert transport.commands == []

    sim.reset_vr()
    assert transport.commands == []


def test_xplane_activation_after_started_flight_reloads_selected_experience():
    clock = FakeClock()
    transport = FakeTransport()
    sim = Sim(address="127.0.0.1", transport=transport, time_func=clock)
    sim.connect()
    complete_initial_load(sim, transport)

    sim.run()
    transport.telemetry.append(telemetry(paused=0))
    assert sim.read().state is ExperienceState.RUNNING

    # POC deactivation requests Pause while its own E-STOP latch stays in the
    # core. Native X-Plane subsequently reports PAUSED, but the adapter retains
    # knowledge that this experience had actually been started.
    sim.pause()
    transport.telemetry.append(telemetry(paused=1))
    assert sim.read().state is ExperienceState.PAUSED

    transport.commands.clear()
    sim.prepare_for_activation()
    assert sim.state is ExperienceState.RESETTING
    assert transport.commands == ["Pause", "FlightMode,0"]

    transport.telemetry.append(telemetry(paused=1))
    assert sim.read().state is ExperienceState.READY


def test_xplane_connection_flags_and_telemetry_timeout():
    clock = FakeClock()
    transport = FakeTransport()
    sim = Sim(address="127.0.0.1", transport=transport, time_func=clock)
    sim.connect()

    status = sim.get_status()
    assert status.pc_connected is True
    assert status.sim_connected is False
    assert status.data_connected is False

    transport.telemetry.append(telemetry(paused=0))
    sim.read()
    status = sim.get_status()
    assert status.pc_connected is True
    assert status.sim_connected is True
    assert status.data_connected is True

    clock.advance(config.TELEMETRY_TIMEOUT_S + 0.1)
    sim.service()
    status = sim.get_status()
    assert status.pc_connected is True
    assert status.data_connected is False
    # While loading, the adapter preserves the known simulator connection
    # across the expected telemetry gap.
    assert status.sim_connected is True
    assert sim.state is ExperienceState.RESETTING


def test_xplane_remote_heartbeat_distinguishes_pc_and_sim_from_data():
    clock = FakeClock()
    transport = FakeTransport()
    sim = Sim(address="192.0.2.10", transport=transport, time_func=clock)
    sim.connect()

    transport.heartbeats.append("heartbeat xplane_running")
    sim.service()
    status = sim.get_status()
    assert status.pc_connected is True
    assert status.sim_connected is True
    assert status.data_connected is False


def test_missing_pause_field_is_live_data_but_cannot_complete_initial_load():
    clock = FakeClock()
    transport = FakeTransport()
    sim = Sim(address="127.0.0.1", transport=transport, time_func=clock)
    sim.connect()

    payload = json.loads(telemetry(paused=0))
    payload.pop("paused")
    transport.telemetry.append(json.dumps(payload))
    sample = sim.read()

    assert sample.telemetry_valid is True
    assert sample.state is ExperienceState.RESETTING
    status = sim.get_status()
    assert status.sim_connected is True
    assert status.data_connected is True
    assert sim._paused_available is False


def test_service_sends_plugin_ping_while_receiving_data():
    clock = FakeClock()
    transport = FakeTransport()
    sim = Sim(transport=transport, time_func=clock)
    sim.connect()
    transport.telemetry.append(telemetry(paused=0))
    sim.read()
    clock.advance(0.5)
    sim.service()
    assert "ping" in transport.commands


def test_xplane_experience_selection_loads_selected_sop_flight_immediately():
    clock = FakeClock()
    transport = FakeTransport()
    sim = Sim(address="127.0.0.1", transport=transport, time_func=clock)
    sim.connect()
    complete_initial_load(sim, transport)

    transport.commands.clear()
    sim.select_experience(1)
    assert sim.state is ExperienceState.RESETTING
    assert transport.commands == ["Pause", "FlightMode,1"]
    assert sim.get_status().busy is True
    assert sim.get_status().entity_name == "beginner takeoff"

    transport.telemetry.append(telemetry(paused=0))
    assert sim.read().state is ExperienceState.RESETTING

    transport.telemetry.append(telemetry(paused=1))
    sample = sim.read()
    assert sample.state is ExperienceState.READY
    assert sim.get_status().busy is False


def test_xplane_load_preserves_sim_connection_across_expected_telemetry_gap():
    clock = FakeClock()
    transport = FakeTransport()
    sim = Sim(address="127.0.0.1", transport=transport, time_func=clock)
    sim.connect()
    complete_initial_load(sim, transport)

    sim.select_experience(1)
    clock.advance(config.TELEMETRY_TIMEOUT_S + 0.1)
    sim.service()
    status = sim.get_status()
    assert sim.state is ExperienceState.RESETTING
    assert status.pc_connected is True
    assert status.sim_connected is True
    assert status.data_connected is False
