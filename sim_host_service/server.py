#server.py  ver 18
from __future__ import annotations

import argparse
import configparser
import ctypes
from ctypes import wintypes
import logging
import os
import socket
import select
import subprocess
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from sim_host_service.protocol import (
    PROTOCOL_VERSION,
    SERVER_VERSION,
    TYPE_COMMAND,
    TYPE_FAN_POWER,
    TYPE_PING,
    TYPE_PONG,
    TYPE_SELECT_SIM,
    TYPE_STARTUP,
    decode_message,
    encode_message,
    make_ack,
)
from sim_host_service.fan_serial import FanSerial

log = logging.getLogger("sim_host_service")
WM_CLOSE = 0x0010
SMTO_ABORTIFHUNG = 0x0002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259
SW_MINIMIZE = 6
SW_RESTORE = 9
SW_MAXIMIZE = 3
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_SHOWWINDOW = 0x0040
HWND_TOP = 0

SHS_DEBUG = False
SHS_DEBUG_HEARTBEAT = False
SHS_SC_RELAY_DEBUG = False
SHS_SC_RELAY_DEBUG_DATA = False
SHS_SC_RELAY_DEBUG_UNKNOWN = True
SHS_SC_RELAY_FORWARD_UNKNOWN = True
SERVER_FILE_VERSION = 18


def project_manifest_version() -> str:
    """Return the file_versions.txt revision belonging to this SHS tree."""
    manifest = Path(__file__).resolve().parent.parent / "file_versions.txt"
    try:
        first_line = manifest.read_text(encoding="utf-8").splitlines()[0].strip()
        prefix = "#file_versions.txt  ver "
        if first_line.startswith(prefix):
            return first_line[len(prefix):].strip()
    except (OSError, IndexError):
        pass
    return "?"


def local_ipv4_addresses() -> list[str]:
    """Return locally assigned IPv4 addresses without invoking external tools."""
    addresses: set[str] = set()
    try:
        for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = item[4][0]
            if address and not address.startswith("127."):
                addresses.add(address)
    except OSError:
        pass
    return sorted(addresses)


def dashboard_status(status: dict) -> tuple[str, str]:
    """Return the public-facing SHS banner text and ttk style name."""
    if status.get("listen_error") or status.get("listen_state") == "Failed":
        return "SERVICE ERROR", "Shs.Error.TLabel"
    if status.get("poc_connected"):
        return "CONSOLE CONNECTED — READY", "Shs.Ready.TLabel"
    return "WAITING FOR CONSOLE CONNECTION", "Shs.Waiting.TLabel"


@dataclass(frozen=True)
class SimConfig:
    key: str
    window_class: str = ""
    window_title: str = ""
    start_batch: str = ""
    close_timeout_s: float = 10.0


