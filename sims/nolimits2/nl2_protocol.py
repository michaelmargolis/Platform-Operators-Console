#nl2_protocol.py  ver 20
from __future__ import annotations

from dataclasses import dataclass
import logging
import queue
import socket
import struct
import threading
import time
from typing import Optional

log = logging.getLogger(__name__)

# Set True while diagnosing the NL2 datalink.  This only adds protocol-level
# TX/RX logging; it does not change connection or ride-state behaviour.
NL2_COMMS_DEBUG = False

# When protocol debug is enabled, suppress the high-rate polling traffic so
# control, connection and error messages remain easy to see.  This suppresses
# GET_TELEMETRY (5), TELEMETRY (6), GET_STATION_STATE (14), and
# STATION_STATE (15).
NL2_COMMS_DEBUG_SUPPRESS_DATA = True


@dataclass(frozen=True)
class NL2Telemetry:
    state: int
    frame: int
    view_mode: int
    coaster_index: int
    coaster_style: int
    train: int
    car: int
    seat: int
    speed: float
    pos_x: float
    pos_y: float
    pos_z: float
    quat_x: float
    quat_y: float
    quat_z: float
    quat_w: float
    g_force_x: float
    g_force_y: float
    g_force_z: float
    received_at: float

    @property
    def in_play_mode(self) -> bool:
        return bool(self.state & 0x01)

    @property
    def paused(self) -> bool:
        # Existing MDX NL2 implementations use state 3 for running and 7 for
        # paused. Bit 0 remains the play-mode flag in both states.
        return self.state == 7


