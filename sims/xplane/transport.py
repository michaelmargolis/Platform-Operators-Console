#transport.py  ver 1
from __future__ import annotations

import socket
from typing import List


class XplaneTransport:
    """Small UDP transport matching the current SOP/XPPython telemetry plugin.

    The telemetry socket listens on TELEMETRY_EVT_PORT and sends commands to
    TELEMETRY_CMD_PORT.  The heartbeat socket follows SOP's convention of
    sending to HEARTBEAT_PORT and listening on HEARTBEAT_PORT + 1.
    """

    def __init__(
        self,
        address: str,
        telemetry_evt_port: int,
        telemetry_cmd_port: int,
        heartbeat_port: int,
    ):
        self.address = address
        self.telemetry_evt_port = int(telemetry_evt_port)
        self.telemetry_cmd_port = int(telemetry_cmd_port)
        self.heartbeat_port = int(heartbeat_port)
        self.telemetry_sock: socket.socket | None = None
        self.heartbeat_sock: socket.socket | None = None
        self.last_error = ""

    @property
    def is_open(self) -> bool:
        return self.telemetry_sock is not None

    def open(self) -> None:
        if self.is_open:
            return
        try:
            telemetry_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            telemetry_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            telemetry_sock.bind(("", self.telemetry_evt_port))
            telemetry_sock.setblocking(False)

            heartbeat_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            heartbeat_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            heartbeat_sock.bind(("", self.heartbeat_port + 1))
            heartbeat_sock.setblocking(False)

            self.telemetry_sock = telemetry_sock
            self.heartbeat_sock = heartbeat_sock
            self.last_error = ""
        except Exception as exc:
            self.last_error = str(exc)
            self.close()
            raise

    def close(self) -> None:
        for sock in (self.telemetry_sock, self.heartbeat_sock):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        self.telemetry_sock = None
        self.heartbeat_sock = None

    def send_command(self, message: str) -> None:
        if self.telemetry_sock is None:
            raise RuntimeError("X-Plane telemetry transport is not open")
        self.telemetry_sock.sendto(
            str(message).encode("utf-8"),
            (self.address, self.telemetry_cmd_port),
        )

    def send_heartbeat_ping(self) -> None:
        if self.heartbeat_sock is None:
            raise RuntimeError("X-Plane heartbeat transport is not open")
        self.heartbeat_sock.sendto(b"ping", (self.address, self.heartbeat_port))

    def drain_telemetry(self) -> List[str]:
        return self._drain(self.telemetry_sock)

    def drain_heartbeat(self) -> List[str]:
        return self._drain(self.heartbeat_sock)

    @staticmethod
    def _drain(sock: socket.socket | None) -> List[str]:
        if sock is None:
            return []
        result: List[str] = []
        while True:
            try:
                payload, _addr = sock.recvfrom(4096)
            except BlockingIOError:
                break
            except OSError:
                break
            try:
                result.append(payload.decode("utf-8").rstrip())
            except UnicodeDecodeError:
                continue
        return result
