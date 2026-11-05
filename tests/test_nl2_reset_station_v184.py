# test_nl2_reset_station_v184.py ver 1
"""Opt-in Flying Beast reset discovery: cannot steal other rides' station.

Fake NL2 replies only. No simulator, actuator output, or real dispatch.
"""
from __future__ import annotations

import time

import pytest

from core.types import ExperienceState
from sims.nolimits2 import ride_config
from sims.nolimits2.sim import Sim
from tests.test_nl2_multistation_v171 import TwoStationClient, VIEW, OTHER, MANUAL
from tests.test_nolimits2_adapter import telemetry


def ready_to_probe(park='flyingbeast', *, speed=0.0):
    client = TwoStationClient()
    client.current_coaster_index = 0
    client.current_station_index = 1  # NL2 chose the OTHER train's station
    sim = Sim(client=client)
    sim._selected = sim._park_keys.index(park)
    sim.state = ExperienceState.RESETTING
    sim._reset_requested_at = time.monotonic() - 0.2
    client.latest_telemetry = telemetry(speed=speed, frame=14)
    return sim, client


def scan(sim, client, flags0=VIEW, flags1=OTHER, *, speed=0.0, frame=14):
    client.put(0, flags0)
    client.put(1, flags1)
    client.latest_telemetry = telemetry(speed=speed, frame=frame)
    sim._update_state(client.latest_telemetry)


def test_flying_beast_resets_at_view_station_not_nearest_other_train():
    sim, client = ready_to_probe()
    assert sim._reset_station_candidates() == (0, 1)
    assert sim._needs_reset_station_probe()
    scan(sim, client, flags1=OTHER | 0x0200)
    assert sim.state is ExperienceState.RESETTING
    assert sim._ready_station is None
    assert not sim.is_ready_to_start()
    # No station-control request may target the wrong station while probing.
    assert not any(cmd[0] in ('manual', 'dispatch', 'platform') for cmd in client.commands)
    scan(sim, client, flags1=OTHER | 0x0200, frame=15)
    assert sim.state is ExperienceState.READY
    assert sim._ready_station == (0, 0)
    assert sim.is_ready_to_start()
    assert not sim._needs_reset_station_probe()
    assert any(e['event'] == 'reset_view_station_confirmed' and e['station'] == (0, 0)
               for e in sim.consume_state_events())
    sim.run()
    client.put(0, VIEW)
    sim._service_dispatch_preparation()
    assert ('dispatch', 0, 0) in client.commands
    assert ('dispatch', 0, 1) not in client.commands
    assert sim._ride_station == (0, 0)


def test_no_second_confirmation_without_new_replies_from_both_stations():
    sim, client = ready_to_probe()
    scan(sim, client)
    for _ in range(5):
        sim._update_state(client.latest_telemetry)
    assert sim._ready_station is None
    assert sim.state is ExperienceState.RESETTING
    client.put(0, VIEW)  # only one station updated: not a full second poll
    sim._update_state(client.latest_telemetry)
    assert sim._ready_station is None
    client.put(1, OTHER)
    sim._update_state(client.latest_telemetry)
    assert sim.state is ExperienceState.READY


def test_flyer_station_probed_without_locking_or_disabling_its_controls():
    sim, client = ready_to_probe()
    sim._request_reset_station_probes(time.monotonic())
    assert ('get_station', 0, 0) in client.commands
    assert ('get_station', 0, 1) in client.commands
    assert not any(cmd[0] in ('manual', 'dispatch', 'platform') for cmd in client.commands)
    scan(sim, client, flags0=VIEW & ~MANUAL, flags1=OTHER)
    scan(sim, client, flags0=VIEW & ~MANUAL, flags1=OTHER, frame=15)
    assert sim._ready_station == (0, 0)
    assert sim.state is ExperienceState.RESETTING
    assert ('manual', True, 0, 0) in client.commands
    assert ('manual', True, 0, 1) not in client.commands
    client.put(0, VIEW)
    client.latest_telemetry = telemetry(speed=0.0, frame=16)
    sim._update_state(client.latest_telemetry)
    assert sim.state is ExperienceState.READY


@pytest.mark.parametrize('flags0,flags1', [
    (OTHER, OTHER),
    (VIEW, VIEW),  # ambiguous: no deterministic station
    (MANUAL, OTHER),
])
def test_no_train_or_multiple_view_train_flags_cannot_authorize(flags0, flags1):
    sim, client = ready_to_probe()
    scan(sim, client, flags0, flags1)
    scan(sim, client, flags0, flags1)
    assert sim.state is ExperienceState.RESETTING
    assert sim._ready_station is None
    assert not sim.is_ready_to_start()
    assert not any(cmd[0] in ('manual', 'dispatch', 'platform') for cmd in client.commands)


