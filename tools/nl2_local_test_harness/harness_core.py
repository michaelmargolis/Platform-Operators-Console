# harness_core.py ver 1 - Read-only helpers and explicit test commands.
# Reuses the production POC protocol module; no duplicated protocol implementation.
from __future__ import annotations

from collections import deque
from pathlib import Path
import struct
import sys
import time
from typing import Dict, Tuple

# Direct execution (py tools/nl2_local_test_harness/harness.py) does not put
# the POC project root on sys.path. Resolve it from our installed location,
# and import ONLY the protocol module (not POC/Qt/SHS/hardware state machine).
POC_ROOT = Path(__file__).resolve().parents[2]
if not (POC_ROOT / 'sims' / 'nolimits2' / 'nl2_protocol.py').is_file():
    raise RuntimeError('This harness must reside in POC/tools/nl2_local_test_harness')
if str(POC_ROOT) not in sys.path:
    sys.path.insert(0, str(POC_ROOT))

from sims.nolimits2.nl2_protocol import NL2Protocol

STATION_BITS = (
    (0, 'E_STOP'),
    (1, 'MANUAL'),
    (2, 'CAN_DISPATCH'),
    (3, 'CAN_CLOSE_GATES'),
    (4, 'CAN_OPEN_GATES'),
    (5, 'CAN_CLOSE_HARNESS'),
    (6, 'CAN_OPEN_HARNESS'),
    (7, 'CAN_RAISE_PLATFORM'),
    (8, 'CAN_LOWER_PLATFORM'),
    (9, 'CAN_LOCK_FLYER'),
    (10, 'CAN_UNLOCK_FLYER'),
    (11, 'TRAIN_PRESENT'),
    (12, 'VIEW_TRAIN_HERE'),
)
CLIENT_MESSAGE_NAMES = {
    0: 'IDLE', 3: 'GET_VERSION', 5: 'GET_TELEMETRY',
    7: 'GET_COASTER_COUNT', 9: 'GET_COASTER_NAME',
    11: 'GET_CURRENT_COASTER_AND_NEAREST_STATION',
    13: 'SET_EMERGENCY_STOP', 14: 'GET_STATION_STATE',
    16: 'SET_MANUAL_MODE', 17: 'DISPATCH', 18: 'SET_GATES',
    19: 'SET_HARNESS', 20: 'SET_PLATFORM', 21: 'SET_FLYER_CAR',
    24: 'LOAD_PARK', 25: 'CLOSE_PARK', 26: 'QUIT_SERVER',
    27: 'SET_PAUSE', 28: 'RESET_PARK', 29: 'SELECT_SEAT',
    30: 'SET_ATTRACTION_MODE', 31: 'RECENTER_VR',
    32: 'SET_CUSTOM_VIEW',
}
READ_ONLY_IDS = frozenset((0, 3, 5, 7, 9, 11, 14))
HEAVY_ACTION_IDS = frozenset((13, 17, 24, 25, 26, 27, 28, 30))
SERVER_REPLY_IDS = frozenset((1, 2, 4, 6, 8, 10, 12, 15))


def station_flags(raw: int) -> Dict[str, bool]:
    return {name: bool(int(raw) & (1 << bit)) for bit, name in STATION_BITS}


def station_summary(raw: int) -> str:
    flags = station_flags(raw)
    return ('0x%08X manual=%s present=%s viewed=%s '
            'dispatch=%s lock_available=%s unlock_available=%s') % (
                raw, int(flags['MANUAL']), int(flags['TRAIN_PRESENT']),
                int(flags['VIEW_TRAIN_HERE']), int(flags['CAN_DISPATCH']),
                int(flags['CAN_LOCK_FLYER']), int(flags['CAN_UNLOCK_FLYER']))


def pair_payload(coaster: int, station: int) -> bytes:
    if not (0 <= coaster <= 2**31 - 1 and 0 <= station <= 2**31 - 1):
        raise ValueError('Coaster and station indices must be nonnegative int32')
    return struct.pack('>ii', coaster, station)


def toggle_payload(coaster: int, station: int, value: int) -> bytes:
    if value not in (0, 1):
        raise ValueError('State value must be 0 or 1')
    return pair_payload(coaster, station) + bytes((value,))


