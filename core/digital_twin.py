# digital_twin.py ver 2
"""Best-effort UDP snapshots for an external POC digital twin."""
from __future__ import annotations

import ipaddress
import json
import logging
import math
import socket
import time
from typing import Callable, Sequence


log = logging.getLogger(__name__)

PROTOCOL_NAME = "POC_TWIN"
PROTOCOL_VERSION = 1
MUSCLE_COUNT = 6
MAX_DATAGRAM_BYTES = 1400


class DigitalTwinSender:
    """Send complete, versioned snapshots without affecting POC control."""

    def __init__(
        self,
        address: str = "-",
        port: int = 10020,
        *,
        socket_factory: Callable = socket.socket,
        time_func: Callable[[], float] = time.time,
    ):
        self.address = str(address).strip()
        self.port = 10020
        self._time = time_func
        self._socket = None
        self._sequence = 0
        self.last_error = ""

        if self.address == "-":
            return

        try:
            self.port = int(port)
            parsed_address = ipaddress.ip_address(self.address)
            if parsed_address.version != 4:
                raise ValueError("only IPv4 addresses are supported")
            if not 1 <= self.port <= 65535:
                raise ValueError("port must be between 1 and 65535")
            self._socket = socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
            self._socket.setblocking(False)
            log.info("Digital twin UDP output at: %s:%d", self.address, self.port)
        except (OSError, TypeError, ValueError) as exc:
            sock = self._socket
            self._socket = None
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
            self._record_error(f"invalid digital twin destination: {exc}")

    @property
    def enabled(self) -> bool:
        return self._socket is not None

    def close(self) -> None:
        sock = self._socket
        self._socket = None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                log.exception("Unable to close digital twin UDP socket")

    def send_snapshot(
        self,
        *,
        platform: str,
        platform_state: str,
        simulator_state: str,
        telemetry_valid: bool,
        transform_applied: bool,
        dtop_load_kg_per_muscle: float,
        transform: Sequence[float],
        muscle_length_mm: Sequence[float],
        contraction_pct: Sequence[float],
        command_pressure_mbar: Sequence[float],
        piston_disengaged: bool | None,
        parking_state: str = "UNKNOWN",
        applied_transform_real: Sequence[float] | None = None,
    ) -> bool:
        if not self.enabled:
            return False

        try:
            packet = {
                "protocol": PROTOCOL_NAME,
                "version": PROTOCOL_VERSION,
                "sequence": self._sequence,
                "timestamp_ms": int(self._time() * 1000.0),
                "platform": str(platform),
                "platform_state": str(platform_state),
                "parking_state": str(parking_state),
                "applied_transform_real": (None if applied_transform_real is None
                    else self._six_numbers(applied_transform_real, "applied_transform_real")),
                "simulator_state": str(simulator_state),
                "telemetry_valid": bool(telemetry_valid),
                "transform_applied": bool(transform_applied),
                "dtop_load_kg_per_muscle": self._finite_number(
                    dtop_load_kg_per_muscle, "dtop_load_kg_per_muscle"
                ),
                "transform": self._six_numbers(transform, "transform"),
                "muscle_length_mm": self._six_numbers(
                    muscle_length_mm, "muscle_length_mm"
                ),
                "contraction_pct": self._six_numbers(
                    contraction_pct, "contraction_pct"
                ),
                "command_pressure_mbar": self._six_numbers(
                    command_pressure_mbar, "command_pressure_mbar"
                ),
                "piston_disengaged": (
                    None if piston_disengaged is None else bool(piston_disengaged)
                ),
            }
            encoded = json.dumps(
                packet,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
            if len(encoded) > MAX_DATAGRAM_BYTES:
                raise ValueError(
                    f"digital twin datagram is {len(encoded)} bytes; limit is {MAX_DATAGRAM_BYTES}"
                )
            self._socket.sendto(encoded, (self.address, self.port))
            self._sequence = (self._sequence + 1) & 0xFFFFFFFF
            if self.last_error:
                log.info("Digital twin UDP output recovered")
                self.last_error = ""
            return True
        except (OSError, TypeError, ValueError) as exc:
            self._record_error(str(exc))
            return False

    @staticmethod
    def _finite_number(value, field_name: str) -> float:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"{field_name} contains a non-finite value")
        return number

    @classmethod
    def _six_numbers(cls, values: Sequence[float], field_name: str) -> list[float]:
        if len(values) != MUSCLE_COUNT:
            raise ValueError(f"{field_name} must contain exactly {MUSCLE_COUNT} values")
        return [cls._finite_number(value, field_name) for value in values]

    def _record_error(self, message: str) -> None:
        message = str(message)
        if message != self.last_error:
            log.warning("Digital twin UDP output disabled/failed: %s", message)
        self.last_error = message
