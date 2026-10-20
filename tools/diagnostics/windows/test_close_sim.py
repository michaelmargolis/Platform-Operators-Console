#test_close_sim.py  ver 4
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sim_host_service.server import load_config

WM_CLOSE = 0x0010
SMTO_ABORTIFHUNG = 0x0002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    pid: int
    title: str
    class_name: str
    visible: bool
    enabled: bool
    iconic: bool


def _require_windows() -> None:
    if os.name != "nt" or not hasattr(ctypes, "windll"):
        raise RuntimeError("this diagnostic must be run on the Windows simulator PC")


def _window_text(user32, hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, len(buffer))
    return buffer.value


def _class_name(user32, hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buffer, len(buffer))
    return buffer.value


def enumerate_top_windows() -> list[WindowInfo]:
    _require_windows()
    user32 = ctypes.windll.user32
    windows: list[WindowInfo] = []
    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    @EnumProc
    def callback(hwnd, _lparam):
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        windows.append(
            WindowInfo(
                hwnd=int(hwnd),
                pid=int(pid.value),
                title=_window_text(user32, hwnd),
                class_name=_class_name(user32, hwnd),
                visible=bool(user32.IsWindowVisible(hwnd)),
                enabled=bool(user32.IsWindowEnabled(hwnd)),
                iconic=bool(user32.IsIconic(hwnd)),
            )
        )
        return True

    user32.EnumWindows(callback, 0)
    return windows


def matching_windows(window_class: str, title_fragment: str) -> tuple[list[WindowInfo], str]:
    windows = enumerate_top_windows()
    if window_class:
        wanted_class = window_class.casefold()
        matches = [
            info for info in windows
            if info.visible and info.class_name.casefold() == wanted_class
        ]
        if matches:
            return matches, "window class"

    if title_fragment:
        wanted_title = title_fragment.casefold()
        matches = [
            info for info in windows
            if info.visible and wanted_title in info.title.casefold()
        ]
        if matches:
            return matches, "window title fallback"

    return [], "none"


def child_buttons(hwnd: int) -> list[tuple[int, str]]:
    _require_windows()
    user32 = ctypes.windll.user32
    buttons: list[tuple[int, str]] = []
    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    @EnumProc
    def callback(child_hwnd, _lparam):
        if _class_name(user32, child_hwnd).casefold() == "button":
            buttons.append((int(child_hwnd), _window_text(user32, child_hwnd)))
        return True

    user32.EnumChildWindows(hwnd, callback, 0)
    return buttons


def process_alive(pid: int) -> bool:
    _require_windows()
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        # The process may have exited between enumeration and this check.
        return False
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return int(exit_code.value) == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def send_close(hwnd: int, timeout_ms: int = 2000) -> bool:
    _require_windows()
    user32 = ctypes.windll.user32
    result = ctypes.c_void_p()
    ok = user32.SendMessageTimeoutW(
        hwnd,
        WM_CLOSE,
        0,
        0,
        SMTO_ABORTIFHUNG,
        int(timeout_ms),
        ctypes.byref(result),
    )
    return bool(ok)


def print_process_windows(pids: set[int]) -> None:
    found = [info for info in enumerate_top_windows() if info.pid in pids and info.visible]
    if not found:
        print("  No visible top-level windows found for the remaining process(es).")
        return
    for info in found:
        state = "minimized" if info.iconic else "normal"
        print(
            f'hwnd=0x{info.hwnd:08X} pid={info.pid} class="{info.class_name}" '
            f'visible={info.visible} enabled={info.enabled} state={state} title="{info.title}"'
        )
        buttons = child_buttons(info.hwnd)
        if buttons:
            print("    child Button controls:")
            for child_hwnd, text in buttons:
                print(f'      hwnd=0x{child_hwnd:08X} text="{text}"')


def run(sim_key: str, config_path: Path, timeout_s: float, poll_s: float, delay_s: float) -> int:
    _require_windows()
    _server_cfg, sims = load_config(config_path)
    if sim_key not in sims:
        choices = ", ".join(sorted(sims)) or "<none>"
        print(f"ERROR: unknown simulator '{sim_key}'. Configured sims: {choices}", file=sys.stderr)
        return 2

    cfg = sims[sim_key]
    window_class = cfg.window_class
    windows, match_method = matching_windows(window_class, cfg.window_title)
    if not windows:
        criteria = []
        if window_class:
            criteria.append(f'class {window_class!r}')
        if cfg.window_title:
            criteria.append(f'title containing {cfg.window_title!r}')
        print(
            f"ERROR: no visible window matching {' or '.join(criteria) or 'configured criteria'} "
            f"was found for {sim_key}",
            file=sys.stderr,
        )
        return 3

    pids = {info.pid for info in windows}
    print(f"Simulator: {sim_key}")
    print(f"Primary window class: {window_class!r}")
    print(f"Fallback title fragment: {cfg.window_title!r}")
    print(f"Match method: {match_method}")
    print(f"Matched {len(windows)} visible window(s), PID(s): {', '.join(map(str, sorted(pids)))}")
    for info in windows:
        print(
            f'  hwnd=0x{info.hwnd:08X} pid={info.pid} class="{info.class_name}" '
            f'title="{info.title}"'
        )

    if delay_s > 0:
        print(f"\nWM_CLOSE will be sent in {delay_s:.1f} seconds; press Ctrl+C now to abort.")
        time.sleep(delay_s)
    print("\nSending WM_CLOSE to matched simulator window(s)...")
    for info in windows:
        delivered = send_close(info.hwnd)
        print(f"  hwnd=0x{info.hwnd:08X}: {'delivered' if delivered else 'timed out/failed'}")

    deadline = time.monotonic() + max(0.0, timeout_s)
    last_alive: set[int] | None = None
    while True:
        alive = {pid for pid in pids if process_alive(pid)}
        if not alive:
            print("\nRESULT: process exited normally after WM_CLOSE.")
            return 0
        if alive != last_alive:
            print(f"Waiting for process exit; still alive: {', '.join(map(str, sorted(alive)))}")
            last_alive = alive
        if time.monotonic() >= deadline:
            print(f"\nRESULT: process still alive after {timeout_s:.1f} seconds.")
            print("Visible windows belonging to the remaining process(es):")
            print_process_windows(alive)
            print("\nNo dialog buttons were clicked and the process was NOT force-killed.")
            return 4
        time.sleep(max(0.05, poll_s))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Diagnostic: send WM_CLOSE to a configured simulator and inspect shutdown behaviour."
    )
    parser.add_argument("sim", help="Simulator key from sim_host_service.ini, e.g. nolimits2 or spacecoaster")
    parser.add_argument(
        "--config",
        default=str(PROJECT_ROOT / "sim_host_service" / "sim_host_service.ini"),
        help="Path to Sim Host Service INI file",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Seconds to wait for the simulator process to exit (default: 10)",
    )
    parser.add_argument(
        "--poll",
        type=float,
        default=0.25,
        help="Process-exit polling interval in seconds (default: 0.25)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=3.0,
        help="Seconds to wait after displaying the matched window before WM_CLOSE (default: 3)",
    )
    args = parser.parse_args()
    try:
        return run(args.sim, Path(args.config).resolve(), args.timeout, args.poll, max(0.0, args.delay))
    except (OSError, RuntimeError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
