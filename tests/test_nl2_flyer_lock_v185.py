# test_nl2_flyer_lock_v185.py ver 2
"""Flying Beast seat lock + reply/departure tracing. Fake-only, no real NL2 or hardware."""
from __future__ import annotations

import struct
import time

import pytest

from core.types import ExperienceState
from sims.nolimits2 import ride_config
from sims.nolimits2.nl2_protocol import NL2Protocol
from sims.nolimits2.sim import Sim
from tests.test_nl2_multistation_v171 import TwoStationClient, VIEW, OTHER, MANUAL, CAN_DISPATCH
from tests.test_nolimits2_adapter import telemetry

CAN_LOCK_FLYER = 0x0200
CAN_UNLOCK_FLYER = 0x0400
VIEW_NOT_READY = VIEW & ~CAN_DISPATCH
OTHER_NOT_READY = OTHER & ~CAN_DISPATCH


class FlyerClient(TwoStationClient):
    def set_flyer_car(self, locked: bool, coaster: int = 0, station: int = 0):
        request_id = self._next_request_id
        self._next_request_id += 1
        self.commands.append(('flyer', bool(locked), int(coaster), int(station), request_id))
        return request_id

    def dispatch(self, coaster=0, station=0):
        request_id = self._next_request_id
        self._next_request_id += 1
        self.commands.append(('dispatch', int(coaster), int(station)))
        return request_id


class SocketSpy:
    def __init__(self):
        self.packets = []
    def sendall(self, packet):
        self.packets.append(packet)


def setup(park='flyingbeast', station=0, *, flags=None, nearest=1):
    client = FlyerClient()
    client.current_coaster_index = 0
    client.current_station_index = nearest
    client.latest_telemetry = telemetry(speed=0, frame=11)
    sim = Sim(client=client)
    sim._selected = sim._park_keys.index(park)
    sim.state = ExperienceState.READY
    sim._ready_station = (0, station)
    client.put(station, VIEW_NOT_READY | CAN_LOCK_FLYER if flags is None else flags)
    sim.run()
    assert sim._dispatch_pending
    return sim, client


def prepare(sim, client, station=0, flags=None):
    client.latest_telemetry = telemetry(speed=0, frame=12)
    client.put(station, VIEW_NOT_READY | CAN_LOCK_FLYER if flags is None else flags)
    sim._service_dispatch_preparation()


def lock_command(client):
    return [x for x in client.commands if x[0] == 'flyer']


def test_official_message_21_wire_format_request_id_and_ack():
    client = NL2Protocol('127.0.0.1')
    client.is_connected = True
    spy = SocketSpy()
    client._sock = spy
    request_id = client.set_flyer_car(True, coaster=2, station=1)
    assert spy.packets == [struct.pack('>cHIHiiBc', b'N', 21, request_id, 9,
                                      2, 1, 0, b'L')]
    assert client._payload_summary(client.N_MSG_SET_FLYER_CAR, spy.packets[0][9:-1]) == (
        'coaster=2 station=1 raw_state=0 action=LOCK')
    assert client.request_result(request_id) is None
    client._process_message(client.N_MSG_OK, request_id, b'')
    assert client.request_result(request_id) == ('ok', '')
    client.set_flyer_car(False, 2, 1)
    assert spy.packets[-1][-2:-1] == b'\x01'
    assert client._payload_summary(client.N_MSG_SET_FLYER_CAR, spy.packets[-1][9:-1]) == (
        'coaster=2 station=1 raw_state=1 action=UNLOCK')


def test_dispatch_wire_packet_unchanged_and_has_correlated_reply():
    c = NL2Protocol('127.0.0.1')
    c.is_connected = True
    spy = SocketSpy()
    c._sock = spy
    req = c.dispatch(3, 4)
    assert spy.packets == [struct.pack('>cHIHii c', b'N', 17, req, 8, 3, 4, b'L')]
    c._process_message(c.N_MSG_ERROR, req, b'not ready')
    assert c.request_result(req) == ('error', 'not ready')


def test_lock_correct_viewed_station_once_waits_ack_then_dispatches():
    sim, c = setup(nearest=1)
    prepare(sim, c)
    lock = lock_command(c)
    assert len(lock) == 1
    assert lock[0][1:4] == (True, 0, 0)
    lock_id = lock[0][4]
    assert sim.state == ExperienceState.READY and sim._dispatch_pending
    assert not any(x[0] == 'dispatch' for x in c.commands)
    prepare(sim, c)
    assert len(lock_command(c)) == 1  # no uncontrolled retries
    # Even a CAN_DISPATCH bit cannot bypass a pending lock-command ACK.
    prepare(sim, c, flags=VIEW | CAN_DISPATCH | CAN_UNLOCK_FLYER)
    assert not any(x[0] == 'dispatch' for x in c.commands)
    c.complete_request(lock_id, ok=True)
    prepare(sim, c, flags=VIEW | CAN_DISPATCH | CAN_UNLOCK_FLYER)
    assert ('dispatch', 0, 0) in c.commands
    assert ('dispatch', 0, 1) not in c.commands
    assert sim._ride_station == (0, 0)
    assert sim.state == ExperienceState.RUNNING
    events = sim.consume_state_events()
    assert len([e for e in events if e['event'] == 'flyer_lock_command_sent']) == 1
    assert len([e for e in events if e['event'] == 'flyer_lock_acknowledged']) == 1
    assert len([e for e in events if e['event'] == 'dispatch_command_sent']) == 1


