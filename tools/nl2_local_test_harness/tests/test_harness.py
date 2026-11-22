# test_harness.py ver 1
"""Network-level regression tests using a local fake NL2 server (no NL2 required)."""
import socket
import socketserver
import struct
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harness_core import (
    DiagnosticProtocol, station_flags, station_summary,
    pair_payload, toggle_payload, parse_hex_bytes, reply_summary,
)


def recv_exact(sock, count):
    chunks = bytearray()
    while len(chunks) < count:
        data = sock.recv(count - len(chunks))
        if not data:
            raise ConnectionError('socket closed')
        chunks.extend(data)
    return bytes(chunks)


def frame(msgid, reqid, body):
    return struct.pack('>cHIH', b'N', msgid, reqid, len(body)) + body + b'L'


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        s = self.request
        s.settimeout(1)
        while True:
            try:
                if recv_exact(s, 1) != b'N':
                    continue
                msgid, reqid, size = struct.unpack('>HIH', recv_exact(s, 8))
                payload = recv_exact(s, size)
                if recv_exact(s, 1) != b'L':
                    return
            except (OSError, ConnectionError):
                return
            with self.server.lock:
                self.server.received.append((msgid, reqid, payload))
                states = dict(self.server.states)
            if msgid == 3:
                answer_id, answer = 4, bytes((2, 6, 4, 0))
            elif msgid == 11:
                answer_id, answer = 12, struct.pack('>ii', 0, 0)
            elif msgid == 14:
                coaster, station = struct.unpack('>ii', payload)
                if (coaster, station) not in states:
                    answer_id, answer = 2, b'Unknown station'
                else:
                    answer_id, answer = 15, struct.pack('>I', states[(coaster, station)])
            elif msgid == 5:
                answer_id = 6
                answer = struct.pack('>IIIIIIIIfffffffffff',
                                     1, 115, 1, 0, 0, 0, 0, 0,
                                     0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0)
            elif msgid == 7:
                answer_id, answer = 8, struct.pack('>i', 1)
            elif msgid == 9:
                answer_id, answer = 10, b'Flying Beast'
            else:
                answer_id, answer = 1, b''
            try:
                s.sendall(frame(answer_id, reqid, answer))
            except (OSError, ConnectionError):
                return


class FakeNL2Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self):
        super().__init__(('127.0.0.1', 0), Handler)
        self.received = []
        self.states = {(0, 0): 0x1A52, (0, 1): 0x0800}
        self.lock = threading.Lock()


class TestCore(unittest.TestCase):
    def test_station_flags_and_hex(self):
        f = station_flags(0x1A52)
        self.assertTrue(f['MANUAL'])
        self.assertTrue(f['CAN_LOCK_FLYER'])
        self.assertTrue(f['TRAIN_PRESENT'])
        self.assertTrue(f['VIEW_TRAIN_HERE'])
        self.assertFalse(f['CAN_DISPATCH'])
        self.assertIn('0x00001A52', station_summary(0x1A52))
        self.assertEqual(parse_hex_bytes('0x00 0x00, 01'), b'\x00\x00\x01')
        self.assertEqual(parse_hex_bytes(''), b'')
        with self.assertRaises(ValueError):
            parse_hex_bytes('ABC')
        self.assertIn('NOT proof', reply_summary(1, b''))

    def test_payloads_are_wire_compatible(self):
        self.assertEqual(pair_payload(0, 1), b'\0\0\0\0\0\0\0\1')
        self.assertEqual(toggle_payload(0, 0, 1), b'\0'*8 + b'\1')
        self.assertEqual(toggle_payload(0, 0, 0), b'\0'*9)
        for invalid in (-1, 2):
            with self.assertRaises(ValueError):
                toggle_payload(0, 0, invalid)