class WindowsSimManager:
    """Best-effort simulator process/window management for the Windows host."""

    def __init__(
        self,
        sims: dict[str, SimConfig],
        sims_dir: Optional[Path] = None,
    ):
        self.sims = sims
        self.sims_dir = Path(sims_dir) if sims_dir is not None else Path(__file__).with_name("sims")
        self._selected_sim = ""

    def is_running(self, sim_key: str) -> bool:
        cfg = self._config(sim_key)
        return bool(self._windows_for_config(cfg))

    def active_sim(self) -> str:
        if self._selected_sim:
            return self._selected_sim
        for key in self.sims:
            if self.is_running(key):
                return key
        return ""

    def ensure_sim(self, sim_key: str) -> tuple[bool, str]:
        cfg = self._config(sim_key)
        if self.is_running(sim_key):
            self._selected_sim = sim_key
            return True, "already running"
        if not cfg.start_batch:
            return False, f"no start_batch configured for {sim_key}"
        batch_path = self.sims_dir / cfg.start_batch
        if not batch_path.is_file():
            return False, f"start batch not found: {batch_path}"
        try:
            subprocess.Popen(["cmd.exe", "/c", str(batch_path)])
            self._selected_sim = sim_key
            return True, f"start initiated: {cfg.start_batch}"
        except OSError as exc:
            return False, str(exc)

    def minimize_sim(self, sim_key: str) -> tuple[bool, str]:
        cfg = self._config(sim_key)
        if not cfg.window_class and not cfg.window_title:
            return False, f"no window_class or window_title configured for {sim_key}"
        windows = self._windows_for_config(cfg)
        if not windows:
            return True, "not running"
        if os.name != "nt" or not hasattr(ctypes, "windll"):
            return False, "window minimize is only available on Windows"
        user32 = ctypes.windll.user32
        for hwnd in windows:
            user32.ShowWindow(hwnd, SW_MINIMIZE)
        return True, f"minimized {len(windows)} window(s)"

    def activate_sim(self, sim_key: str) -> tuple[bool, str]:
        cfg = self._config(sim_key)
        if not cfg.window_class and not cfg.window_title:
            return False, f"no window_class or window_title configured for {sim_key}"
        windows = self._windows_for_config(cfg)
        if not windows:
            return False, "not running"
        if os.name != "nt" or not hasattr(ctypes, "windll"):
            return False, "window activation is only available on Windows"
        user32 = ctypes.windll.user32
        foreground_ok = True
        for hwnd in windows:
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, SW_RESTORE)
            user32.ShowWindow(hwnd, SW_MAXIMIZE)
            user32.SetWindowPos(hwnd, HWND_TOP, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
            user32.BringWindowToTop(hwnd)
            foreground_ok = bool(user32.SetForegroundWindow(hwnd)) and foreground_ok
        self._selected_sim = sim_key
        if foreground_ok:
            return True, f"maximized and foregrounded {len(windows)} window(s)"
        # Windows can reject SetForegroundWindow because of its foreground-lock
        # policy even after the window has been restored and raised. Treat that
        # as a best-effort warning rather than failing the simulator switch.
        return True, f"maximized and raised {len(windows)} window(s); foreground request was declined"

    def close_sim(self, sim_key: str) -> tuple[bool, str]:
        """Ask a simulator to exit cleanly, then wait for its process to stop."""
        cfg = self._config(sim_key)
        if not cfg.window_class and not cfg.window_title:
            return False, f"no window_class or window_title configured for {sim_key}"
        windows = self._windows_for_config(cfg)
        if not windows:
            return True, "not running"
        if os.name != "nt" or not hasattr(ctypes, "windll"):
            return False, "window close is only available on Windows"

        user32 = ctypes.windll.user32
        pids = {self._window_pid(hwnd) for hwnd in windows}
        pids.discard(0)
        for hwnd in windows:
            result = ctypes.c_void_p()
            sent = user32.SendMessageTimeoutW(
                hwnd, WM_CLOSE, 0, 0, SMTO_ABORTIFHUNG, 2000, ctypes.byref(result)
            )
            if not sent:
                return False, f"WM_CLOSE timed out for hwnd 0x{hwnd:08X}"

        deadline = time.monotonic() + max(0.1, float(cfg.close_timeout_s))
        while time.monotonic() < deadline:
            if not any(self._process_alive(pid) for pid in pids):
                if self._selected_sim == sim_key:
                    self._selected_sim = ""
                return True, f"closed cleanly ({len(pids)} process(es))"
            time.sleep(0.1)

        alive = sorted(pid for pid in pids if self._process_alive(pid))
        return False, f"close timeout after {cfg.close_timeout_s:g}s; process(es) still running: {alive}"

    def select_sim(self, sim_key: str) -> tuple[bool, str]:
        self._config(sim_key)
        details = []

        # A VR runtime may retain ownership while a simulator remains alive.
        # Close every other configured simulator and confirm its process has
        # exited before the replacement is started or brought forward.
        for other in self.sims:
            if other == sim_key:
                continue
            if self.is_running(other):
                close_ok, detail = self.close_sim(other)
                details.append(f"close {other}: {detail}")
                if not close_ok:
                    return False, "; ".join(details)

        if self.is_running(sim_key):
            target_ok, detail = self.activate_sim(sim_key)
        else:
            target_ok, detail = self.ensure_sim(sim_key)
        details.append(f"{sim_key}: {detail}")

        if target_ok:
            self._selected_sim = sim_key
        return target_ok, "; ".join(details)

    def _config(self, sim_key: str) -> SimConfig:
        try:
            return self.sims[sim_key]
        except KeyError as exc:
            raise ValueError(f"unknown simulator: {sim_key}") from exc

    def _windows_for_config(self, cfg: SimConfig) -> list[int]:
        return self._matching_windows(cfg.window_class, cfg.window_title)

    @staticmethod
    def _matching_windows(window_class: str = "", title_fragment: str = "") -> list[int]:
        """Match visible top-level windows by class first, then title fallback."""
        if os.name != "nt" or not hasattr(ctypes, "windll"):
            return []
        user32 = ctypes.windll.user32
        all_windows: list[tuple[int, str, str]] = []
        EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        @EnumProc
        def callback(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            class_buffer = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, class_buffer, len(class_buffer))
            length = user32.GetWindowTextLengthW(hwnd)
            title = ""
            if length > 0:
                title_buffer = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, title_buffer, len(title_buffer))
                title = title_buffer.value
            all_windows.append((int(hwnd), class_buffer.value, title))
            return True

        user32.EnumWindows(callback, 0)
        if window_class:
            wanted_class = window_class.casefold()
            by_class = [hwnd for hwnd, cls, _title in all_windows if cls.casefold() == wanted_class]
            if by_class:
                return by_class
        if title_fragment:
            wanted_title = title_fragment.casefold()
            return [hwnd for hwnd, _cls, title in all_windows if wanted_title in title.casefold()]
        return []

    @staticmethod
    def _window_pid(hwnd: int) -> int:
        user32 = ctypes.windll.user32
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value)

    @staticmethod
    def _process_alive(pid: int) -> bool:
        if not pid:
            return False
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return False
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return int(exit_code.value) == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)


