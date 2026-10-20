# serial_remote.py  ver 1
"""Serial remote-control adapter for POC.

This is based on the proven MdxMotionPlatformV3 SerialRemote behavior, updated
for Python 3/pyserial and the POC control-action boundary.

The receive thread performs only serial I/O and queues complete text messages.
``service()`` must be called from the POC/Qt thread; callbacks therefore run in
the same thread as the UI/core state machines.
"""
from __future__ import annotations

import logging
import threading
import time
from queue import Empty, Queue
from typing import Callable, Mapping

try:
    import serial
    import serial.tools.list_ports
except ImportError:  # Allow POC to start even when optional serial support is absent.
    serial = None

log = logging.getLogger(__name__)


class SerialRemote:
    """Read the legacy MDX serial remote and emit common POC actions."""

    AUTO_CONN_STR = "MdxRemote_V1"
    BAUD_RATE = 57600
    READ_TIMEOUT_S = 2.0
    SEARCH_RETRY_S = 1.0

    # Raw serial command -> common POC action name.
    COMMAND_ACTIONS = {
        "activate": "activate",
        "deactivate": "deactivate",
        "dispatch": "dispatch",
        "pause": "pause",
        "reset": "reset_vr",
    }

    def __init__(self, actions: Mapping[str, Callable], auto_start: bool = False):
        self.actions = dict(actions)
        self._rx_queue: Queue[str] = Queue()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._ser = None

        self.is_connected = False
        self.port = ""
        self.last_event = ""
        self.last_error = ""

        if auto_start:
            self.start()

    @property
    def available(self) -> bool:
        return serial is not None

    def start(self) -> bool:
        """Start serial auto-detection/receive in a background thread."""
        if not self.available:
            self.last_error = "pyserial is not installed"
            log.warning("Serial remote disabled: %s", self.last_error)
            return False
        if self._thread and self._thread.is_alive():
            return True

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._rx_thread, name="SerialRemote", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop_event.set()
        self._disconnect()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=0.5)
        self._thread = None

    def service(self) -> None:
        """Dispatch queued remote messages in the caller's thread."""
        while True:
            try:
                message = self._rx_queue.get_nowait()
            except Empty:
                return
            self._dispatch_message(message)

    def _dispatch_message(self, message: str) -> None:
        msg = str(message).strip()
        if not msg or self.AUTO_CONN_STR in msg:
            return

        self.last_event = msg

        if msg.startswith("intensity="):
            try:
                level = int(msg.split("=", 1)[1].strip())
            except (ValueError, IndexError):
                log.warning("Ignoring malformed serial remote intensity message: %r", msg)
                return
            self._invoke("intensity", level)
            return

        action_name = self.COMMAND_ACTIONS.get(msg)
        if action_name is None:
            log.warning("Ignoring unknown serial remote message: %r", msg)
            return
        self._invoke(action_name)

    def _invoke(self, action_name: str, *args) -> None:
        callback = self.actions.get(action_name)
        if callback is None:
            log.warning("No POC action registered for serial remote command %s", action_name)
            return
        try:
            callback(*args)
        except Exception:
            log.exception("Serial remote action %s failed", action_name)

    def _rx_thread(self) -> None:
        while not self._stop_event.is_set():
            if self._ser is None:
                if not self._search_and_connect():
                    self._stop_event.wait(self.SEARCH_RETRY_S)
                    continue

            try:
                raw = self._ser.readline()
                if raw:
                    text = raw.decode("ascii", errors="ignore").strip()
                    if text:
                        self._rx_queue.put(text)
            except Exception as exc:
                if self._stop_event.is_set():
                    break
                self.last_error = str(exc)
                log.warning("Serial remote error on %s: %s; reconnecting", self.port or "?", exc)
                self._disconnect()
                self._stop_event.wait(0.5)

    def _search_and_connect(self) -> bool:
        try:
            ports = sorted(serial.tools.list_ports.comports(), key=lambda p: p.device)
        except Exception as exc:
            self.last_error = str(exc)
            log.warning("Unable to enumerate serial ports: %s", exc)
            return False

        for port_info in ports:
            if self._stop_event.is_set():
                return False
            port_name = port_info.device
            if self._connect(port_name):
                return True
        return False

    def _connect(self, port_name: str) -> bool:
        """Probe one port using the legacy ``V`` identification request."""
        candidate = None
        try:
            candidate = serial.Serial(port_name, self.BAUD_RATE, timeout=self.READ_TIMEOUT_S)
            if not candidate.is_open:
                return False

            candidate.reset_input_buffer()
            candidate.reset_output_buffer()
            time.sleep(0.1)
            candidate.write(b"V\n")
            candidate.flush()

            # Preserve the proven remote's generous response interval.
            time.sleep(1.1)
            identified = False
            for _ in range(3):
                raw = candidate.readline()
                if not raw:
                    break
                text = raw.decode("ascii", errors="ignore").strip()
                if self._looks_like_remote(text):
                    identified = True
                    break

            if not identified:
                candidate.close()
                return False

            self._ser = candidate
            self.port = port_name
            self.is_connected = True
            self.last_error = ""
            log.info("Detected serial remote on %s", port_name)
            return True
        except Exception as exc:
            self.last_error = str(exc)
            if candidate is not None:
                try:
                    candidate.close()
                except Exception:
                    pass
            return False

    @classmethod
    def _looks_like_remote(cls, text: str) -> bool:
        if not text:
            return False
        if cls.AUTO_CONN_STR in text:
            return True
        if text.startswith("intensity="):
            return True
        return text in cls.COMMAND_ACTIONS

    def _disconnect(self) -> None:
        current = self._ser
        self._ser = None
        self.is_connected = False
        self.port = ""
        if current is not None:
            try:
                current.close()
            except Exception:
                pass
