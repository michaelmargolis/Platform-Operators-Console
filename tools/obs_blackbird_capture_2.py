"""Blackbird ride + OBS recording proof of concept (Windows only).

Place in PlatformOperatorsConsole/tools/; run from the repository root:
    pip install obsws-python
    python tools/obs_blackbird_capture.py --obs-password YOUR_PASSWORD

In OBS, create a Game Capture source named "NL2 Game Capture" and enable
Tools > WebSocket Server Settings (default port 4455). The script changes
that source's target window and OBS's recording directory before recording.
No POC UI, SHS or Festo hardware. Do not run alongside POC.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.capture_nl2_rides import capture_one, connect_recorder
from sims.nolimits2 import config
from sims.nolimits2.sim import Sim


def list_nl2_windows(expected_ride: str) -> tuple[list[tuple[str, str, str]], list[str]]:
    """Discover NL2 windows across ALL monitors, with useful failure diagnostics.

    EnumWindows enumerates desktop top-level windows irrespective of monitor.
    Never require IsWindowVisible: games can briefly hide/recreate windows
    during park loading, and a minimized window may still be a valid target.
    """
    if sys.platform != "win32":
        raise RuntimeError("OBS Game Capture window discovery requires Windows")

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                     wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    matches = []
    candidates = []

    @enum_proc
    def callback(hwnd, _):
        title_length = user32.GetWindowTextLengthW(hwnd)
        title_buf = ctypes.create_unicode_buffer(max(1, title_length + 1))
        class_buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title_buf, len(title_buf))
        user32.GetClassNameW(hwnd, class_buf, len(class_buf))
        title, class_name = title_buf.value, class_buf.value
        is_nl2_class = class_name.casefold().startswith("nl3d_main_")
        is_nl2_title = "nolimits 2" in title.casefold() or ".nl2park" in title.casefold()
        if not (is_nl2_class or is_nl2_title):
            return True

        visible = bool(user32.IsWindowVisible(hwnd))
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        exe = ""
        process = kernel32.OpenProcess(0x1000, False, pid.value)
        if process:
            try:
                exe_buf = ctypes.create_unicode_buffer(32768)
                exe_size = wintypes.DWORD(len(exe_buf))
                if kernel32.QueryFullProcessImageNameW(process, 0, exe_buf,
                                                        ctypes.byref(exe_size)):
                    exe = Path(exe_buf.value).name
            finally:
                kernel32.CloseHandle(process)
        candidates.append(f"HWND={int(hwnd)} visible={visible} pid={pid.value} "
                          f"title={title!r} class={class_name!r} exe={exe!r}")
        # Prefer the actual 3D render window. A title alone may match a launcher.
        # Case-insensitive and substring matching accommodate dynamic captions.
        if (is_nl2_class and expected_ride.casefold() in title.casefold()
                and exe):
            matches.append((title, class_name, exe))
        return True

    if not user32.EnumWindows(callback, 0):
        raise RuntimeError("Windows EnumWindows failed")
    return matches, candidates


def wait_for_nl2_window(ride: str, timeout: float) -> tuple[str, str, str]:
    deadline = time.monotonic() + timeout
    last_candidates = []
    while True:
        matches, candidates = list_nl2_windows(ride)
        last_candidates = candidates
        if len(matches) == 1:
            print("Found NL2 window:", matches[0])
            return matches[0]
        if len(matches) > 1:
            raise RuntimeError(f"Multiple matching NL2 windows for {ride}: {matches}")
        if time.monotonic() >= deadline:
            details = "\n  ".join(last_candidates) if last_candidates else "(no NL2-like windows found)"
            raise RuntimeError(
                f"No matching NL2 render window for {ride!r}. "
                "Enumeration covers all monitors; check the following actual "
                f"Windows titles/classes:\n  {details}"
            )
        time.sleep(0.25)


def configure_obs(client, source_name: str, output_dir: Path, nl2_window):
    """Select a specific game window and set the recording path before starting."""
    existing = client.get_input_settings(source_name)
    if existing.input_kind  not in ("game_capture", "window_capture"):
        raise RuntimeError(f"OBS source {source_name!r} is not Game Capture "
                           f"(kind={existing.input_kind!r})")
    title, class_name, exe = nl2_window
    # OBS Windows Game Capture expects 'title:class:executable'.
    window_id = f"{title}:{class_name}:{exe}"
    client.set_input_settings(source_name,
                              {"capture_mode": "window", "window": window_id},
                              overlay=True)
    actual = client.get_input_settings(source_name).input_settings
    if actual.get("window") != window_id or actual.get("capture_mode") != "window":
        raise RuntimeError("OBS Game Capture settings did not persist")

    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        client.set_record_directory(str(output_dir))
    except Exception as exc:
        # OBS versions without SetRecordDirectory can use the simple output profile.
        print(f"SetRecordDirectory unavailable ({exc}); trying SimpleOutput/FilePath.")
        client.set_profile_parameter("SimpleOutput", "FilePath", str(output_dir))
    # Validate path rather than risk saving a recording elsewhere.
    try:
        actual_dir = Path(client.get_record_directory().record_directory)
    except Exception:
        actual_dir = Path(client.get_profile_parameter("SimpleOutput", "FilePath").parameter_value)
    if actual_dir.resolve() != output_dir.resolve():
        raise RuntimeError(f"OBS recording path mismatch: {actual_dir} != {output_dir}")
    print("OBS Game Capture target:", window_id)
    print("OBS recording directory:", actual_dir)
    print("Check OBS preview shows the game; WebSocket cannot confirm visible pixels.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--obs-password", default="", help="OBS WebSocket password")
    parser.add_argument("--obs-port", type=int, default=4455)
    parser.add_argument("--obs-source", default="NL2 Game Capture",
                        help="Name of existing OBS Game Capture input")
    parser.add_argument("--nl2-host", default="127.0.0.1")
    parser.add_argument("--window-timeout", type=float, default=15.0)
    parser.add_argument("--max-duration", type=float, default=480.0)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("This proof of concept requires Windows")
    try:
        import obsws_python as obs
    except ImportError:
        parser.error("Install OBS client with: pip install obsws-python")

    rides = tuple(config.RIDES)
    match = [i for i, (name, _) in enumerate(rides) if name.casefold() == "blackbird"]
    if len(match) != 1:
        parser.error("Blackbird was not found uniquely in NL2 config.RIDES")
    index = match[0]
    capture_args = argparse.Namespace(
        host=args.nl2_host, hz=20.0, prepare_timeout=90.0,
        max_duration=args.max_duration,
        output_dir=ROOT / "captures" / "nl2_ride_study", state_monitor=False,
    )
    sim = Sim(address=args.nl2_host, startup_ride_index=index)
    obs_client = None
    recording_started = False
    try:
        connect_recorder(sim, capture_args.prepare_timeout, 1.0 / capture_args.hz)
        window = wait_for_nl2_window("Blackbird", args.window_timeout)
        obs_client = obs.ReqClient(host="127.0.0.1", port=args.obs_port,
                                   password=args.obs_password, timeout=5)
        if obs_client.get_record_status().output_active:
            raise RuntimeError("OBS is already recording; stop it before this test")
        configure_obs(obs_client, args.obs_source, capture_args.output_dir, window)
        print("Starting OBS recording; Blackbird will be prepared and dispatched...")
        obs_client.start_record()
        recording_started = True
        print("OBS start requested:", datetime.now(timezone.utc).isoformat(),
              "monotonic:", time.monotonic())
        csv_path = capture_one(sim, index, 1, capture_args, rides)
        print("Ride completed:", csv_path)
        return 0
    finally:
        if recording_started:
            try:
                stopped = obs_client.stop_record()
                print("OBS recording saved:", stopped.output_path)
                print("OBS stop requested:", datetime.now(timezone.utc).isoformat(),
                      "monotonic:", time.monotonic())
            except Exception as exc:
                print("WARNING: OBS recording could not be stopped:", exc,
                      file=sys.stderr)
        sim.disconnect()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted by user; closing connections.", file=sys.stderr)
        raise SystemExit(130)
