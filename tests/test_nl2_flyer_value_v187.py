# test_nl2_flyer_value_v187.py ver 1
"""v187 verified flyer-lock mapping / restraint gating (no live NL2 required)."""
from __future__ import annotations

import struct

import pytest

from core.types import ExperienceState
from sims.nolimits2 import ride_config
from sims.nolimits2.nl2_protocol import NL2Protocol
from tests.test_nl2_flyer_lock_v185 import (
    SocketSpy, setup, prepare, lock_command, VIEW,
    CAN_DISPATCH, CAN_UNLOCK_FLYER,
)
from tools.nl2_local_test_harness.harness_core import DiagnosticProtocol, toggle_payload


def test_protocol_mapping_matches_field_verified_lock_value_zero():
    protocol = NL2Protocol('127.0.0.1')
    protocol.is_connected = True
    spy = SocketSpy()
    protocol._sock = spy
    lock_req = protocol.set_flyer_car(locked=True, coaster=2, station=1)
    unlock_req = protocol.set_flyer_car(locked=False, coaster=2, station=1)
    assert spy.packets[0] == struct.pack('>cHIHiiBc', b'N', 21, lock_req, 9,
                                         2, 1, 0, b'L')
    assert spy.packets[1] == struct.pack('>cHIHiiBc', b'N', 21, unlock_req, 9,
                                         2, 1, 1, b'L')
    assert spy.packets[0][9:-1] == toggle_payload(2, 1, 0)
    assert spy.packets[1][9:-1] == toggle_payload(2, 1, 1)


def test_command_ack_and_new_dispatch_ready_flag_required_before_dispatch():
    sim, client = setup(nearest=1)
    prepare(sim, client)
    commands = lock_command(client)
    assert len(commands) == 1
    assert commands[0][1:4] == (True, 0, 0)
    events = sim.consume_state_events()
    lock_event = [e for e in events if e['event'] == 'flyer_lock_command_sent']
    assert len(lock_event) == 1 and lock_event[0]['raw_state'] == 0
    assert not any(c[0] == 'dispatch' for c in client.commands)
    client.complete_request(commands[0][4], ok=True)
    prepare(sim, client)  # NL2 ACK alone is not a seat-state sensor
    assert sim._flyer_lock_acknowledged
    assert sim.state is ExperienceState.READY
    assert not any(c[0] == 'dispatch' for c in client.commands)
    prepare(sim, client, flags=VIEW | CAN_DISPATCH | CAN_UNLOCK_FLYER)
    assert client.commands.count(('dispatch', 0, 0)) == 1
    assert sim.state is ExperienceState.RUNNING
    assert len(lock_command(client)) == 1


def test_manual_prelocked_station_dispatches_without_lock():
    sim, client = setup(flags=VIEW | CAN_DISPATCH | CAN_UNLOCK_FLYER)
    prepare(sim, client, flags=VIEW | CAN_DISPATCH | CAN_UNLOCK_FLYER)
    assert not lock_command(client)
    assert client.commands.count(('dispatch', 0, 0)) == 1


@pytest.mark.parametrize('park', sorted(set(ride_config.PARKS) - {'flyingbeast'}))
def test_other_park_dispatch_path_does_not_use_flyer_lock(park):
    sim, client = setup(park=park, flags=VIEW | CAN_DISPATCH)
    prepare(sim, client, flags=VIEW | CAN_DISPATCH)
    assert not lock_command(client)
    assert client.commands.count(('dispatch', 0, 0)) == 1


def test_test_harness_shares_production_protocol_and_does_not_duplicate_it():
    assert issubclass(DiagnosticProtocol, NL2Protocol)
    assert DiagnosticProtocol.__mro__[1] is NL2Protocol
    from pathlib import Path
    tool_dir = Path(__file__).resolve().parents[1] / 'tools' / 'nl2_local_test_harness'
    assert not (tool_dir / 'nl2_protocol.py').exists()
