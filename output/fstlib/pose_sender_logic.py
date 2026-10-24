# pose_sender_logic.py ver 2
"""Non-GUI pose-to-pressure logic and EasyIP pressure-read validation.

This module intentionally has no socket or GUI dependencies. In particular,
constructing PoseMotion does not transmit any packets or start a Festo thread.
"""
from __future__ import annotations

import ipaddress
import math
from pathlib import Path

from config.platforms import PLATFORMS
from core.platform_controller import SopPlatformBackend
from output.fstlib import easyip


DOF_NAMES = ("Surge", "Sway", "Heave", "Roll", "Pitch", "Yaw")
# Operator-facing conventions follow ROS axes from the rider perspective:
# +X forward, +Y left, +Z up, +roll right side down, +pitch nose down, +yaw CCW.
# POC internal heave uses the opposite sign, so convert at the UI boundary.
USER_TO_INTERNAL_SIGN = (1.0, 1.0, -1.0, 1.0, 1.0, 1.0)


def user_to_internal_normalized(values):
    values = tuple(float(v) for v in values)
    if len(values) != 6:
        raise ValueError("Expected six normalized DOF values")
    return tuple(v * s for v, s in zip(values, USER_TO_INTERNAL_SIGN))


def internal_to_user_real(values):
    values = tuple(float(v) for v in values)
    if len(values) != 6:
        raise ValueError("Expected six real DOF values")
    return tuple(v * s for v, s in zip(values, USER_TO_INTERNAL_SIGN))
PRESSURE_CHANNELS = 6
MAX_PRESSURE_MBAR = 6000
FRAME_PERIOD_S = 0.05
PRESSURE_SLEW_MBAR_PER_S = 6000.0
POLL_INTERVAL_S = 0.2
READ_TIMEOUT_S = 1.0
FRESH_PRESSURE_S = 2.0
DEFAULT_TARGET_IP = "192.168.0.10"


class MemoryFesto:
    """Capture the exact POC pressure words without opening another UDP socket."""

    def __init__(self, ip=DEFAULT_TARGET_IP):
        self.ip = ip
        self.last_words = None
        self.generation = 0

    def send_pressures(self, words):
        values = tuple(int(round(v)) for v in words)
        if len(values) not in (6, 7):
            raise ValueError("Expected six pressure values and optional piston word")
        if any(not 0 <= v <= MAX_PRESSURE_MBAR for v in values[:6]):
            raise ValueError("Requested pressure outside 0..6000 mbar")
        if len(values) == 7 and values[6] not in (0, 1):
            raise ValueError("Invalid prop control flag")
        self.last_words = values
        self.generation += 1

    def get_ip_address(self):
        return self.ip


class PoseMotion:
    """Reuse POC workspace limiting, D-to-P mapping and park transition steps."""

    def __init__(self, selected_platform: str, project_root: Path):
        if selected_platform not in PLATFORMS:
            raise ValueError(f"Unknown platform {selected_platform!r}")
        self.project_root = Path(project_root).resolve()
        self.platform_key = selected_platform
        self.platform_info = PLATFORMS[selected_platform]
        self.capture = MemoryFesto()
        # D-to-P CSV paths in the POC configs are relative to the project root.
        # The caller sets cwd only for this initialization, then restores it.
        import os
        old_cwd = Path.cwd()
        try:
            os.chdir(self.project_root)
            self.backend = SopPlatformBackend(
                self.platform_info["sop_config_module"],
                festo_ip=DEFAULT_TARGET_IP,
                festo_interface=self.capture,
            )
        finally:
            os.chdir(old_cwd)
        self.mode = "parked"  # Starting parked is an operator-confirmed assumption.
        self.normalized = (0.0,) * 6
        self._last_applied = None

    @property
    def has_piston(self):
        return self.backend.has_piston

    def set_normalized(self, values):
        values = tuple(float(v) for v in values)
        if len(values) != 6 or any(not math.isfinite(v) or abs(v) > 1.0 for v in values):
            raise ValueError("Six finite normalized transforms between -1 and +1 required")
        self.normalized = values

    def preview(self):
        """Return requested real pose and limited real pose, without sending."""
        real = self.backend.k.norm_to_real(self.normalized)
        limited, _ = self.backend._limit_workspace(real)
        return tuple(map(float, real)), tuple(map(float, limited))

    def begin_unpark(self):
        if self.mode != "parked":
            raise RuntimeError("Unpark requires parked state")
        self.set_normalized([0.0] * 6)
        self.backend.begin_activate(self.normalized)
        self.mode = "unparking"
        self._last_applied = None

    def begin_park(self):
        if self.mode != "active":
            raise RuntimeError("Park requires active state")
        self.backend.begin_deactivate()
        self.mode = "parking"

    def tick(self):
        """Run one 50 ms POC step; writes only to MemoryFesto."""
        if self.mode in ("unparking", "parking"):
            if self.backend.service_transition():
                self.mode = "active" if self.mode == "unparking" else "parked"
                self._last_applied = None
        elif self.mode == "active":
            if self.normalized != self._last_applied:
                self.backend.move(self.normalized)
                self._last_applied = self.normalized
        return self.capture.last_words