def test_flying_beast_prelocked_manually_dispatches_without_lock_command():
    sim, c = setup(flags=VIEW | CAN_DISPATCH | CAN_UNLOCK_FLYER)
    prepare(sim, c, flags=VIEW | CAN_DISPATCH | CAN_UNLOCK_FLYER)
    assert not lock_command(c)
    assert ('dispatch', 0, 0) in c.commands


def test_lock_works_when_viewed_train_is_at_station_1():
    sim, c = setup(station=1, nearest=0)
    c.put(0, OTHER_NOT_READY | CAN_LOCK_FLYER)
    prepare(sim, c, station=1)
    assert lock_command(c)[0][1:4] == (True, 0, 1)
    assert not any(x[0] == 'flyer' and x[3] == 0 for x in c.commands)


@pytest.mark.parametrize('park', sorted(set(ride_config.PARKS) - {'flyingbeast'}))
def test_no_other_ride_issues_flyer_command(park):
    sim, c = setup(park=park)
    prepare(sim, c)
    assert not lock_command(c)
    assert not any(e['event'].startswith('flyer_lock') for e in sim.consume_state_events())


def test_wrong_station_train_stale_or_moving_telemetry_blocks_lock():
    # A station occupied by the other train must never receive a lock command.
    sim, c = setup(flags=OTHER_NOT_READY | CAN_LOCK_FLYER)
    prepare(sim, c, flags=OTHER_NOT_READY | CAN_LOCK_FLYER)
    assert not lock_command(c)
    # The viewed train is present but the telemetry is stale.
    sim, c = setup()
    c.latest_telemetry = telemetry(received_at=time.monotonic() - 5)
    c.put(0, VIEW_NOT_READY | CAN_LOCK_FLYER)
    sim._service_dispatch_preparation()
    assert not lock_command(c)
    # The train moved; do not try a seat lock during motion.
    sim, c = setup()
    c.latest_telemetry = telemetry(speed=3.0)
    c.put(0, VIEW_NOT_READY | CAN_LOCK_FLYER)
    sim._service_dispatch_preparation()
    assert not lock_command(c)
    # Telemetry for a different coaster must never authorise a seat lock.
    sim, c = setup()
    tm = telemetry()
    c.latest_telemetry = type(tm)(**{**tm.__dict__, 'coaster_index': 2})
    c.put(0, VIEW_NOT_READY | CAN_LOCK_FLYER)
    sim._service_dispatch_preparation()
    assert not lock_command(c)


def test_rejected_lock_fails_closed_even_if_late_dispatchable():
    sim, c = setup()
    prepare(sim, c)
    c.complete_request(lock_command(c)[0][4], ok=False, detail='seats cannot be locked')
    prepare(sim, c, flags=VIEW | CAN_DISPATCH)
    assert not sim._dispatch_pending
    assert sim.state == ExperienceState.READY
    assert not any(x[0] == 'dispatch' for x in c.commands)
    assert 'rejected' in sim._flyer_lock_failure
    assert any(e['event'] == 'flyer_lock_rejected' for e in sim.consume_state_events())
    prepare(sim, c, flags=VIEW | CAN_DISPATCH)
    assert not any(x[0] == 'dispatch' for x in c.commands)


def test_lock_ack_timeout_fails_closed_no_retry():
    sim, c = setup()
    prepare(sim, c)
    sim._flyer_lock_sent_at -= 10
    prepare(sim, c, flags=VIEW | CAN_DISPATCH)
    assert not sim._dispatch_pending
    assert not any(x[0] == 'dispatch' for x in c.commands)
    assert len(lock_command(c)) == 1
    assert any(e['event'] == 'flyer_lock_ack_timeout' for e in sim.consume_state_events())


def test_lock_ready_timeout_fails_closed_no_retry():
    sim, c = setup()
    prepare(sim, c)
    c.complete_request(lock_command(c)[0][4])
    prepare(sim, c)
    assert sim._flyer_lock_acknowledged
    sim._flyer_lock_sent_at -= 40
    prepare(sim, c, flags=VIEW_NOT_READY | CAN_LOCK_FLYER)
    assert not sim._dispatch_pending
    assert not any(x[0] == 'dispatch' for x in c.commands)
    assert any(e['event'] == 'flyer_lock_ready_timeout' for e in sim.consume_state_events())


def test_cancel_prevents_late_ack_and_ready_flags_from_dispatching():
    sim, c = setup()
    prepare(sim, c)
    request_id = lock_command(c)[0][4]
    sim.cancel_pending_dispatch()
    c.complete_request(request_id)
    prepare(sim, c, flags=VIEW | CAN_DISPATCH)
    assert not any(x[0] == 'dispatch' for x in c.commands)
    assert len(lock_command(c)) == 1