class SimHostService:
    def __init__(
        self,
        manager: WindowsSimManager,
        *,
        bind_address: str = "0.0.0.0",
        port: int = 10040,
        recent_request_count: int = 128,
        spacecoaster_relay_enabled: bool = True,
        spacecoaster_relay_bind_address: str = "0.0.0.0",
        spacecoaster_relay_listen_port: int = 10009,
        spacecoaster_relay_target_port: int = 10009,
        fan_enabled: bool = True,
        fan_port: str = "",
        fan_baudrate: int = 115200,
        fan_command_timeout_s: float = 1.0,
        poc_timeout_s: float = 3.0,
        config_path: Optional[Path] = None,
        fan: Optional[FanSerial] = None,
        time_func=time.monotonic,
    ):
        self.manager = manager
        self.bind_address = bind_address
        self.port = int(port)
        self.recent_request_count = max(8, int(recent_request_count))
        self.spacecoaster_relay_enabled = bool(spacecoaster_relay_enabled)
        self.spacecoaster_relay_bind_address = str(spacecoaster_relay_bind_address)
        self.spacecoaster_relay_listen_port = int(spacecoaster_relay_listen_port)
        self.spacecoaster_relay_target_port = int(spacecoaster_relay_target_port)
        self.fan_command_timeout_s = max(0.1, float(fan_command_timeout_s))
        self.poc_timeout_s = max(0.1, float(poc_timeout_s))
        self._time = time_func
        self.fan = fan or FanSerial(
            config_path or Path(__file__).with_name("sim_host_service.ini"),
            enabled=fan_enabled,
            port=fan_port,
            baudrate=fan_baudrate,
        )
        self._recent: OrderedDict[tuple[str, int, int], bytes] = OrderedDict()
        self._poc_address: Optional[str] = None
        self._spacecoaster_unity_addr: Optional[tuple[str, int]] = None
        self._spacecoaster_last_state: Optional[bytes] = None
        self._sc_relay_counts: dict[str, int] = {}
        self._sc_unknown_counts: dict[tuple[int, bytes], int] = {}
        self._stop_event = threading.Event()
        self._control_sock: Optional[socket.socket] = None
        self._relay_sock: Optional[socket.socket] = None
        self.listen_state = "Starting"
        self.listen_error = ""
        self.last_datagram_at: Optional[float] = None
        self.last_datagram_source = ""
        self.last_poc_message_at: Optional[float] = None
        self.last_poc_message_type = ""
        self.last_sim_action = ""
        self.fan_speed = 0.0
        self.fan_power = 0
        self._last_fan_command_at: Optional[float] = None
        self.last_error = ""

    def serve_forever(self) -> None:
        self._stop_event.clear()
        control_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            control_sock.bind((self.bind_address, self.port))
        except OSError as exc:
            self.listen_state = "Failed"
            self.listen_error = str(exc)
            control_sock.close()
            raise
        self._disable_udp_connection_reset(control_sock, "SHS control")
        self.listen_state = "Listening"
        self.listen_error = ""
        relay_sock = self._open_spacecoaster_relay_socket()
        self._control_sock = control_sock
        self._relay_sock = relay_sock
        log.info("Sim Host Service %s listening on %s:%d", SERVER_VERSION, self.bind_address, self.port)
        try:
            while not self._stop_event.is_set():
                sockets = [control_sock]
                if relay_sock is not None:
                    sockets.append(relay_sock)
                # A finite timeout is intentional: on Windows, an infinite select()
                # can prevent Ctrl+C from being delivered while the server is idle.
                readable, _writable, _exceptional = select.select(sockets, [], [], 0.25)
                if self._stop_event.is_set():
                    break

                if control_sock in readable:
                    packet = self._recv_udp_datagram(control_sock, 4096, "SHS control")
                    if packet is None:
                        continue
                    data, addr = packet
                    if SHS_DEBUG:
                        try:
                            dbg_message = decode_message(data)
                            if dbg_message.get("type") != TYPE_PING or SHS_DEBUG_HEARTBEAT:
                                log.debug("SHS RX from %s: %s", addr, dbg_message)
                        except Exception:
                            log.debug("SHS RX undecodable from %s: %r", addr, data)
                    response = self.handle_datagram(data, addr)
                    if response is not None:
                        control_sock.sendto(response, addr)
                        if SHS_DEBUG:
                            try:
                                dbg_response = decode_message(response)
                                if dbg_response.get("type") != TYPE_PONG or SHS_DEBUG_HEARTBEAT:
                                    log.debug("SHS TX to %s: %s", addr, dbg_response)
                            except Exception:
                                pass

                if relay_sock is not None and relay_sock in readable:
                    packet = self._recv_udp_datagram(
                        relay_sock, 65535, "SpaceCoaster relay"
                    )
                    if packet is not None:
                        data, addr = packet
                        self.forward_spacecoaster_datagram(data, addr, relay_sock)
                self.fan.service()
                self._service_fan_timeout()
        except Exception as exc:
            self.listen_state = "Failed"
            self.listen_error = f"{type(exc).__name__}: {exc}"
            log.exception("Sim Host Service server thread failed")
            raise
        finally:
            if self.listen_state != "Failed":
                self.listen_state = "Stopped"
            self._control_sock = None
            self._relay_sock = None
            try:
                control_sock.close()
            except OSError:
                pass
            if relay_sock is not None:
                try:
                    relay_sock.close()
                except OSError:
                    pass
            self.fan.close()
            log.info("Sim Host Service stopped")

    @staticmethod
    def _disable_udp_connection_reset(sock: socket.socket, label: str) -> None:
        """Prevent Windows ICMP port-unreachable replies killing a UDP loop."""
        ioctl_code = getattr(socket, "SIO_UDP_CONNRESET", None)
        if os.name != "nt" or ioctl_code is None:
            return
        try:
            sock.ioctl(ioctl_code, False)
        except OSError as exc:
            # recvfrom() still has a defensive ConnectionResetError handler.
            log.warning("Unable to disable %s UDP connection resets: %s", label, exc)

    @staticmethod
    def _recv_udp_datagram(
        sock: socket.socket, max_bytes: int, label: str
    ) -> Optional[tuple[bytes, tuple[str, int]]]:
        """Receive one datagram, ignoring the recoverable Windows 10054 reset."""
        try:
            return sock.recvfrom(max_bytes)
        except ConnectionResetError as exc:
            log.info("Ignoring recoverable %s UDP reset: %s", label, exc)
            return None

    def stop(self) -> None:
        """Request a clean exit from serve_forever()."""
        self._stop_event.set()

    def _service_fan_timeout(self) -> None:
        last = self._last_fan_command_at
        if last is None or self.fan_power == 0:
            return
        if self._time() - last >= self.fan_command_timeout_s:
            self.fan_power = 0
            self.fan.set_power(0)
            self.last_error = "Console fan command timeout; fan forced to 0%"
            log.warning(self.last_error)

    def status_snapshot(self) -> dict:
        now = self._time()
        age = None if self.last_poc_message_at is None else max(0.0, now - self.last_poc_message_at)
        datagram_age = None if self.last_datagram_at is None else max(0.0, now - self.last_datagram_at)
        fan_status = self.fan.snapshot()
        active_sim = self.manager.active_sim()
        try:
            sim_running = bool(active_sim and self.manager.is_running(active_sim))
        except Exception:
            sim_running = False
        return {
            "poc_connected": age is not None and age <= self.poc_timeout_s,
            "poc_message_age": age,
            "poc_message_type": self.last_poc_message_type,
            "last_datagram_age": datagram_age,
            "last_datagram_source": self.last_datagram_source,
            "listen_state": self.listen_state,
            "listen_address": self.bind_address,
            "listen_port": self.port,
            "listen_error": self.listen_error,
            "local_ipv4": local_ipv4_addresses(),
            "poc_address": self._poc_address or "",
            "active_sim": active_sim,
            "sim_running": sim_running,
            "last_sim_action": self.last_sim_action,
            "spacecoaster_relay_enabled": self.spacecoaster_relay_enabled,
            "spacecoaster_unity_endpoint": (
                "" if self._spacecoaster_unity_addr is None
                else f"{self._spacecoaster_unity_addr[0]}:{self._spacecoaster_unity_addr[1]}"
            ),
            "fan_speed": self.fan_speed,
            "fan_power": self.fan_power,
            "fan_connected": fan_status["connected"],
            "fan_port": fan_status["port"],
            "fan_last_command": fan_status["last_command"],
            "fan_last_response": fan_status["last_response"],
            "last_error": fan_status["last_error"] or self.last_error,
        }

    def _open_spacecoaster_relay_socket(self) -> Optional[socket.socket]:
        if not self.spacecoaster_relay_enabled:
            log.info("SpaceCoaster UDP relay disabled")
            return None
        relay_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            relay_sock.bind((
                self.spacecoaster_relay_bind_address,
                self.spacecoaster_relay_listen_port,
            ))
        except OSError as exc:
            relay_sock.close()
            log.error(
                "SpaceCoaster UDP relay could not bind %s:%d: %s",
                self.spacecoaster_relay_bind_address,
                self.spacecoaster_relay_listen_port,
                exc,
            )
            return None
        self._disable_udp_connection_reset(relay_sock, "SpaceCoaster relay")
        log.info(
            "SpaceCoaster UDP relay listening on %s:%d",
            self.spacecoaster_relay_bind_address,
            self.spacecoaster_relay_listen_port,
        )
        return relay_sock

    def forward_spacecoaster_datagram(
        self,
        data: bytes,
        source: tuple[str, int],
        relay_sock: socket.socket,
    ) -> bool:
        """Forward a Unity SpaceCoaster datagram unchanged to the current POC host.

        The source endpoint is also the return address for semantic control
        commands. Unity uses an OS-assigned source port, so it must be learned
        from real telemetry rather than configured.
        """
        if source != self._spacecoaster_unity_addr:
            self._spacecoaster_unity_addr = source
            log.info("SpaceCoaster Unity UDP endpoint learned: %s:%d", source[0], source[1])
        poc_address = self._poc_address
        kind = self._spacecoaster_packet_kind(data)
        if kind == "state":
            self._cache_spacecoaster_state(data)
        is_data = kind in {"xyzrpy", "posrpy"}
        self._sc_relay_counts[kind] = self._sc_relay_counts.get(kind, 0) + 1
        if kind == "unknown":
            self._debug_unknown_spacecoaster_packet(data, source)
            if not SHS_SC_RELAY_FORWARD_UNKNOWN:
                return False
        if not poc_address:
            if SHS_SC_RELAY_DEBUG and (SHS_SC_RELAY_DEBUG_DATA or not is_data):
                log.debug("SHS SC relay RX from %s but no POC heartbeat destination is known", source)
            return False

        target = (poc_address, self.spacecoaster_relay_target_port)
        try:
            relay_sock.sendto(data, target)
        except OSError as exc:
            log.warning("SHS SC relay send to %s failed: %s", target, exc)
            return False

        if SHS_SC_RELAY_DEBUG and kind != "unknown" and (SHS_SC_RELAY_DEBUG_DATA or not is_data):
            preview = data.decode("utf-8", errors="replace").strip()
            if len(preview) > 160:
                preview = preview[:157] + "..."
            log.debug(
                "SHS SC relay %s -> %s kind=%s count=%d: %s",
                source, target, kind, self._sc_relay_counts[kind], preview,
            )
        return True


    def _cache_spacecoaster_state(self, data: bytes) -> None:
        """Cache only stable Unity states; state 99 is a transition notice."""
        try:
            fields = data.decode("ascii").strip().split(",", 2)
            state = int(fields[1])
        except (UnicodeDecodeError, ValueError, IndexError):
            return
        if 0 <= state <= 4:
            self._spacecoaster_last_state = data

    def _replay_spacecoaster_state(self, poc_address: str) -> bool:
        """Replay the last stable Unity state to a newly started POC adapter."""
        relay_sock = self._relay_sock
        state = self._spacecoaster_last_state
        if relay_sock is None or state is None:
            return False
        target = (poc_address, self.spacecoaster_relay_target_port)
        try:
            relay_sock.sendto(state, target)
        except OSError as exc:
            log.warning("SpaceCoaster cached state replay to %s failed: %s", target, exc)
            return False
        log.info("SpaceCoaster cached state replayed to %s:%d", target[0], target[1])
        return True

    def _clear_spacecoaster_session(self, reason: str) -> None:
        """Discard process-specific Unity endpoint and state information."""
        if self._spacecoaster_unity_addr is not None or self._spacecoaster_last_state is not None:
            log.info("SpaceCoaster relay session cleared: %s", reason)
        self._spacecoaster_unity_addr = None
        self._spacecoaster_last_state = None


    def send_spacecoaster_control(self, command: str) -> tuple[bool, str]:
        """Send a semantic control command to the learned Unity UDP endpoint."""
        allowed = {"pause", "resume", "ready", "run", "reset_vr", "lobby"}
        if command not in allowed:
            return False, f"unsupported SpaceCoaster UDP command: {command}"
        relay_sock = self._relay_sock
        unity_addr = self._spacecoaster_unity_addr
        if relay_sock is None:
            return False, "SpaceCoaster UDP relay socket is not available"
        if unity_addr is None:
            return False, "SpaceCoaster Unity UDP endpoint has not been learned"
        payload = f"control,{command}\n".encode("ascii")
        try:
            relay_sock.sendto(payload, unity_addr)
        except OSError as exc:
            return False, f"SpaceCoaster UDP control send failed: {exc}"
        log.info("SpaceCoaster UDP control %s -> %s:%d", command, unity_addr[0], unity_addr[1])
        return True, f"UDP control sent: {command}"

    def _debug_unknown_spacecoaster_packet(self, data: bytes, source: tuple[str, int]) -> None:
        """Identify unknown Unity datagrams without flooding the SHS log."""
        if not (SHS_SC_RELAY_DEBUG and SHS_SC_RELAY_DEBUG_UNKNOWN):
            return
        signature = (len(data), data[:24])
        count = self._sc_unknown_counts.get(signature, 0) + 1
        self._sc_unknown_counts[signature] = count
        if count > 3 and count % 100 != 0:
            return
        preview = data[:160].decode("utf-8", errors="backslashreplace")
        preview = preview.replace("\r", "\\r").replace("\n", "\\n")
        log.debug(
            "SHS SC unknown from %s len=%d count=%d prefix=%r hex=%s",
            source, len(data), count, preview, data[:32].hex(" "),
        )

    @staticmethod
    def _spacecoaster_packet_kind(data: bytes) -> str:
        """Classify only for diagnostics; forwarded bytes are never changed."""
        head = data.lstrip().split(b",", 1)[0].strip().lower()
        if head in (b"xyzrpy", b"posrpy", b"state", b"command", b"config"):
            return head.decode("ascii")
        return "unknown"

    def handle_datagram(self, data: bytes, addr: tuple[str, int]) -> Optional[bytes]:
        self.last_datagram_at = self._time()
        self.last_datagram_source = f"{addr[0]}:{addr[1]}"
        try:
            message = decode_message(data)
            message_type = str(message.get("type", ""))
            request_id = int(message.get("id"))
        except (UnicodeDecodeError, ValueError, TypeError) as exc:
            log.warning("Ignoring malformed SHS datagram from %s: %s", addr, exc)
            return None

        self.last_poc_message_at = self._time()
        self.last_poc_message_type = message_type

        if message_type == TYPE_PING:
            if addr[0] != self._poc_address:
                self._poc_address = addr[0]
                if SHS_SC_RELAY_DEBUG:
                    log.info("SpaceCoaster relay destination set from POC heartbeat: %s:%d", self._poc_address, self.spacecoaster_relay_target_port)
            return encode_message({
                "type": TYPE_PONG,
                "id": request_id,
                "protocol": PROTOCOL_VERSION,
                "server_version": SERVER_VERSION,
                "active_sim": self.manager.active_sim(),
            })

        if message_type == TYPE_FAN_POWER:
            try:
                protocol = int(message.get("protocol", -1))
                speed = max(0.0, float(message.get("speed", 0.0)))
                power = int(message.get("power"))
                if protocol != PROTOCOL_VERSION:
                    raise ValueError(f"unsupported protocol {protocol}")
                if not 0 <= power <= 100:
                    raise ValueError(f"fan power out of range: {power}")
            except (TypeError, ValueError) as exc:
                self.last_error = str(exc)
                log.warning("Ignoring invalid fan command from %s: %s", addr, exc)
                return None
            self.fan_speed = speed
            self.fan_power = power
            self._last_fan_command_at = self._time()
            self.last_error = ""
            self.fan.set_power(power)
            return None

        cache_key = (addr[0], int(addr[1]), request_id)
        cached = self._recent.get(cache_key)
        if cached is not None:
            return cached

        try:
            protocol = int(message.get("protocol", -1))
            if protocol != PROTOCOL_VERSION:
                response = make_ack(request_id, False, error=f"unsupported protocol {protocol}")
            elif message_type == TYPE_STARTUP:
                sim_key = str(message.get("sim", ""))
                was_running = self.manager.is_running(sim_key)
                if sim_key == "spacecoaster" and not was_running:
                    self._clear_spacecoaster_session("fresh startup")
                ok, detail = self.manager.ensure_sim(sim_key)
                # A restarted POC has a new, empty SpaceCoaster adapter. If the
                # existing Unity process is already running, replay its last
                # stable state so POC does not wait forever for an edge-triggered
                # state event which happened before POC restarted. Do not replay
                # stale state when SHS has just launched a new Unity process.
                if ok and sim_key == "spacecoaster" and was_running:
                    if not self._replay_spacecoaster_state(addr[0]):
                        sync_ok, sync_detail = self.send_spacecoaster_control("lobby")
                        if sync_ok:
                            log.info(
                                "SpaceCoaster was already running with no cached "
                                "state; lobby recovery requested"
                            )
                        else:
                            # Recovery is best effort. Do not reject the startup
                            # ACK merely because Unity's UDP endpoint has not yet
                            # been learned.
                            log.info(
                                "SpaceCoaster was already running with no cached "
                                "state; lobby recovery unavailable: %s",
                                sync_detail,
                            )
                response = make_ack(
                    request_id, ok, detail=detail, active_sim=self.manager.active_sim()
                )
                self.last_sim_action = f"Start {sim_key}: {detail}"
                log.info("startup %s: %s", sim_key, detail)
            elif message_type == TYPE_SELECT_SIM:
                sim_key = str(message.get("sim", ""))
                spacecoaster_was_running = self.manager.is_running("spacecoaster")
                if sim_key == "spacecoaster" and not spacecoaster_was_running:
                    self._clear_spacecoaster_session("fresh simulator selection")
                ok, detail = self.manager.select_sim(sim_key)
                if ok and sim_key != "spacecoaster" and spacecoaster_was_running:
                    self._clear_spacecoaster_session("SpaceCoaster closed")
                response = make_ack(
                    request_id, ok, detail=detail, active_sim=self.manager.active_sim()
                )
                self.last_sim_action = f"Select {sim_key}: {detail}"
                log.info("select_sim %s: %s", sim_key, detail)
            elif message_type == TYPE_COMMAND:
                target = str(message.get("target", ""))
                command = str(message.get("command", ""))
                if target == "spacecoaster" and command in {
                    "pause", "resume", "ready", "run", "reset_vr", "lobby"
                }:
                    ok, detail = self.send_spacecoaster_control(command)
                else:
                    ok = False
                    detail = f"unknown command: {target}/{command}"
                response = make_ack(request_id, ok, detail=detail)
                self.last_sim_action = f"{target}/{command}: {detail}"
                log.info("command %s/%s: %s", target, command, detail)
            else:
                response = make_ack(request_id, False, error=f"unknown message type: {message_type}")
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            log.exception("SHS request %s from %s failed", message_type, addr)
            response = make_ack(request_id, False, error=str(exc))

        encoded = encode_message(response)
        self._remember(cache_key, encoded)
        return encoded

    def _remember(self, key: tuple[str, int, int], response: bytes) -> None:
        self._recent[key] = response
        self._recent.move_to_end(key)
        while len(self._recent) > self.recent_request_count:
            self._recent.popitem(last=False)