def parse_hex_bytes(value: str) -> bytes:
    text = value.strip().replace(',', ' ').replace(':', ' ')
    text = text.replace('0x', '').replace('0X', '')
    if not text:
        return b''
    try:
        return bytes.fromhex(text)
    except ValueError as exc:
        raise ValueError('Payload must be complete hexadecimal bytes, e.g. 00 00 00 00') from exc


def reply_summary(msg_id: int, data: bytes) -> str:
    if msg_id == 1:
        return 'OK (request accepted, NOT proof of actuator or train movement)'
    if msg_id == 2:
        return 'ERROR: ' + data.decode('utf-8', errors='replace')
    if msg_id == 4 and len(data) >= 4:
        return 'VERSION ' + '.'.join(str(byte) for byte in data[:4])
    if msg_id == 8 and len(data) >= 4:
        return 'INT_VALUE ' + str(struct.unpack('>i', data[:4])[0])
    if msg_id == 10:
        return 'STRING ' + repr(data.decode('utf-8', errors='replace'))
    if msg_id == 12 and len(data) >= 8:
        return 'CURRENT coaster=%d nearest_station=%d' % struct.unpack('>ii', data[:8])
    if msg_id == 15 and len(data) >= 4:
        return 'STATION ' + station_summary(struct.unpack('>I', data[:4])[0])
    if msg_id == 6 and len(data) >= 12:
        state, frame = struct.unpack('>ii', data[:8])
        return 'TELEMETRY state=%d frame=%d' % (state, frame)
    return 'reply id=%d payload=%s' % (msg_id, data.hex(' '))


class DiagnosticProtocol(NL2Protocol):
    """Thin extension of the shared production POC NL2Protocol.

    Inherits the actual POC socket receiver, message parsing, request tracking and
    station snapshot cache. This extension exposes arbitrary documented client
    commands as a framed message with a known request ID, and retains received
    replies for GUI diagnostics. All GUI polling and processing runs on one thread.
    """

    def __init__(self, address='127.0.0.1', port=15151, **kwargs):
        super().__init__(address, port=port, **kwargs)
        self.events = deque(maxlen=5000)
        self.raw_requests: Dict[int, Tuple[int, bytes]] = {}

    def send_command(self, msg_id: int, payload: bytes = b'') -> int:
        msg_id = int(msg_id)
        payload = bytes(payload)
        if not (0 <= msg_id <= 65535):
            raise ValueError('NL2 message ID outside ushort16 range')
        if msg_id in SERVER_REPLY_IDS:
            raise ValueError('Message ID %d is server-reply-only' % msg_id)
        if len(payload) > 65535:
            raise ValueError('Payload too long for NL2 ushort16 size')

        req_id = self._request_id()
        if msg_id == self.N_MSG_GET_STATION_STATE:
            if len(payload) != 8:
                raise ValueError('GET_STATION_STATE requires 8-byte coaster/station payload')
            self._station_requests[req_id] = struct.unpack('>ii', payload)
            while len(self._station_requests) > 64:
                self._station_requests.pop(next(iter(self._station_requests)))
        if msg_id == self.N_MSG_GET_CURRENT_COASTER_AND_NEAREST_STATION:
            if payload:
                raise ValueError('GET_CURRENT_COASTER_AND_NEAREST_STATION payload must be empty')
            self._nearest_station_requests[req_id] = time.monotonic()
            while len(self._nearest_station_requests) > 64:
                self._nearest_station_requests.pop(next(iter(self._nearest_station_requests)))

        frame = struct.pack('>cHIH', b'N', msg_id, req_id, len(payload)) + payload + b'L'
        self._send(frame)  # Shared POC send/framing/threaded TCP implementation.
        self.raw_requests[req_id] = (msg_id, payload)
        while len(self.raw_requests) > 256:
            self.raw_requests.pop(next(iter(self.raw_requests)))
        return req_id

    def _process_message(self, msg_id: int, request_id: int, data: bytes) -> None:
        super()._process_message(msg_id, request_id, data)
        self.events.append((time.monotonic(), int(msg_id), int(request_id), bytes(data)))

    def disconnect(self) -> None:
        super().disconnect()
        self.raw_requests.clear()
        self.events.clear()