def test_no_speculative_cross_station_poll_during_lock_or_dispatch():
    sim, c = setup(nearest=1)
    prepare(sim, c)
    lock = lock_command(c)
    assert len(lock) == 1
    c.put(1, OTHER_NOT_READY | CAN_LOCK_FLYER)
    for _ in range(4):
        sim._service_dispatch_preparation()
    assert not any(x == ('get_station', 0, 1) for x in c.commands)
    assert not any(x[0] == 'flyer' and x[3] == 1 for x in c.commands)
    c.complete_request(lock[0][4])
    prepare(sim, c, flags=VIEW | CAN_DISPATCH | CAN_UNLOCK_FLYER)
    sim._service_flyer_dispatch_confirmation()
    assert not any(x == ('get_station', 0, 1) for x in c.commands)


def test_dispatch_ack_and_departure_timeout_are_separate_and_never_redispatch():
    sim, c = setup(flags=VIEW | CAN_DISPATCH)
    prepare(sim, c, flags=VIEW | CAN_DISPATCH)
    rid = sim._flyer_dispatch_request_id
    assert rid is not None
    c.complete_request(rid)
    sim._service_flyer_dispatch_confirmation()
    events = sim.consume_state_events()
    assert any(e['event'] == 'dispatch_command_acknowledged' for e in events)
    assert not any(e['event'] == 'view_train_departed' for e in events)
    sent_count = len([x for x in c.commands if x[0] == 'dispatch'])
    sim._flyer_dispatch_sent_at -= 30
    sim._service_flyer_dispatch_confirmation()
    sim._service_flyer_dispatch_confirmation()
    events = sim.consume_state_events()
    assert len([e for e in events if e['event'] == 'dispatch_departure_timeout']) == 1
    assert len([x for x in c.commands if x[0] == 'dispatch']) == sent_count


def test_confirmed_departure_prevents_timeout_warning():
    sim, c = setup(flags=VIEW | CAN_DISPATCH)
    prepare(sim, c, flags=VIEW | CAN_DISPATCH)
    tm = telemetry(speed=12, frame=22)
    c.latest_telemetry = tm
    c.put(0, MANUAL)
    sim._update_state(tm)
    assert sim._view_train_departed
    sim._flyer_dispatch_sent_at -= 40
    sim._service_flyer_dispatch_confirmation()
    assert not any(e['event'] == 'dispatch_departure_timeout'
                   for e in sim.consume_state_events())


def test_abort_clears_after_new_explicit_run_only():
    sim, c = setup()
    prepare(sim, c)
    c.complete_request(lock_command(c)[0][4], ok=False, detail='rejected')
    prepare(sim, c)
    assert sim._flyer_lock_failure
    assert not sim._dispatch_pending
    sim.run()  # operator authorises a new attempt
    assert sim._flyer_lock_failure == ''
    assert sim._dispatch_pending


def test_error_remains_visible_in_status_until_operator_reauthorises():
    sim, c = setup()
    prepare(sim, c)
    c.complete_request(lock_command(c)[0][4], ok=False, detail='lock not available')
    prepare(sim, c)
    sim._data_connected = True
    text = sim.get_status().text
    assert 'seat-lock request rejected' in text
    assert sim.state == ExperienceState.READY
    sim.run()
    assert 'seat-lock request rejected' not in sim.get_status().text


def test_production_service_processes_ack_and_dispatch_without_helper():
    sim, c = setup()
    sim._connected_sync_started = True
    sim._data_connected = True
    sim._last_api_probe = time.monotonic()
    prepare(sim, c)
    lock_id = lock_command(c)[0][4]
    c.complete_request(lock_id)
    c.latest_telemetry = telemetry(speed=0, frame=44)
    c.put(0, VIEW | CAN_DISPATCH)
    # Production order: client.service -> async ack -> station observation.
    sim.service()
    assert sim.state == ExperienceState.RUNNING
    assert ('dispatch', 0, 0) in c.commands
    dispatch_id = sim._flyer_dispatch_request_id
    c.complete_request(dispatch_id)
    sim.service()
    events = sim.consume_state_events()
    assert any(e['event'] == 'flyer_lock_acknowledged' for e in events)
    assert any(e['event'] == 'dispatch_command_acknowledged' for e in events)


def test_rejected_dispatch_cannot_authorise_auxiliary_train():
    sim, c = setup(flags=VIEW | CAN_DISPATCH)
    prepare(sim, c, flags=VIEW | CAN_DISPATCH)
    rid = sim._flyer_dispatch_request_id
    c.complete_request(rid, ok=False, detail='blocked')
    sim._service_flyer_dispatch_confirmation()
    assert not sim._aux_dispatch_authorized
    assert 'DISPATCH rejected' in sim._flyer_dispatch_failure
    assert any(e['event'] == 'dispatch_command_rejected'
               for e in sim.consume_state_events())
