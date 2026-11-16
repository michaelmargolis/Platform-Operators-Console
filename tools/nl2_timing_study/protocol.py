# protocol.py ver 1
"""Instrument the production transport without changing framing or dispatch rules.

The receive queue timestamps a fully received packet on the existing listener
thread. The production parser still processes it on the adapter thread. Neither
clock is a timestamp provided by NL2's simulation; frame IDs are NOT seconds.
"""
from __future__ import annotations
from collections import OrderedDict, deque
import queue
import struct
import time
from .clock import clock_ns
from sims.nolimits2.nl2_protocol import NL2Protocol


class ReceiptQueue(queue.Queue):
    def __init__(self):
        super().__init__()
        self.last_receipt_ns = 0

    def put(self, item, block=True, timeout=None):
        super().put((item, clock_ns()), block, timeout)

    def get(self, block=True, timeout=None):
        item, self.last_receipt_ns = super().get(block, timeout)
        return item


class TimingProtocol(NL2Protocol):
    """Only loopback is accepted. Reuses all production socket/parser methods."""
    MAX_RECORDS = 50000

    def __init__(self, address="127.0.0.1", port=15151, **kwargs):
        if address not in ("localhost", "127.0.0.1"):
            raise ValueError("This timing harness only connects to localhost (127.0.0.1)")
        super().__init__("127.0.0.1", port=port, **kwargs)
        self._rx_queue = ReceiptQueue()
        self._study_pending = OrderedDict()
        self._study_records = deque()
        self.study_overflow = 0

    def _record(self, record):
        if len(self._study_records) >= self.MAX_RECORDS:
            self._study_records.popleft()
            self.study_overflow += 1
        self._study_records.append(record)

    def drain_study_records(self):
        result = list(self._study_records)
        self._study_records.clear()
        return result

    def _send(self, data):
        # Identical bytes go through the existing protocol; no extra requests.
        msg, request, size = struct.unpack(">HIH", data[1:9])
        before = clock_ns()
        super()._send(data)
        after = clock_ns()
        self._study_pending[request] = (msg, before, after)
        while len(self._study_pending) > 10000:
            self._study_pending.popitem(last=False)
        self._record(dict(kind="tx", msg=msg, request_id=request,
                          send_ns=before, send_end_ns=after, receive_ns=None,
                          process_ns=None, request_msg=msg,
                          payload_hex=data[9:9+size].hex(), telemetry=None))

    def _process_message(self, msg_id, request_id, data):
        received = self._rx_queue.last_receipt_ns or clock_ns()
        processed = clock_ns()
        request = self._study_pending.pop(request_id, None)
        # The production telemetry.received_at remains the service time. Do NOT
        # change it: that would alter yaw differentiation/state machine timing.
        super()._process_message(msg_id, request_id, data)
        tm = self.latest_telemetry if (msg_id == self.N_MSG_TELEMETRY
                                      and len(data) == self.TELEMETRY_SIZE) else None
        self._record(dict(kind="rx", msg=msg_id, request_id=request_id,
                          send_ns=request[1] if request else None,
                          send_end_ns=request[2] if request else None,
                          receive_ns=received, process_ns=processed,
                          request_msg=request[0] if request else None,
                          payload_hex="" if tm else data.hex(), telemetry=tm))
