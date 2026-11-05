# test_nl2_flying_beast_diagnostics_v186.py ver 2
"""Preserved v186 operator and command-ACK diagnostics, streamlined in v187."""

import time

import pytest

from core.types import ExperienceState
from sims.nolimits2 import ride_config
from sims.nolimits2.sim import Sim
from tests.test_nl2_flyer_lock_v185 import (
    setup, prepare, lock_command, CAN_LOCK_FLYER, CAN_UNLOCK_FLYER,
    VIEW_NOT_READY, OTHER_NOT_READY,
)
from tests.test_nl2_multistation_v171 import VIEW, OTHER, MANUAL, CAN_DISPATCH
from tests.test_nolimits2_adapter import telemetry


def events_of(sim, name):
    return [e for e in sim.consume_state_events() if e['event'] == name]


def dispatched():
    sim, c = setup(flags=VIEW | CAN_DISPATCH)
    prepare(sim, c, flags=VIEW | CAN_DISPATCH)
    assert sim.state is ExperienceState.RUNNING
    return sim, c


def test_operator_start_before_lock_records_lock_eligibility():
    sim, c = setup()
    ev = events_of(sim, 'flyer_operator_start')
    assert len(ev) == 1
    assert ev[0]['state'] == 'READY'
    assert ev[0]['station'] == (0, 0)
    assert ev[0]['can_lock_flyer'] and not ev[0]['can_dispatch']
    assert ev[0]['view_train_here'] and ev[0]['telemetry_speed'] == 0
    assert not lock_command(c)  # no auto-lock merely on reaching READY
    prepare(sim, c)
    assert len(lock_command(c)) == 1


def test_operator_start_after_manual_lock_will_not_test_autolock():
    sim, c = setup(flags=VIEW | CAN_DISPATCH | CAN_UNLOCK_FLYER)
    ev = events_of(sim, 'flyer_operator_start')
    assert ev and ev[0]['can_dispatch'] and not ev[0]['can_lock_flyer']
    prepare(sim, c, flags=VIEW | CAN_DISPATCH | CAN_UNLOCK_FLYER)
    assert not lock_command(c)
    assert ('dispatch', 0, 0) in c.commands


def test_start_ignored_is_explicit_and_does_not_send_control():
    sim, c = dispatched()
    before = len(c.commands)
    sim.consume_state_events()
    sim.run()
    ev = sim.consume_state_events()
    assert len([x for x in ev if x['event'] == 'flyer_operator_start_ignored']) == 1
    assert len([x for x in ev if x['event'] == 'flyer_operator_start']) == 1
    assert len(c.commands) == before


def test_lock_gate_wait_reasons_are_change_triggered_and_recover():
    sim, c = setup()
    c.latest_telemetry = telemetry(received_at=time.monotonic() - 5)
    c.put(0, VIEW_NOT_READY | CAN_LOCK_FLYER)
    sim._service_dispatch_preparation()
    ev = events_of(sim, 'flyer_lock_gate_waiting')
    assert len(ev) == 1
    assert not ev[0]['telemetry_fresh']
    assert ev[0]['view_train_here']
    assert not lock_command(c)
    c.put(0, VIEW_NOT_READY | CAN_LOCK_FLYER)
    sim._service_dispatch_preparation()
    assert not events_of(sim, 'flyer_lock_gate_waiting')
    c.latest_telemetry = telemetry(speed=0, frame=33)
    prepare(sim, c)
    assert len(lock_command(c)) == 1


def test_dispatch_diagnostics_no_longer_poll_other_station():
    sim, c = dispatched()
    sent_count = sum(x[0] == 'dispatch' for x in c.commands)
    sim.consume_state_events()
    c.put(1, OTHER_NOT_READY | CAN_LOCK_FLYER)
    for _ in range(3):
        sim._service_flyer_dispatch_confirmation()
    assert not any(x == ('get_station', 0, 1) for x in c.commands)
    assert sum(x[0] == 'dispatch' for x in c.commands) == sent_count
    assert not any(x[0] == 'flyer' for x in c.commands)

def test_dispatch_status_distinguishes_ack_from_motion():
    sim, c = dispatched()
    sim._data_connected = True
    assert 'awaiting acknowledgement' in sim.get_status().text
    c.complete_request(sim._flyer_dispatch_request_id)
    sim._service_flyer_dispatch_confirmation()
    assert 'acknowledged dispatch' in sim.get_status().text
    assert not sim._view_train_departed
    tm = telemetry(speed=9, frame=21)
    c.latest_telemetry = tm
    c.put(0, MANUAL)
    sim._update_state(tm)
    motion_events = events_of(sim, 'flyer_dispatch_motion_detected')
    assert len(motion_events) == 1
    assert 'speed' in motion_events[0]
    assert sim._view_train_departed
    # Status reflects actual state rather than treating the API OK as motion.


def test_deactivation_stops_dispatch_ack_tracking():
    sim, c = dispatched()
    sim._service_flyer_dispatch_confirmation()
    sim.cancel_pending_dispatch()
    after_cancel = len(c.commands)
    sim._service_flyer_dispatch_confirmation()
    assert len(c.commands) == after_cancel
    assert not events_of(sim, 'dispatch_command_acknowledged')


@pytest.mark.parametrize('park', sorted(set(ride_config.PARKS) - {'flyingbeast'}))
def test_all_other_parks_avoid_new_diagnostics_and_station_queries(park):
    sim, c = setup(park=park, flags=VIEW | CAN_DISPATCH)
    prepare(sim, c, flags=VIEW | CAN_DISPATCH)
    before_other = c.commands.count(('get_station', 0, 1))
    sim._service_flyer_dispatch_confirmation()
    events = sim.consume_state_events()
    assert not any(e['event'].startswith('flyer_') for e in events)
    assert c.commands.count(('get_station', 0, 1)) == before_other
    assert ('dispatch', 0, 0) in c.commands
