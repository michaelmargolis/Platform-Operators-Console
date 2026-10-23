# test_nl2_request_ids_v156.py  ver 1
"""Regression: NL2 load/telemetry commands must never share a request ID."""

import struct
from unittest.mock import patch

from sims.nolimits2.nl2_protocol import NL2Protocol


class CaptureSocket:
    def __init__(self):
        self.packets = []

    def sendall(self, packet):
        self.packets.append(packet)


def test_load_park_and_telemetry_have_distinct_ids_within_one_millisecond():
    protocol = NL2Protocol('127.0.0.1')
    fake_socket = CaptureSocket()
    protocol.is_connected = True
    protocol._sock = fake_socket

    # Force the old millisecond-timestamp ID implementation to collide.
    with patch('sims.nolimits2.nl2_protocol.time.monotonic', return_value=protocol._start_time + 0.125):
        load_id = protocol.load_park('C:/Rides/Blackbird.nl2park')
        protocol.request_telemetry()
        protocol.request_station_state(0, 0)

    message_ids = [struct.unpack('>HIH', packet[1:9])[:2] for packet in fake_socket.packets]
    assert [item[0] for item in message_ids] == [
        protocol.N_MSG_LOAD_PARK,
        protocol.N_MSG_GET_TELEMETRY,
        protocol.N_MSG_GET_STATION_STATE,
    ]
    assert len({request_id for _, request_id in message_ids}) == 3
    assert load_id == message_ids[0][1]

    # During a park load NL2 can reject GET_TELEMETRY as not in play mode.
    # This must not be reported as a failure of LOAD_PARK.
    protocol._process_message(protocol.N_MSG_ERROR, message_ids[1][1], b'Not in play mode')
    assert protocol.request_result(load_id) is None
    protocol._process_message(protocol.N_MSG_OK, load_id, b'')
    assert protocol.request_result(load_id) == ('ok', '')


def test_request_ids_remain_unique_over_many_fast_requests():
    protocol = NL2Protocol('127.0.0.1')
    with patch('sims.nolimits2.nl2_protocol.time.monotonic', return_value=protocol._start_time + 0.001):
        values = [protocol._request_id() for _ in range(1000)]
    assert len(set(values)) == len(values)