class TestTCP(unittest.TestCase):
    def setUp(self):
        self.server = FakeNL2Server()
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.client = DiagnosticProtocol('127.0.0.1', self.server.server_address[1])
        self.assertTrue(self.client.connect())

    def tearDown(self):
        self.client.disconnect()
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=2)

    def pump_until(self, predicate, timeout=2.0):
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            self.client.service()
            if predicate():
                return True
            time.sleep(0.01)
        return False

    def test_connect_is_read_only_until_explicit_queries(self):
        time.sleep(0.04)
        self.assertEqual(self.server.received, [])
        self.client.request_version()
        self.assertTrue(self.pump_until(lambda: self.client.version == '2.6.4.0'))
        self.assertEqual(self.server.received[0][0], 3)

    def test_station_indices_correlated_and_cache(self):
        a = self.client.send_command(14, pair_payload(0, 1))
        b = self.client.send_command(14, pair_payload(0, 0))
        self.assertNotEqual(a, b)
        self.assertTrue(self.pump_until(lambda: self.client.station_snapshot((0, 1)) is not None and self.client.station_snapshot((0, 0)) is not None))
        self.assertEqual(self.client.station_snapshot((0, 0))[0], 0x1A52)
        self.assertEqual(self.client.station_snapshot((0, 1))[0], 0x0800)
        self.assertTrue(all(row[1] in (a, b) for row in self.server.received))

    def test_flyer_values_send_exact_bytes_ack_does_not_change_station(self):
        r0 = self.client.send_command(21, toggle_payload(0, 0, 0))
        r1 = self.client.send_command(21, toggle_payload(0, 0, 1))
        self.assertTrue(self.pump_until(lambda: self.client.request_result(r0) is not None and self.client.request_result(r1) is not None))
        self.assertEqual(self.client.request_result(r0), ('ok', ''))
        self.assertEqual(self.client.request_result(r1), ('ok', ''))
        self.assertEqual(self.server.received[0], (21, r0, b'\0'*9))
        self.assertEqual(self.server.received[1], (21, r1, b'\0'*8 + b'\1'))
        self.client.request_station_state(0, 0)
        self.assertTrue(self.pump_until(lambda: self.client.station_snapshot((0, 0)) is not None))
        self.assertEqual(self.client.station_snapshot((0, 0))[0], 0x1A52)

    def test_same_flyer_payload_as_original_poc_method(self):
        # Compare the standalone raw sender to the proven production writer.
        explicit = self.client.send_command(21, toggle_payload(0, 0, 0))
        production = self.client.set_flyer_car(True, coaster=0, station=0)
        self.assertTrue(self.pump_until(lambda: self.client.request_result(production) is not None))
        records = [r for r in self.server.received if r[0] == 21]
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0][2], records[1][2])
        self.assertEqual(records[0][2], b'\0'*9)
        self.assertNotEqual(explicit, production)

    def test_semantic_unlock_is_raw_one(self):
        req = self.client.set_flyer_car(False, coaster=0, station=1)
        self.assertTrue(self.pump_until(lambda: self.client.request_result(req) is not None))
        self.assertEqual(self.server.received[0][2], pair_payload(0, 1) + b'\1')
        self.assertEqual(self.client.request_result(req), ('ok', ''))

    def test_manual_and_dispatch_frames_and_ack(self):
        manual = self.client.send_command(16, toggle_payload(0, 0, 1))
        dispatch = self.client.send_command(17, pair_payload(0, 0))
        self.assertTrue(self.pump_until(lambda: self.client.request_result(dispatch) is not None))
        self.assertEqual(self.client.request_result(manual), ('ok', ''))
        self.assertEqual(self.client.request_result(dispatch), ('ok', ''))
        self.assertEqual(self.server.received[0][0], 16)
        self.assertEqual(self.server.received[1][0], 17)
        self.assertEqual(self.server.received[1][2], b'\0'*8)

    def test_telemetry_and_nearest(self):
        self.client.request_telemetry()
        self.client.send_command(11, b'')
        self.assertTrue(self.pump_until(lambda: self.client.latest_telemetry is not None and self.client.current_station_index == 0))
        self.assertEqual(self.client.latest_telemetry.frame, 115)
        self.assertEqual(self.client.current_coaster_index, 0)

    def test_read_only_additional_replies(self):
        r1 = self.client.send_command(7, b'')
        r2 = self.client.send_command(9, struct.pack('>i', 0))
        self.assertTrue(self.pump_until(lambda: len(self.client.events) >= 2))
        events = list(self.client.events)
        self.assertTrue(any(ev[2] == r1 and ev[1] == 8 for ev in events))
        self.assertTrue(any(ev[2] == r2 and ev[1] == 10 for ev in events))

    def test_validation_rejects_bad_station_read_and_server_message(self):
        with self.assertRaises(ValueError):
            self.client.send_command(14, b'wrong')
        with self.assertRaises(ValueError):
            self.client.send_command(1, b'')
        with self.assertRaises(ValueError):
            self.client.send_command(65536, b'')
        self.assertEqual(self.server.received, [])

if __name__ == '__main__':
    unittest.main()