def load_config(path: Path) -> tuple[dict, dict[str, SimConfig]]:
    parser = configparser.ConfigParser()
    if not parser.read(path, encoding="utf-8"):
        raise FileNotFoundError(path)

    server = {
        "bind_address": parser.get("server", "bind_address", fallback="0.0.0.0"),
        "port": parser.getint("server", "port", fallback=10040),
        "recent_request_count": parser.getint("server", "recent_request_count", fallback=128),
        "spacecoaster_relay_enabled": parser.getboolean("spacecoaster_relay", "enabled", fallback=True),
        "spacecoaster_relay_bind_address": parser.get("spacecoaster_relay", "bind_address", fallback="0.0.0.0"),
        "spacecoaster_relay_listen_port": parser.getint("spacecoaster_relay", "listen_port", fallback=10009),
        "spacecoaster_relay_target_port": parser.getint("spacecoaster_relay", "target_port", fallback=10009),
        "fan_enabled": parser.getboolean("fan", "enabled", fallback=True),
        "fan_port": parser.get("fan", "port", fallback="").strip(),
        "fan_baudrate": parser.getint("fan", "baudrate", fallback=115200),
        "fan_command_timeout_s": parser.getfloat("fan", "command_timeout_s", fallback=1.0),
        "poc_timeout_s": parser.getfloat("server", "poc_timeout_s", fallback=3.0),
        "config_path": path,
    }

    sims: dict[str, SimConfig] = {}
    for section in parser.sections():
        if not section.startswith("sim:"):
            continue
        key = section.split(":", 1)[1].strip()
        sims[key] = SimConfig(
            key=key,
            window_class=parser.get(section, "window_class", fallback="").strip(),
            window_title=parser.get(section, "window_title", fallback="").strip(),
            start_batch=parser.get(section, "start_batch", fallback="").strip(),
            close_timeout_s=parser.getfloat(section, "close_timeout_s", fallback=10.0),
        )
    return server, sims


