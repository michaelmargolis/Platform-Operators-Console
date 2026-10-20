#fan_serial.py  ver 1
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable

try:
    import serial
    from serial.tools import list_ports
except ImportError:  # Keep SHS available so the GUI can report the problem.
    serial = None
    list_ports = None


log = logging.getLogger("sim_host_service.fan")


def save_opened_port(config_path: Path, port: str) -> None:
    """Update only fan.port while preserving the rest of the INI text."""
    path = Path(config_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    output = []
    in_fan = False
    fan_found = False
    port_written = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_fan and not port_written:
                output.append(f"port = {port}")
                port_written = True
            in_fan = stripped.lower() == "[fan]"
            fan_found = fan_found or in_fan
        if in_fan and stripped.lower().startswith("port") and "=" in stripped:
            output.append(f"port = {port}")
            port_written = True
        else:
            output.append(line)

    if in_fan and not port_written:
        output.append(f"port = {port}")
    elif not fan_found:
        if output and output[-1]:
            output.append("")
        output.extend(("[fan]", f"port = {port}"))

    path.write_text("\n".join(output) + "\n", encoding="utf-8")


class FanSerial:
    """Thread-safe serial transport for the USB fan controller."""

    def __init__(
        self,
        config_path: Path,
        *,
        enabled: bool = True,
        port: str = "",
        baudrate: int = 115200,
        serial_factory: Callable | None = None,
    ):
        self.config_path = Path(config_path)
        self.enabled = bool(enabled)
        self.configured_port = str(port).strip()
        self.baudrate = int(baudrate)
        self._serial_factory = serial_factory
        self._serial = None
        self._lock = threading.Lock()
        self.last_command = ""
        self.last_response = ""
        self.last_error = ""

        if self.enabled and self.configured_port:
            self.open_port(self.configured_port)

    @staticmethod
    def available_ports() -> list[str]:
        if list_ports is None:
            return []
        return [str(item.device) for item in list_ports.comports()]

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._serial is not None

    def open_port(self, port: str) -> bool:
        port = str(port).strip()
        if not port:
            with self._lock:
                self.last_error = "No fan COM port selected"
            return False
        factory = self._serial_factory
        if factory is None:
            if serial is None:
                with self._lock:
                    self.last_error = "pyserial is not installed"
                return False
            factory = serial.Serial

        with self._lock:
            self._close_locked(send_zero=True)
            try:
                self._serial = factory(
                    port=port,
                    baudrate=self.baudrate,
                    timeout=0,
                    write_timeout=0.25,
                )
                self._write_locked(0)
                self.configured_port = port
                try:
                    save_opened_port(self.config_path, port)
                    self.last_error = ""
                except OSError as exc:
                    self.last_error = f"COM port opened but INI save failed: {exc}"
                    log.error(self.last_error)
                log.info("Fan controller connected at %s", port)
                return True
            except Exception as exc:
                self.last_error = str(exc)
                log.error("Unable to open fan controller at %s: %s", port, exc)
                self._close_locked(send_zero=False)
                return False

    def service(self) -> None:
        """Collect any controller status text without blocking SHS."""
        with self._lock:
            stream = self._serial
            if stream is None:
                return
            try:
                while int(getattr(stream, "in_waiting", 0) or 0) > 0:
                    line = stream.readline().decode("utf-8", errors="replace").strip()
                    if line:
                        self.last_response = line
            except Exception as exc:
                self.last_error = str(exc)
                log.error("Fan serial read failed: %s", exc)
                self._close_locked(send_zero=False)

    def set_power(self, power: int) -> bool:
        power = max(0, min(100, int(power)))
        with self._lock:
            if self._serial is None:
                return False
            try:
                self._write_locked(power)
                self.last_error = ""
                return True
            except Exception as exc:
                self.last_error = str(exc)
                log.error("Fan serial write failed: %s", exc)
                self._close_locked(send_zero=False)
                return False

    def close(self) -> None:
        with self._lock:
            self._close_locked(send_zero=True)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "connected": self._serial is not None,
                "port": self.configured_port,
                "last_command": self.last_command,
                "last_response": self.last_response,
                "last_error": self.last_error,
            }

    def _write_locked(self, power: int) -> None:
        command = f"power,{power}\n"
        self._serial.write(command.encode("ascii"))
        self.last_command = command.strip()

    def _close_locked(self, *, send_zero: bool) -> None:
        stream = self._serial
        self._serial = None
        if stream is None:
            return
        if send_zero:
            try:
                stream.write(b"power,0\n")
                self.last_command = "power,0"
            except Exception:
                pass
        try:
            stream.close()
        except Exception:
            pass