class PressureMonitor:
    """Validate matching replies to EasyIP flagword read requests at offset 10."""

    def __init__(self, target_ip=DEFAULT_TARGET_IP):
        self.set_target(target_ip)

    def set_target(self, ip):
        parsed = ipaddress.ip_address(ip)
        if parsed.version != 4:
            raise ValueError("Target must be IPv4")
        self.target_ip = str(parsed)
        self.counter = 0
        self.pending = None
        self.last_poll_at = -1.0e30
        self.last_rx_at = None
        self.actual = None
        self.last_error = "Waiting for pressure response"

    def make_request(self, now):
        if self.pending is not None:
            if now - self.pending[1] < READ_TIMEOUT_S:
                return None
            self.pending = None
            self.last_error = "Pressure-read timeout"
        if now - self.last_poll_at < POLL_INTERVAL_S:
            return None
        self.counter = (self.counter + 1) & 0xFFFF
        self.pending = (self.counter, now)
        self.last_poll_at = now
        return easyip.Factory.req_flagword(self.counter, 6, 10).pack()

    def decode_response(self, data, host, now):
        if host != self.target_ip or self.pending is None:
            return False
        try:
            packet = easyip.Packet(data)
            if (
                packet.flags != easyip.Flags.RESPONSE
                or packet.counter != self.pending[0]
                or packet.error != 0
                or packet.senddata_type != easyip.Operands.FLAG_WORD
                or packet.senddata_size != PRESSURE_CHANNELS
            ):
                return False
            values = tuple(int(v) for v in packet.decode_payload(easyip.Packet.DIRECTION_SEND))
            if len(values) != 6 or any(v > MAX_PRESSURE_MBAR for v in values):
                self.last_error = "Invalid pressure values"
                return False
        except Exception:
            self.last_error = "Malformed pressure response"
            return False
        self.actual = values
        self.last_rx_at = now
        self.pending = None
        self.last_error = None
        return True

    def fresh(self, now):
        return self.last_rx_at is not None and now - self.last_rx_at <= FRESH_PRESSURE_S

    def make_output(self, pressure_words):
        if len(pressure_words) not in (6, 7):
            raise ValueError("Invalid output word count")
        self.counter = (self.counter + 1) & 0xFFFF
        return easyip.Factory.send_flagword(self.counter, list(pressure_words)).pack()


def slew_pressures(current, target):
    """Maximum 300 mbar per 50 ms step; piston word passes through unchanged."""
    if len(target) not in (6, 7) or len(current) != 6:
        raise ValueError("Incorrect pressure array sizes")
    step = PRESSURE_SLEW_MBAR_PER_S * FRAME_PERIOD_S
    result = []
    for a, b in zip(current, target[:6]):
        x = float(a)
        y = float(b)
        result.append(int(round(x + max(-step, min(step, y - x)))))
    if len(target) == 7:
        result.append(int(target[6]))
    return tuple(result)