class ShsWindow:
    """Public-facing SHS dashboard with technician controls and diagnostics."""

    def __init__(self, root, service: SimHostService):
        import tkinter as tk
        from tkinter import ttk

        self.root = root
        self.service = service
        self.root.title("Platform Operators Console — Simulator Host Service")
        self.root.geometry("900x650")
        self.root.minsize(820, 600)
        self.root.resizable(True, True)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        icon_path = Path(__file__).resolve().parent / "windows" / "poc_shs.ico"
        if os.name == "nt" and icon_path.exists():
            try:
                self.root.iconbitmap(default=str(icon_path))
            except tk.TclError:
                log.warning("Unable to apply SHS window icon: %s", icon_path)

        style = ttk.Style(root)
        style.configure("Shs.Header.TLabel", font=("Segoe UI", 22, "bold"))
        style.configure("Shs.Version.TLabel", font=("Segoe UI", 11))
        style.configure("Shs.Section.TLabelframe.Label", font=("Segoe UI", 15, "bold"))
        style.configure("Shs.Caption.TLabel", font=("Segoe UI", 12, "bold"))
        style.configure("Shs.Value.TLabel", font=("Segoe UI", 12))
        style.configure("Shs.Control.TLabel", font=("Segoe UI", 12))
        style.configure("Shs.Control.TButton", font=("Segoe UI", 12))
        style.configure("Shs.Control.TCombobox", font=("Segoe UI", 12))
        style.configure(
            "Shs.Ready.TLabel", font=("Segoe UI", 20, "bold"),
            foreground="#ffffff", background="#19733c", anchor="center",
        )
        style.configure(
            "Shs.Waiting.TLabel", font=("Segoe UI", 20, "bold"),
            foreground="#202020", background="#f2c94c", anchor="center",
        )
        style.configure(
            "Shs.Error.TLabel", font=("Segoe UI", 20, "bold"),
            foreground="#ffffff", background="#b3261e", anchor="center",
        )

        frame = ttk.Frame(root, padding=18)
        frame.grid(sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(5, weight=1)

        ttk.Label(
            frame, text="Platform Operators Console", style="Shs.Header.TLabel",
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            frame,
            text=f"Simulator Host Service   •   SHS version "
                 f"{project_manifest_version()} : {SERVER_FILE_VERSION}",
            style="Shs.Version.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(0, 12))

        self.banner_text = tk.StringVar(value="STARTING SIMULATOR HOST SERVICE")
        self.banner = ttk.Label(
            frame, textvariable=self.banner_text, style="Shs.Waiting.TLabel",
            padding=(12, 12),
        )
        self.banner.grid(row=2, column=0, sticky="ew", pady=(0, 12))

        self.values = {
            name: tk.StringVar(value="-")
            for name in (
                "console", "last_console", "console_address", "windows_ip",
                "listen", "sim", "sim_process", "sim_action", "relay", "fan",
                "speed", "power", "serial_command", "controller_message", "error",
            )
        }

        summary = ttk.Frame(frame)
        summary.grid(row=3, column=0, sticky="ew")
        summary.columnconfigure(0, weight=1)
        summary.columnconfigure(1, weight=1)

        connection = ttk.LabelFrame(
            summary, text="Console Connection", padding=10,
            style="Shs.Section.TLabelframe",
        )
        connection.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        simulator = ttk.LabelFrame(
            summary, text="Simulator", padding=10,
            style="Shs.Section.TLabelframe",
        )
        simulator.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        self._add_rows(connection, (
            ("Connection", "console"),
            ("Last message", "last_console"),
            ("Console address", "console_address"),
            ("Windows IPv4", "windows_ip"),
            ("SHS listening", "listen"),
        ))
        self._add_rows(simulator, (
            ("Active simulator", "sim"),
            ("Process/window", "sim_process"),
            ("Last action", "sim_action"),
            ("SpaceCoaster relay", "relay"),
        ))

        fan_frame = ttk.LabelFrame(
            frame, text="Fan Controller", padding=10,
            style="Shs.Section.TLabelframe",
        )
        fan_frame.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        self._add_rows(fan_frame, (
            ("Connection", "fan"),
            ("Simulator speed", "speed"),
            ("Commanded power", "power"),
            ("Last serial command", "serial_command"),
            ("Controller response", "controller_message"),
        ))

        controls = ttk.Frame(fan_frame)
        controls.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Label(controls, text="Fan COM port:", style="Shs.Control.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 8)
        )
        self.port_var = tk.StringVar(value=service.fan.snapshot()["port"])
        self.port_combo = ttk.Combobox(
            controls, textvariable=self.port_var, width=20, state="readonly",
            style="Shs.Control.TCombobox",
        )
        self.port_combo.grid(row=0, column=1, sticky="w")
        ttk.Button(
            controls, text="Rescan Ports", command=self.refresh_ports,
            style="Shs.Control.TButton",
        ).grid(row=0, column=2, sticky="w", padx=(12, 6))
        ttk.Button(
            controls, text="Open Port", command=self.open_port,
            style="Shs.Control.TButton",
        ).grid(row=0, column=3, sticky="w")

        lower = ttk.Frame(frame)
        lower.grid(row=5, column=0, sticky="nsew", pady=(12, 0))
        lower.columnconfigure(0, weight=1)
        lower.rowconfigure(0, weight=1)
        self.diagnostics_frame = ttk.LabelFrame(
            lower, text="Console Connection Diagnostics", padding=10,
            style="Shs.Section.TLabelframe",
        )
        self.diagnostics_frame.grid(row=0, column=0, sticky="nsew")
        self.diagnostics_frame.columnconfigure(0, weight=1)
        self.diagnostics_text = tk.StringVar(value="")
        ttk.Label(
            self.diagnostics_frame, textvariable=self.diagnostics_text,
            style="Shs.Value.TLabel", anchor="nw", justify="left", wraplength=740,
        ).grid(sticky="nsew")

        self.error_frame = ttk.LabelFrame(
            lower, text="Service Messages", padding=10,
            style="Shs.Section.TLabelframe",
        )
        self.error_frame.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        self.error_frame.columnconfigure(0, weight=1)
        ttk.Label(
            self.error_frame, textvariable=self.values["error"],
            style="Shs.Value.TLabel", anchor="w", wraplength=740,
        ).grid(sticky="ew")

        self.refresh_ports()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh_status()

    def _add_rows(self, parent, rows) -> None:
        from tkinter import ttk

        parent.columnconfigure(1, weight=1)
        for row, (caption, key) in enumerate(rows):
            ttk.Label(
                parent, text=caption + ":", style="Shs.Caption.TLabel",
            ).grid(row=row, column=0, sticky="ne", padx=(0, 8), pady=2)
            ttk.Label(
                parent, textvariable=self.values[key], style="Shs.Value.TLabel",
                anchor="w", justify="left", wraplength=250,
            ).grid(row=row, column=1, sticky="nw", pady=2)

    def refresh_ports(self) -> None:
        configured = self.service.fan.snapshot()["port"]
        ports = self.service.fan.available_ports()
        if configured and configured not in ports:
            ports.insert(0, configured)
        self.port_combo["values"] = ports
        if configured:
            self.port_var.set(configured)
        elif ports:
            self.port_var.set(ports[0])

    def open_port(self) -> None:
        self.service.fan.open_port(self.port_var.get())
        self.refresh_status()

    def refresh_status(self) -> None:
        status = self.service.status_snapshot()
        age = status["poc_message_age"]
        banner_text, banner_style = dashboard_status(status)
        self.banner_text.set(banner_text)
        self.banner.configure(style=banner_style)

        self.values["console"].set("Connected" if status["poc_connected"] else "Not connected")
        self.values["last_console"].set(
            "-" if age is None else f"{status['poc_message_type']} ({age:.1f} s ago)"
        )
        self.values["console_address"].set(status["poc_address"] or "-")
        self.values["windows_ip"].set(", ".join(status["local_ipv4"]) or "None found")
        listen_summary = (
            f"{status['listen_state']} — UDP "
            f"{status['listen_address']}:{status['listen_port']}"
        )
        if status["listen_error"]:
            listen_summary += f" — {status['listen_error']}"
        self.values["listen"].set(listen_summary)
        self.values["sim"].set(status["active_sim"] or "-")
        self.values["sim_process"].set("Running" if status["sim_running"] else "Not running")
        self.values["sim_action"].set(status["last_sim_action"] or "-")
        if not status["spacecoaster_relay_enabled"]:
            relay = "Disabled"
        elif status["spacecoaster_unity_endpoint"]:
            relay = f"Unity {status['spacecoaster_unity_endpoint']}"
        else:
            relay = "Waiting for Unity"
        self.values["relay"].set(relay)
        self.values["fan"].set(
            f"Connected: {status['fan_port']}" if status["fan_connected"] else "Not connected"
        )
        self.values["speed"].set(f"{status['fan_speed']:.2f}")
        self.values["power"].set(f"{status['fan_power']}%")
        self.values["serial_command"].set(status["fan_last_command"] or "-")
        self.values["controller_message"].set(status["fan_last_response"] or "-")
        self.values["error"].set(status["last_error"] or "No current errors")
        if status["poc_connected"]:
            self.diagnostics_frame.grid_remove()
        else:
            packet = "none received"
            if status["last_datagram_age"] is not None:
                packet = (
                    f"{status['last_datagram_source']} "
                    f"({status['last_datagram_age']:.1f} s ago)"
                )
            listen = (
                f"{status['listen_state']} on UDP "
                f"{status['listen_address']}:{status['listen_port']}"
            )
            if status["listen_error"]:
                listen += f" - {status['listen_error']}"
            addresses = ", ".join(status["local_ipv4"]) or "none found"
            self.diagnostics_text.set(
                f"SHS: {listen}\n"
                f"Windows IPv4: {addresses}\n"
                f"Last UDP packet on SHS port: {packet}\n"
                "Expected Console target: this Windows IPv4 address, UDP port shown above"
            )
            self.diagnostics_frame.grid()
        self.root.after(250, self.refresh_status)

    def close(self) -> None:
        self.service.stop()
        self.root.after(300, self.root.destroy)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Platform Operators Console Sim Host Service"
    )
    parser.add_argument(
        "--config",
        default=str(Path(__file__).with_name("sim_host_service.ini")),
        help="Path to Sim Host Service INI file",
    )
    parser.add_argument("--no-gui", action="store_true", help="Run SHS without the operator window")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if SHS_DEBUG else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    config_path = Path(args.config).resolve()
    print(
        f"Sim Host Service {SERVER_VERSION} "
        f"(server.py ver {SERVER_FILE_VERSION}, protocol {PROTOCOL_VERSION})",
        flush=True,
    )
    server_cfg, sims = load_config(config_path)
    manager = WindowsSimManager(sims)
    service = SimHostService(manager, **server_cfg)
    try:
        if args.no_gui:
            service.serve_forever()
        else:
            try:
                import tkinter as tk
                root = tk.Tk()
            except Exception as exc:
                log.error("Unable to open SHS window; running headless: %s", exc)
                service.serve_forever()
            else:
                thread = threading.Thread(target=service.serve_forever, name="SHSServer", daemon=True)
                thread.start()
                ShsWindow(root, service)
                root.mainloop()
                service.stop()
                thread.join(timeout=2.0)
    except KeyboardInterrupt:
        log.info("Sim Host Service stopping")
        service.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
