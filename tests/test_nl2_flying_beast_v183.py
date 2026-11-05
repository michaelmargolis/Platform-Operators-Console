# test_nl2_flying_beast_v183.py  ver 1
"""Regression tests for the v183 Flying Beast park-load and seat diagnostics.

No hardware, NL2 process or physical dispatch commands are used.
"""
from __future__ import annotations

import logging
import time

from core.types import ExperienceState
from sims.nolimits2.sim import Sim
from tests.test_nolimits2_adapter import (
    FakeNL2Client, connect_and_service, fresh, set_station, telemetry,
)

MANUAL = 0x0002
CAN_DISPATCH = 0x0004
CAN_LOCK_FLYER = 0x0200
CAN_UNLOCK_FLYER = 0x0400
VIEW_TRAIN = 0x0800 | 0x1000


def test_pending_flying_beast_load_blocks_old_normalized_height_telemetry():
    client = FakeNL2Client()
    sim = Sim(client=client)
    connect_and_service(sim)
    assert sim.heave_mode == 'normalized_height'

    index = sim.get_experiences().index('Flying Beast')
    sim.select_experience(index)
    request_id = sim._ride_load_request_id
    assert sim._ride_load_pending and sim.state == ExperienceState.RESETTING
    assert sim.heave_mode == 'normalized_height'  # old park until ACK

    # Regression: previously this fresh frame ran _normalized_height_z() with
    # _selected=Flying Beast but normalized-height=old park -> ValueError.
    fresh(client, frame=20, pos_y=10.0)
    sample = sim.read()
    assert not sample.telemetry_valid
    assert sim.state == ExperienceState.RESETTING
    assert sim._ride_load_pending
    assert sim.heave_mode == 'normalized_height'

    client.complete_request(request_id, ok=True)
    # The acknowledged park must discard the pre-load frame and adopt the
    # variant's heave mode before any new telemetry can be interpreted.
    sample = sim.read()
    assert not sample.telemetry_valid
    assert not sim._ride_load_pending
    assert sim.heave_mode == 'legacy_height'
    assert client.latest_telemetry is None

    set_station(client, MANUAL | VIEW_TRAIN)
    fresh(client, frame=21, pos_y=10.0)
    sample = sim.read()
    assert sample.telemetry_valid
    assert sim.heave_mode == 'legacy_height'


def test_failed_park_load_does_not_process_telemetry_under_selected_ride():
    client = FakeNL2Client()
    sim = Sim(client=client)
    connect_and_service(sim)
    sim.select_experience(sim.get_experiences().index('Flying Beast'))
    client.complete_request(sim._ride_load_request_id, ok=False, detail='load rejected')
    fresh(client, frame=25)
    sample = sim.read()
    assert sim._park_load_failed
    assert not sim._ride_load_pending
    assert not sample.telemetry_valid
    assert not sim.is_ready_to_start()

    # Further telemetry cannot be interpreted until a successful park load.
    fresh(client, frame=26)
    assert not sim.read().telemetry_valid


def test_station_diagnostics_decode_flyer_flags_only_on_changes(caplog):
    client = FakeNL2Client()
    client.is_connected = True
    client.last_response_at = time.monotonic()
    sim = Sim(client=client)
    client.current_coaster_index = 0
    client.current_station_index = 1
    client.latest_telemetry = telemetry(frame=1)
    sim._ride_station = (0, 0)  # departure station pinned, not nearest

    # Simulate the protocol's request-ID-correlated station-state cache.
    cache = {(0, 0): (MANUAL | VIEW_TRAIN | CAN_LOCK_FLYER, time.monotonic())}
    client.station_snapshot = lambda identity: cache.get(identity)
    with caplog.at_level(logging.DEBUG, logger='sims.nolimits2.sim'):
        sim._log_station_diagnostics(sim._station_observation())
        sim._log_station_diagnostics(sim._station_observation())
        cache[(0, 0)] = (MANUAL | VIEW_TRAIN | CAN_UNLOCK_FLYER, time.monotonic())
        sim._log_station_diagnostics(sim._station_observation())
        cache[(0, 0)] = (MANUAL | VIEW_TRAIN | CAN_UNLOCK_FLYER | CAN_DISPATCH,
                         time.monotonic())
        sim._log_station_diagnostics(sim._station_observation())
        sim._log_station_diagnostics(sim._station_observation())

    messages = [x.message for x in caplog.records if 'NL2 station flags:' in x.message]
    assert len(messages) == 3
    assert all('station=(0, 0) nearest=(0, 1)' in msg for msg in messages)
    assert 'can_lock_flyer=True' in messages[0]
    assert 'can_unlock_flyer=False' in messages[0]
    assert 'can_lock_flyer=False' in messages[1]
    assert 'can_unlock_flyer=True' in messages[1]
    assert 'can_dispatch=True' in messages[2]
    assert all('view_train_here=True' in msg for msg in messages)
    assert all('coaster_matches=True' in msg for msg in messages)


def test_station_diagnostics_never_attribute_other_station_flags_to_view_station(caplog):
    client = FakeNL2Client()
    client.is_connected = True
    client.last_response_at = time.monotonic()
    sim = Sim(client=client)
    sim._ride_station = (0, 0)
    client.current_coaster_index = 0
    client.current_station_index = 1
    client.latest_telemetry = telemetry(frame=1)
    # Deliberately put a flyer-lock flag on station 1 ONLY.
    observed_at = time.monotonic() - 0.001
    client.station_snapshot = lambda identity: (
        (MANUAL, observed_at) if identity == (0, 0)
        else (MANUAL | CAN_LOCK_FLYER, observed_at)
    )
    with caplog.at_level(logging.DEBUG, logger='sims.nolimits2.sim'):
        sim._log_station_diagnostics(sim._station_observation())
    messages = [r.message for r in caplog.records if 'NL2 station flags:' in r.message]
    assert len(messages) == 1
    assert 'station=(0, 0) nearest=(0, 1)' in messages[0]
    assert 'can_lock_flyer=False' in messages[0]
    assert 'view_train_here=False' in messages[0]


def test_dispatch_wait_logs_lock_to_unlock_transition_but_sends_no_seat_command(caplog):
    client = FakeNL2Client()
    sim = Sim(client=client)
    connect_and_service(sim)
    sim.state = ExperienceState.READY
    fresh(client, speed=0, frame=10)
    set_station(client, MANUAL | VIEW_TRAIN)
    sim.run()

    with caplog.at_level(logging.DEBUG, logger='sims.nolimits2.sim'):
        set_station(client, MANUAL | VIEW_TRAIN | CAN_LOCK_FLYER)
        sim._service_dispatch_preparation()
        set_station(client, MANUAL | VIEW_TRAIN | CAN_UNLOCK_FLYER)
        sim._service_dispatch_preparation()
        set_station(client, MANUAL | VIEW_TRAIN | CAN_UNLOCK_FLYER | CAN_DISPATCH)
        sim._service_dispatch_preparation()

    messages = [r.message for r in caplog.records if 'NL2 station flags:' in r.message]
    assert len(messages) == 3
    assert 'can_lock_flyer=True' in messages[0]
    assert 'can_unlock_flyer=True' in messages[1]
    assert 'can_dispatch=True' in messages[2]
    assert not any('station not yet dispatchable' in r.message for r in caplog.records)
    assert not any(cmd[0] == 'flyer' for cmd in client.commands)
    assert any(cmd[0] == 'dispatch' for cmd in client.commands)