def test_stale_or_wrong_coaster_or_moving_view_train_cannot_authorize():
    sim, client = ready_to_probe()
    client.put(0, VIEW, age=3.0)
    client.put(1, OTHER)
    sim._update_state(client.latest_telemetry)
    assert sim._ready_station is None
    client.put(0, VIEW)
    client.latest_telemetry = telemetry(speed=5, frame=20)
    scan(sim, client, speed=5)
    scan(sim, client, speed=5)
    assert sim._ready_station is None
    client.latest_telemetry = telemetry(speed=0.0, frame=21)
    client.latest_telemetry = type(client.latest_telemetry)(**{
        **client.latest_telemetry.__dict__, 'coaster_index': 1,
    })
    sim._update_state(client.latest_telemetry)
    assert sim._ready_station is None
    assert sim.state is ExperienceState.RESETTING


def test_candidate_change_requires_fresh_two_round_confirmation():
    sim, client = ready_to_probe()
    scan(sim, client, VIEW, OTHER)
    assert sim._reset_probe_candidate == (0, 0)
    scan(sim, client, OTHER, VIEW)
    assert sim._reset_probe_candidate == (0, 1)
    assert sim._reset_probe_confirmations == 1
    for _ in range(3):
        sim._update_state(client.latest_telemetry)
    assert sim._ready_station is None
    scan(sim, client, OTHER, VIEW)
    assert sim._ready_station == (0, 1)


@pytest.mark.parametrize('park', sorted(set(ride_config.PARKS) - {'flyingbeast'}))
def test_every_other_supported_park_keeps_v183_station_selection(park):
    sim, client = ready_to_probe(park=park)
    assert sim._reset_station_candidates() == ()
    assert not sim._needs_reset_station_probe()
    # Existing v183 behavior: nearest station is used, no alternate scanning.
    client.put(0, OTHER)
    client.put(1, VIEW)
    client.latest_telemetry = telemetry(frame=14)
    sim._request_station_state()
    assert client.commands == [('get_station', 0, 1)]
    sim._update_state(client.latest_telemetry)
    assert sim.state is ExperienceState.READY
    assert sim._ready_station is None  # no new station was pinned
    assert not any(e['event'] == 'reset_view_station_confirmed'
                   for e in sim.consume_state_events())


def test_flying_beast_ready_without_reset_uses_unmodified_path():
    sim, client = ready_to_probe()
    sim._reset_requested_at = 0.0  # manual mode preparation, no park reset
    client.put(1, VIEW)
    sim._request_station_state()
    assert client.commands == [('get_station', 0, 1)]
    sim._update_state(client.latest_telemetry)
    assert sim.state is ExperienceState.READY
    assert not sim._needs_reset_station_probe()


def test_search_is_suppressed_during_run_and_after_variant_load_failure():
    sim, client = ready_to_probe()
    sim.state = ExperienceState.RUNNING
    assert not sim._needs_reset_station_probe()
    sim.state = ExperienceState.RESETTING
    sim._park_load_failed = True
    assert not sim._needs_reset_station_probe()
    sim._park_load_failed = False
    sim._ride_load_pending = True
    assert not sim._needs_reset_station_probe()


def test_service_then_read_polling_selects_station_without_extra_manual_or_dispatch():
    """Exercise production service() -> read() rather than helper alone."""
    sim, client = ready_to_probe()
    sim._apply_loaded_variant(sim._selected)  # LOAD_PARK acknowledgement applied config
    sim._connected_sync_started = True  # startup load already acknowledged
    sim._last_api_probe = time.monotonic()
    sim._last_station_request = 0.0
    sim.service()
    assert ('get_station', 0, 0) in client.commands
    assert ('get_station', 0, 1) in client.commands
    assert not any(c[0] in ('manual', 'dispatch') for c in client.commands)
    client.put(0, VIEW)
    client.put(1, OTHER | 0x0200)
    client.latest_telemetry = telemetry(speed=0.0, frame=200)
    sim.read()
    assert sim.state is ExperienceState.RESETTING
    sim._last_station_request = 0.0  # advance station poll cycle
    sim.service()
    client.put(0, VIEW)
    client.put(1, OTHER | 0x0200)
    client.latest_telemetry = telemetry(speed=0.0, frame=201)
    sim.read()
    assert sim.state is ExperienceState.READY
    assert sim._ready_station == (0, 0)
    assert not any(c[0] in ('manual', 'dispatch') for c in client.commands)
