#client.py  ver 6
from __future__ import annotations

import logging
import socket
import time
from dataclasses import dataclass
from typing import Callable, Optional

from sim_host_service.protocol import (
    PROTOCOL_VERSION,
    TYPE_ACK,
    TYPE_COMMAND,
    TYPE_FAN_POWER,
    TYPE_PING,
    TYPE_PONG,
    TYPE_SELECT_SIM,
    TYPE_STARTUP,
    decode_message,
    encode_message,
)

log = logging.getLogger(__name__)

SHS_DEBUG = False
SHS_DEBUG_HEARTBEAT = False


@dataclass
class PendingRequest:
    message: dict
    sent_at: float
    retries: int = 0


class SimHostClient:
    """Small non-blocking UDP client for the Windows Sim Host Service.

    Heartbeats are independent of simulator telemetry.  The configured startup
    simulator is not sent until at least one valid pong has been received.  If
    the service disappears and later returns, the startup request is sent again
    so a restarted Windows host can recover its configured simulator.
    """

    def __init__(
        self,
        address: str,
        port: int,
        *,
        heartbeat_interval_s: float = 1.0,
        heartbeat_timeout_s: float = 3.0,
        command_retry_s: float = 0.5,
        command_retries: int = 3,
        time_func: Callable[[], float] = time.monotonic,
        socket_factory: Callable[..., socket.socket] = socket.socket,
    ):
        self.address = str(address)
        self.port = int(port)
        self.heartbeat_interval_s = float(heartbeat_interval_s)
        self.heartbeat_timeout_s = float(heartbeat_timeout_s)
        self.command_retry_s = float(command_retry_s)
        self.command_retries = int(command_retries)
        self._time = time_func
        self._socket_factory = socket_factory

        self._socket: Optional[socket.socket] = None
        # Start each POC process in a fresh transaction-ID range. SHS caches
        # recent requests by source endpoint + request ID so restarting at 1
        # could collide if Windows reuses the same UDP source port.
        self._next_id = (time.time_ns() & 0x7FFFFFFF) or 1
        self._last_ping_time = -1e9
        self._last_pong_time: Optional[float] = None
        self._connected = False
        self._startup_sim: Optional[str] = None
        self._startup_sent_for: Optional[str] = None
        self._pending: dict[int, PendingRequest] = {}

        self.server_version = ""
        self.active_sim = ""
        self.last_error = ""
        self.last_ack = ""

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def last_pong_age(self) -> Optional[float]:
        if self._last_pong_time is None:
            return None
        return max(0.0, self._time() - self._last_pong_time)

    def set_startup_sim(self, sim_key: str) -> None:
        sim_key = str(sim_key)
        if sim_key != self._startup_sim:
            self._startup_sim = sim_key
            self._startup_sent_for = None

    def start(self) -> None:
        if self._socket is not None:
            return
        sock = self._socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)
        sock.bind(("0.0.0.0", 0))
        self._socket = sock
        self._last_ping_time = -1e9
        self.last_error = ""
        if SHS_DEBUG:
            local = sock.getsockname() if hasattr(sock, "getsockname") else "<unknown>"
            log.debug("SHS client started: target=%s:%d local=%s", self.address, self.port, local)

    def stop(self) -> None:
        sock = self._socket
        self._socket = None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        self._connected = False
        self._last_pong_time = None
        self._startup_sent_for = None
        self._pending.clear()

    def service(self) -> None:
        if self._socket is None:
            return

        now = self._time()
        if now - self._last_ping_time >= self.heartbeat_interval_s:
            ping_id = self._allocate_id()
            if SHS_DEBUG and SHS_DEBUG_HEARTBEAT:
                log.debug("SHS TX ping id=%d -> %s:%d", ping_id, self.address, self.port)
            self._send_untracked({"type": TYPE_PING, "id": ping_id})
            self._last_ping_time = now

        self._receive_all(now)

        if (
            self._connected
            and self._last_pong_time is not None
            and now - self._last_pong_time > self.heartbeat_timeout_s
        ):
            log.warning("Sim Host Service heartbeat timed out (age=%.3fs, limit=%.3fs)", now - self._last_pong_time, self.heartbeat_timeout_s)
            self._connected = False
            self._startup_sent_for = None

        # The startup request is deliberately gated by a received heartbeat.
        if self._connected and self._startup_sim and self._startup_sent_for != self._startup_sim:
            request_id = self._send_request(TYPE_STARTUP, sim=self._startup_sim)
            self._startup_sent_for = self._startup_sim
            log.info("SHS startup requested for %s (id=%d)", self._startup_sim, request_id)

        self._retry_pending(now)

    def select_sim(self, sim_key: str) -> bool:
        """Request a future runtime simulator transition on the Windows host."""
        if not self._connected:
            self.last_error = "Sim Host Service is not connected"
            return False
        sim_key = str(sim_key)
        self._send_request(TYPE_SELECT_SIM, sim=sim_key)
        # This selection becomes the recovery target if SHS or the network is
        # subsequently restarted. Mark it as already requested on the current
        # connection so service() does not immediately send a duplicate startup.
        self._startup_sim = sim_key
        self._startup_sent_for = sim_key
        return True

    def command(self, target: str, command: str, **fields) -> bool:
        if not self._connected:
            self.last_error = "Sim Host Service is not connected"
            return False
        self._send_request(TYPE_COMMAND, target=str(target), command=str(command), **fields)
        return True

    def send_fan_power(self, speed: float, power: int) -> bool:
        """Send replaceable fan state without command retries or ACK traffic."""
        if not self._connected or self._socket is None:
            return False
        self._send_untracked({
            "type": TYPE_FAN_POWER,
            "id": self._allocate_id(),
            "protocol": PROTOCOL_VERSION,
            "speed": max(0.0, float(speed)),
            "power": max(0, min(100, int(power))),
        })
        return True

    def _allocate_id(self) -> int:
        request_id = self._next_id
        self._next_id += 1
        if self._next_id > 0x7FFFFFFF:
            self._next_id = 1
        return request_id

    def _send_untracked(self, message: dict) -> None:
        if self._socket is None:
            return
        try:
            self._socket.sendto(encode_message(message), (self.address, self.port))
            if SHS_DEBUG and message.get("type") != TYPE_PING:
                log.debug("SHS TX %s", message)
        except OSError as exc:
            self.last_error = str(exc)
            if SHS_DEBUG:
                log.debug("SHS TX error: %s", exc)

    def _send_request(self, message_type: str, **fields) -> int:
        request_id = self._allocate_id()
        message = {"type": message_type, "id": request_id, "protocol": PROTOCOL_VERSION, **fields}
        now = self._time()
        self._send_untracked(message)
        self._pending[request_id] = PendingRequest(message=message, sent_at=now)
        return request_id

    def _receive_all(self, now: float) -> None:
        if self._socket is None:
            return
        while True:
            try:
                data, _addr = self._socket.recvfrom(4096)
            except BlockingIOError:
                return
            except OSError as exc:
                self.last_error = str(exc)
                return

            try:
                message = decode_message(data)
            except (UnicodeDecodeError, ValueError) as exc:
                self.last_error = f"Bad SHS response: {exc}"
                continue

            message_type = message.get("type")
            if SHS_DEBUG and (message_type != TYPE_PONG or SHS_DEBUG_HEARTBEAT):
                log.debug("SHS RX from %s: %s", _addr, message)
            if message_type == TYPE_PONG:
                if int(message.get("protocol", -1)) != PROTOCOL_VERSION:
                    self.last_error = f"Unsupported SHS protocol: {message.get('protocol')}"
                    continue
                was_connected = self._connected
                self._connected = True
                self._last_pong_time = now
                self.server_version = str(message.get("server_version", ""))
                self.active_sim = str(message.get("active_sim", "") or "")
                if not was_connected:
                    log.info("Sim Host Service connected at %s:%d", self.address, self.port)
                elif SHS_DEBUG and SHS_DEBUG_HEARTBEAT:
                    log.debug("SHS heartbeat ok: active_sim=%s server=%s", self.active_sim or "<none>", self.server_version or "<unknown>")
                continue

            if message_type == TYPE_ACK:
                try:
                    request_id = int(message.get("id"))
                except (TypeError, ValueError):
                    continue
                self._pending.pop(request_id, None)
                ok = bool(message.get("ok", False))
                detail = str(message.get("detail", message.get("error", "")))
                self.last_ack = f"{request_id}: {'ok' if ok else 'failed'} {detail}".strip()
                if SHS_DEBUG:
                    log.debug("SHS ACK id=%d ok=%s detail=%r", request_id, ok, detail)
                if not ok:
                    self.last_error = detail or "SHS command failed"
                    log.warning("SHS request %d failed: %s", request_id, self.last_error)
                active_sim = message.get("active_sim")
                if active_sim is not None:
                    self.active_sim = str(active_sim or "")

    def _retry_pending(self, now: float) -> None:
        for request_id, pending in list(self._pending.items()):
            if now - pending.sent_at < self.command_retry_s:
                continue
            if pending.retries >= self.command_retries:
                self.last_error = f"No acknowledgement for SHS request {request_id}"
                self._pending.pop(request_id, None)
                # Allow a startup request to be tried again after another pong.
                if pending.message.get("type") == TYPE_STARTUP:
                    self._startup_sent_for = None
                continue
            if SHS_DEBUG:
                log.debug("SHS retry id=%d attempt=%d message=%s", request_id, pending.retries + 1, pending.message)
            self._send_untracked(pending.message)
            pending.sent_at = now
            pending.retries += 1