class NL2Protocol:
    """Small Python 3 NL2 TCP protocol client for the POC prototype.

    Connection deliberately stays close to the proven MDX NL2 implementations:
    establish the TCP socket and make it available immediately.  The simulator
    adapter sends GET_VERSION as a separate API-health request, so TCP and NL2
    API connection state remain independent.

    Higher-level ride state interpretation belongs in sim.py.
    """

    N_MSG_OK = 1
    N_MSG_ERROR = 2
    N_MSG_GET_VERSION = 3
    N_MSG_VERSION = 4
    N_MSG_GET_TELEMETRY = 5
    N_MSG_TELEMETRY = 6
    N_MSG_GET_CURRENT_COASTER_AND_NEAREST_STATION = 11
    N_MSG_INT_VALUE_PAIR = 12
    N_MSG_GET_STATION_STATE = 14
    N_MSG_STATION_STATE = 15
    N_MSG_SET_MANUAL_MODE = 16
    N_MSG_DISPATCH = 17
    N_MSG_SET_PLATFORM = 20
    N_MSG_LOAD_PARK = 24
    N_MSG_CLOSE_PARK = 25
    N_MSG_SET_PAUSE = 27
    N_MSG_RESET_PARK = 28
    N_MSG_RECENTER_VR = 31

    PORT = 15151
    TELEMETRY_SIZE = 76
    TELEMETRY_FORMAT = ">IIIIIIIIfffffffffff"

    _MSG_NAMES = {
        N_MSG_OK: "OK",
        N_MSG_ERROR: "ERROR",
        N_MSG_GET_VERSION: "GET_VERSION",
        N_MSG_VERSION: "VERSION",
        N_MSG_GET_TELEMETRY: "GET_TELEMETRY",
        N_MSG_TELEMETRY: "TELEMETRY",
        N_MSG_GET_CURRENT_COASTER_AND_NEAREST_STATION: "GET_CURRENT_COASTER_AND_NEAREST_STATION",
        N_MSG_INT_VALUE_PAIR: "INT_VALUE_PAIR",
        N_MSG_GET_STATION_STATE: "GET_STATION_STATE",
        N_MSG_STATION_STATE: "STATION_STATE",
        N_MSG_SET_MANUAL_MODE: "SET_MANUAL_MODE",
        N_MSG_DISPATCH: "DISPATCH",
        N_MSG_SET_PLATFORM: "SET_PLATFORM",
        N_MSG_LOAD_PARK: "LOAD_PARK",
        N_MSG_CLOSE_PARK: "CLOSE_PARK",
        N_MSG_SET_PAUSE: "SET_PAUSE",
        N_MSG_RESET_PARK: "RESET_PARK",
        N_MSG_RECENTER_VR: "RECENTER_VR",
    }

    def __init__(
        self,
        address: str,
        port: int = PORT,
        connect_timeout: float = 1.0,
    ):
        self.address = address
        self.port = int(port)
        self.connect_timeout = float(connect_timeout)

        # is_connected means that the TCP socket is established.  Simulator/API
        # connectivity is deliberately determined separately by the adapter
        # from valid NL2 responses.
        self.is_connected = False
        self.version: Optional[str] = None
        self.latest_telemetry: Optional[NL2Telemetry] = None
        self.current_coaster_index: Optional[int] = None
        self.current_station_index: Optional[int] = None
        self.current_station_received_at = 0.0
        self.station_status: Optional[int] = None
        self.station_status_received_at = 0.0
        self.station_status_station = None  # coaster/station identified by reply request ID
        self._station_requests = {}
        # Per-station responses are required while monitoring the pinned
        # departure station and discovering a different arrival station.
        self._station_status_by_identity = {}
        self._nearest_station_requests = {}
        self.last_response_at = 0.0
        self.last_version_response_at = 0.0
        self.last_version_request_at = 0.0
        self.last_error = ""
        # Request-specific completion is needed for asynchronous operations such
        # as LOAD_PARK.  Do not infer completion from telemetry because loading
        # the park already in use may produce no observable play-mode transition.
        self._request_results = {}
        self.connect_attempts = 0
        self.last_connect_attempt_at = 0.0

        self._sock: Optional[socket.socket] = None
        self._rx_queue: queue.Queue = queue.Queue()
        self._listener: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._send_lock = threading.Lock()
        self._start_time = time.monotonic()
        # Millisecond timestamps are not unique: LOAD_PARK and the subsequent
        # telemetry/station polls can be sent within the same millisecond.
        # A telemetry ERROR could then be mistaken for the LOAD_PARK result.
        # Seed a sequential 32-bit request counter once and never reuse an ID
        # within the same session, even for back-to-back requests.
        self._request_id_lock = threading.Lock()
        self._request_id_counter = int(self._start_time * 1000) & 0xFFFFFFFF

    def connect(self) -> bool:
        """Open the NL2 TCP socket and start the receiver.

        This deliberately does not wait for VERSION or telemetry.  A successful
        return means only that the TCP datalink exists.  The adapter separately
        proves simulator/API connectivity from NL2 responses.
        """
        if self.is_connected:
            return True

        self.connect_attempts += 1
        self.last_connect_attempt_at = time.monotonic()
        self._close_socket()
        self._clear_rx_queue()

        try:
            log.debug("Attempting connect to NoLimits @ %s:%d", self.address, self.port)
            sock = socket.create_connection((self.address, self.port), self.connect_timeout)
            sock.settimeout(0.5)
            self._sock = sock
            self._stop_event.clear()
            self.is_connected = True
            self.version = None
            self.last_response_at = 0.0
            self.last_version_response_at = 0.0
            self.last_error = ""

            self._listener = threading.Thread(
                target=self._listener_loop,
                args=(sock,),
                name="nl2-listener",
                daemon=True,
            )
            self._listener.start()
            return True
        except OSError as exc:
            self.last_error = str(exc)
            self.is_connected = False
            self._close_socket()
            return False

    def disconnect(self) -> None:
        self._stop_event.set()
        self.is_connected = False
        self._close_socket()
        self._clear_rx_queue()
        self.version = None
        self.latest_telemetry = None
        self.invalidate_current_station()
        self.station_status_received_at = 0.0
        self.last_response_at = 0.0
        self.last_version_response_at = 0.0

    def service(self) -> None:
        while True:
            try:
                msg_id, request_id, data = self._rx_queue.get_nowait()
            except queue.Empty:
                break
            self._process_message(msg_id, request_id, data)

    def request_version(self) -> None:
        self.last_version_request_at = time.monotonic()
        self._send(self._simple_message(self.N_MSG_GET_VERSION))

    def request_telemetry(self) -> None:
        self._send(self._simple_message(self.N_MSG_GET_TELEMETRY))

    def request_current_coaster_and_station(self) -> None:
        # Bind the nearest-station response to this request, as we already do
        # for GET_STATION_STATE. Old responses cannot replace the current query.
        request_id = self._request_id()
        self._nearest_station_requests[request_id] = time.monotonic()
        while len(self._nearest_station_requests) > 64:
            self._nearest_station_requests.pop(next(iter(self._nearest_station_requests)))
        packet = struct.pack(">cHIHc", b"N",
                             self.N_MSG_GET_CURRENT_COASTER_AND_NEAREST_STATION,
                             request_id, 0, b"L")
        self._send(packet)

    def invalidate_current_station(self) -> None:
        self.current_coaster_index = None
        self.current_station_index = None
        self.current_station_received_at = 0.0
        self.station_status = None
        self.station_status_received_at = 0.0
        self.station_status_station = None
        self._station_requests.clear()
        self._station_status_by_identity.clear()
        self._nearest_station_requests.clear()

    def station_snapshot(self, identity):
        """Return (status, monotonic reception time) for an identified station."""
        return self._station_status_by_identity.get(tuple(identity))

    def request_station_state(self, coaster: int = 0, station: int = 0) -> None:
        # Requests may overlap. Match the reply ID to its requested station;
        # the most recently cached nearest station is NOT proof of identity.
        identity = (int(coaster), int(station))
        data = struct.pack(">ii", *identity)
        request_id = self._request_id()
        self._station_requests[request_id] = identity
        # Bound outstanding polls if the simulator stops replying.
        while len(self._station_requests) > 64:
            self._station_requests.pop(next(iter(self._station_requests)))
        packet = struct.pack(">cHIH", b"N", self.N_MSG_GET_STATION_STATE,
                             request_id, len(data)) + data + b"L"
        self._send(packet)

    def set_manual_mode(self, enabled: bool = True, coaster: int = 0, station: int = 0) -> None:
        # Match the tested attraction/manual-mode interface: coaster index,
        # station index, then boolean True for manual mode.  Manual mode disables
        # NL2 automatic station dispatch; POC dispatches explicitly with msg 17.
        data = struct.pack(">ii?", int(coaster), int(station), bool(enabled))
        self._send(self._message(self.N_MSG_SET_MANUAL_MODE, data))

    def dispatch(self, coaster: int = 0, station: int = 0) -> None:
        self._send(self._message(self.N_MSG_DISPATCH, struct.pack(">ii", coaster, station)))

    def set_platform(self, lowered: bool, coaster: int = 0, station: int = 0) -> None:
        """Lower/raise the NL2 station platform.

        Matches the tested coaster interface N_MSG_SET_PLATFORM payload:
        coaster index, station index, then True to lower/disengage the floor.
        """
        data = struct.pack(">ii?", int(coaster), int(station), bool(lowered))
        self._send(self._message(self.N_MSG_SET_PLATFORM, data))

    def load_park(self, park_path: str, start_paused: bool = True) -> int:
        # NL2 expects Windows absolute paths in /C:/... form.  Keep the config
        # readable on either Windows or Pi, normalize separators here, and
        # encode the path exactly once at the protocol boundary.
        path = str(park_path).replace("\\", "/")
        if len(path) >= 3 and path[1] == ":" and path[2] == "/":
            path = "/" + path
        if NL2_COMMS_DEBUG:
            log.debug('NL2 LOAD_PARK wire path="%s"', path)
        data = struct.pack(">?", bool(start_paused)) + path.encode("utf-8")
        request_id = self._request_id()
        packet = struct.pack(">cHIH", b"N", self.N_MSG_LOAD_PARK, request_id, len(data)) + data + b"L"
        self._request_results.pop(request_id, None)
        self._send(packet)
        return request_id

    def request_result(self, request_id: int):
        """Return None, ('ok', '') or ('error', text) for a request id."""
        return self._request_results.get(int(request_id))

    def close_park(self) -> None:
        self._send(self._simple_message(self.N_MSG_CLOSE_PARK))

    def set_pause(self, paused: bool) -> None:
        self._send(self._message(self.N_MSG_SET_PAUSE, struct.pack(">?", bool(paused))))

    def reset_park(self, start_paused: bool = False) -> None:
        self._send(self._message(self.N_MSG_RESET_PARK, struct.pack(">?", bool(start_paused))))

    def recenter_vr(self) -> None:
        self._send(self._simple_message(self.N_MSG_RECENTER_VR))

    def response_is_fresh(self, max_age_s: float, now: Optional[float] = None) -> bool:
        if not self.is_connected or self.last_response_at <= 0.0:
            return False
        if now is None:
            now = time.monotonic()
        return now - self.last_response_at <= float(max_age_s)

    def _request_id(self) -> int:
        with self._request_id_lock:
            self._request_id_counter = (self._request_id_counter + 1) & 0xFFFFFFFF
            return self._request_id_counter

    def _simple_message(self, msg_id: int) -> bytes:
        return struct.pack(">cHIHc", b"N", msg_id, self._request_id(), 0, b"L")

    def _message(self, msg_id: int, data: bytes) -> bytes:
        return struct.pack(">cHIH", b"N", msg_id, self._request_id(), len(data)) + data + b"L"

    def _send(self, data: bytes) -> None:
        if not self.is_connected or self._sock is None:
            raise ConnectionError("NoLimits2 is not connected")
        if NL2_COMMS_DEBUG:
            self._log_packet("TX", data)
        try:
            with self._send_lock:
                self._sock.sendall(data)
        except OSError as exc:
            self.last_error = str(exc)
            self.is_connected = False
            self._close_socket()
            raise ConnectionError(self.last_error) from exc

    def _listener_loop(self, sock: socket.socket) -> None:
        try:
            while not self._stop_event.is_set() and self._sock is sock:
                first = self._recv_exact(sock, 1)
                if first is None:
                    continue
                if first != b"N":
                    log.warning("NL2 invalid message start byte: %r", first)
                    continue
                header = self._recv_exact(sock, 8)
                if header is None:
                    continue
                msg_id, request_id, size = struct.unpack(">HIH", header)
                data = self._recv_exact(sock, size) if size else b""
                if data is None:
                    continue
                end = self._recv_exact(sock, 1)
                if end != b"L":
                    log.warning("NL2 invalid message terminator: %r", end)
                    continue
                if NL2_COMMS_DEBUG:
                    self._log_fields("RX", msg_id, request_id, data)
                self._rx_queue.put((msg_id, request_id, data))
        except (ConnectionError, OSError) as exc:
            if not self._stop_event.is_set():
                self.last_error = str(exc)
                log.warning("NL2 listener stopped: %s", exc)
        finally:
            # A stale listener must never tear down a newer connection.
            if self._sock is sock:
                self.is_connected = False
                self._close_socket()

    def _recv_exact(self, sock: socket.socket, size: int) -> Optional[bytes]:
        if size == 0:
            return b""
        data = bytearray()
        while len(data) < size and not self._stop_event.is_set():
            try:
                chunk = sock.recv(size - len(data))
            except socket.timeout:
                continue
            if not chunk:
                raise ConnectionError("NoLimits2 closed the connection")
            data.extend(chunk)
        return bytes(data) if len(data) == size else None

    def _log_packet(self, direction: str, packet: bytes) -> None:
        """Log one fully framed outbound NL2 message without changing it."""
        try:
            if len(packet) < 10 or packet[0:1] != b"N" or packet[-1:] != b"L":
                log.debug("NL2 %s malformed packet len=%d raw=%r", direction, len(packet), packet)
                return
            msg_id, request_id, size = struct.unpack(">HIH", packet[1:9])
            payload = packet[9:-1]
            if len(payload) != size:
                log.debug(
                    "NL2 %s malformed packet id=%d req=%d declared=%d actual=%d",
                    direction, msg_id, request_id, size, len(payload),
                )
                return
            self._log_fields(direction, msg_id, request_id, payload)
        except Exception as exc:
            # Debug logging must never interfere with NL2 communications.
            log.debug("NL2 %s debug decode failed: %s", direction, exc)

    def _log_fields(self, direction: str, msg_id: int, request_id: int, data: bytes) -> None:
        if NL2_COMMS_DEBUG_SUPPRESS_DATA and msg_id in (
            self.N_MSG_GET_TELEMETRY,
            self.N_MSG_TELEMETRY,
            self.N_MSG_GET_STATION_STATE,
            self.N_MSG_STATION_STATE,
        ):
            return
        name = self._MSG_NAMES.get(msg_id, f"MSG_{msg_id}")
        summary = self._payload_summary(msg_id, data)
        suffix = f" {summary}" if summary else ""
        log.debug(
            "NL2 %s id=%d %-17s req=%d len=%d%s",
            direction, msg_id, name, request_id, len(data), suffix,
        )

    def _payload_summary(self, msg_id: int, data: bytes) -> str:
        """Return a short human-readable payload summary for protocol tracing."""
        try:
            if msg_id == self.N_MSG_ERROR:
                return f'error="{data.decode("utf-8", errors="replace")}"'
            if msg_id == self.N_MSG_VERSION and len(data) >= 4:
                return "version=" + ".".join(str(int(v)) for v in data[:4])
            if msg_id == self.N_MSG_INT_VALUE_PAIR and len(data) >= 8:
                first, second = struct.unpack(">ii", data[:8])
                return f"coaster={first} station={second}"
            if msg_id == self.N_MSG_STATION_STATE and len(data) >= 4:
                return f"status=0x{struct.unpack('>I', data[:4])[0]:08x}"
            if msg_id == self.N_MSG_SET_MANUAL_MODE and len(data) >= 9:
                coaster, car, enabled = struct.unpack(">ii?", data[:9])
                return f"coaster={coaster} car={car} manual={bool(enabled)}"
            if msg_id == self.N_MSG_SET_PAUSE and len(data) >= 1:
                return f"paused={bool(struct.unpack('>?', data[:1])[0])}"
            if msg_id == self.N_MSG_RESET_PARK and len(data) >= 1:
                return f"start_paused={bool(struct.unpack('>?', data[:1])[0])}"
            if msg_id in (self.N_MSG_DISPATCH, self.N_MSG_GET_STATION_STATE) and len(data) >= 8:
                first, second = struct.unpack(">ii", data[:8])
                labels = ("coaster", "station")
                return f"{labels[0]}={first} {labels[1]}={second}"
            if msg_id == self.N_MSG_TELEMETRY and len(data) == self.TELEMETRY_SIZE:
                values = struct.unpack(self.TELEMETRY_FORMAT, data)
                return f"state={values[0]} frame={values[1]} speed={values[8]:.3f}"
        except (struct.error, ValueError):
            pass
        if data and len(data) <= 16:
            return "data=" + data.hex(" ")
        return ""

    def _process_message(self, msg_id: int, request_id: int, data: bytes) -> None:
        now = time.monotonic()
        # Any correctly framed NL2 response proves that the API link is alive,
        # even if that response reports that play-mode data is unavailable.
        self.last_response_at = now

        if msg_id == self.N_MSG_VERSION and len(data) >= 4:
            values = tuple(int(v) for v in data[:4])
            self.version = ".".join(str(v) for v in values)
            self.last_version_response_at = now
            self.last_error = ""
        elif msg_id == self.N_MSG_TELEMETRY:
            if len(data) != self.TELEMETRY_SIZE:
                self.last_error = f"Invalid NL2 telemetry size {len(data)}"
                return
            values = struct.unpack(self.TELEMETRY_FORMAT, data)
            self.latest_telemetry = NL2Telemetry(*values, received_at=now)
            self.last_error = ""
        elif msg_id == self.N_MSG_INT_VALUE_PAIR and len(data) >= 8:
            if self._nearest_station_requests.pop(request_id, None) is None:
                # A delayed or unsolicited nearest-station response must not
                # replace the station selected for the current ridden train.
                return
            self.current_coaster_index, self.current_station_index = struct.unpack(">ii", data[:8])
            self.current_station_received_at = now
            # Crucially, do not invalidate outstanding station-state polls or
            # their cache: the departure and arrival stations are independent.
            self.last_error = ""
        elif msg_id == self.N_MSG_STATION_STATE and len(data) >= 4:
            station_identity = self._station_requests.pop(request_id, None)
            if station_identity is None:
                # Never let an unsolicited or old response be used to dispatch.
                return
            self.station_status = struct.unpack(">I", data[:4])[0]
            self.station_status_station = station_identity
            self.station_status_received_at = now
            self._station_status_by_identity[station_identity] = (self.station_status, now)
            self.last_error = ""
        elif msg_id == self.N_MSG_ERROR:
            self.last_error = data.decode("utf-8", errors="replace")
            self._request_results[request_id] = ("error", self.last_error)
            # "Not in play mode" is an expected reply while a park is loading
            # or resetting. Keep it available to state logic but do not emit
            # routine console noise when protocol tracing is disabled.
            if NL2_COMMS_DEBUG:
                log.debug("NL2 error response to request %d: %s", request_id, self.last_error)
        elif msg_id == self.N_MSG_OK:
            self._request_results[request_id] = ("ok", "")
            self.last_error = ""

    def _clear_rx_queue(self) -> None:
        while True:
            try:
                self._rx_queue.get_nowait()
            except queue.Empty:
                return

    def _close_socket(self) -> None:
        sock, self._sock = self._sock, None
        if sock is None:
            return
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass
