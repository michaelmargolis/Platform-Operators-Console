# nl2_obs_recorder.py  ver 3
"""Optional OBS WebSocket video recording for the standalone NL2 capture tool.

Importing this module does not require OBS, Windows, or obsws-python. Nothing
here is imported by POC's runtime, and it does not send hardware commands.
OBS controls only the recording lifecycle; exact video-frame/telemetry alignment
requires subsequent calibration (WebSocket acknowledgements are not frame PTS).
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import json
import os
import re
from pathlib import Path
import sys
import time


def clock_marker() -> dict:
    return {"monotonic_s": time.monotonic(),
            "utc": datetime.now(timezone.utc).isoformat(timespec="milliseconds")}


def _normalized_ride_name(value: str) -> str:
    """Compare park names independently of spaces, punctuation and case."""
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _matches_ride_window(ride_name: str, title: str, class_name: str, exe: str) -> bool:
    """Match a rendered NL2 window, tolerating compacted .nl2park filenames."""
    if not class_name.casefold().startswith("nl3d_main_") or not exe:
        return False
    park_title = title.casefold().split(".nl2park", 1)[0]
    return (".nl2park" in title.casefold()
            and _normalized_ride_name(ride_name) == _normalized_ride_name(park_title))


def nl2_windows(ride_name: str):
    """Find rendered NL2 top-level windows across all monitors (Windows only).

    Match the class prefix, ride caption and executable rather than a GUID or
    monitor index. Return candidates for useful error messages on failure.
    """
    if sys.platform != "win32":
        raise RuntimeError("OBS NL2 window selection is supported on Windows only")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
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
    matching = []
    candidates = []

    @callback_type
    def on_window(hwnd, _):
        title_buffer = ctypes.create_unicode_buffer(max(1, user32.GetWindowTextLengthW(hwnd) + 1))
        class_buffer = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, title_buffer, len(title_buffer))
        user32.GetClassNameW(hwnd, class_buffer, len(class_buffer))
        title, class_name = title_buffer.value, class_buffer.value
        render_class = class_name.casefold().startswith("nl3d_main_")
        nl2_title = ".nl2park" in title.casefold() or "nolimits 2" in title.casefold()
        if not (render_class or nl2_title):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        exe = ""
        process = kernel32.OpenProcess(0x1000, False, pid.value)
        if process:
            try:
                exe_buffer = ctypes.create_unicode_buffer(32768)
                size = wintypes.DWORD(len(exe_buffer))
                if kernel32.QueryFullProcessImageNameW(process, 0, exe_buffer, ctypes.byref(size)):
                    exe = Path(exe_buffer.value).name
            finally:
                kernel32.CloseHandle(process)
        candidates.append(f"HWND={int(hwnd)} visible={bool(user32.IsWindowVisible(hwnd))} "
                          f"pid={pid.value} title={title!r} class={class_name!r} exe={exe!r}")
        if _matches_ride_window(ride_name, title, class_name, exe):
            matching.append((title, class_name, exe))
        return True

    if not user32.EnumWindows(on_window, 0):
        raise OSError("EnumWindows failed")
    return matching, candidates


def find_nl2_window(ride_name: str, timeout_s: float):
    end = time.monotonic() + timeout_s
    candidates = []
    while True:
        matches, candidates = nl2_windows(ride_name)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise RuntimeError(f"Multiple NL2 render windows matched {ride_name!r}: {matches}")
        if time.monotonic() >= end:
            detail = "\n  ".join(candidates) if candidates else "(none)"
            raise RuntimeError(
                f"No NL2 render window found for {ride_name!r} (all monitors searched).\n  {detail}")
        time.sleep(0.25)


class OBSRideRecorder:
    """One OBS connection, separate video and timing manifest for every ride."""

    def __init__(self, output_dir: Path, *, host="127.0.0.1", port=4455,
                 password="", source="NL2 Game Capture", window_timeout=15.0,
                 tail_s=0.5, lead_s=0.35, client=None, window_finder=find_nl2_window):
        self.output_dir = Path(output_dir).resolve()
        self.source = source
        self.window_timeout = window_timeout
        self.tail_s = tail_s
        self.lead_s = lead_s
        self.window_finder = window_finder
        if client is None:
            try:
                import obsws_python
            except ImportError as exc:
                raise RuntimeError("Install OBS WebSocket client: pip install obsws-python") from exc
            client = obsws_python.ReqClient(host=host, port=port, password=password, timeout=5)
        self.client = client
        self._active = None
        self._owned_recording = False
        self._saved_directory = None
        self._directory_set_with_profile = False
        if self.client.get_record_status().output_active:
            raise RuntimeError("OBS is already recording; stop it before using --record-video")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _record_directory(self):
        try:
            return Path(self.client.get_record_directory().record_directory)
        except Exception:
            return Path(self.client.get_profile_parameter("SimpleOutput", "FilePath").parameter_value)

    def _set_directory(self, directory):
        """Set OBS output directory; retry if OBS is finishing an MP4.

        A transient OBS error 500 after StopRecord is not evidence that the
        WebSocket request is unsupported. Do not switch to profile-setting
        fallback on that error.
        """
        directory = Path(directory)
        deadline = time.monotonic() + 5.0
        while True:
            try:
                self.client.set_record_directory(str(directory))
                self._directory_set_with_profile = False
                break
            except AttributeError:
                # Older obsws-python builds without this WebSocket request.
                self.client.set_profile_parameter("SimpleOutput", "FilePath", str(directory))
                self._directory_set_with_profile = True
                break
            except Exception as exc:
                # Code 500 is often transient while OBS is finalizing output.
                # A recognized unsupported-request error can use the fallback.
                code = getattr(exc, "code", None)
                if code is None:
                    code = getattr(exc, "status_code", None)
                if code in (204, 205):
                    self.client.set_profile_parameter("SimpleOutput", "FilePath", str(directory))
                    self._directory_set_with_profile = True
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"OBS rejected recording directory {directory}: {exc}") from exc
                time.sleep(0.2)
        actual = self._record_directory()
        if os.path.normcase(str(actual.resolve())) != os.path.normcase(str(directory.resolve())):
            raise RuntimeError(f"OBS recording directory mismatch: {actual} != {directory}")

    def _name_video_when_available(self, source: Path, target: Path, context: dict):
        """Rename after OBS releases the recording, retaining it if locked.

        Windows may keep the file open briefly after StopRecord acknowledges.
        A naming failure must not discard an otherwise valid ride recording.
        """
        if source.resolve() == target.resolve():
            return source
        if target.exists():
            raise FileExistsError(f"Cannot overwrite existing ride video: {target}")
        deadline = time.monotonic() + 5.0
        while True:
            try:
                # Repeat collision check before every attempt (never overwrite).
                if target.exists():
                    raise FileExistsError(f"Cannot overwrite existing ride video: {target}")
                source.rename(target)
                return target
            except PermissionError as exc:
                if time.monotonic() >= deadline:
                    context["video_rename_warning"] = (
                        f"OBS video remains under its original filename; "
                        f"Windows still holds the file: {exc}")
                    print(f"WARNING: {context['video_rename_warning']}", file=sys.stderr)
                    return source
                time.sleep(0.2)

    def _configure(self, ride_name):
        title, class_name, exe = self.window_finder(ride_name, self.window_timeout)
        source_info = self.client.get_input_settings(self.source)
        kind = source_info.input_kind
        if kind not in ("game_capture", "window_capture"):
            raise RuntimeError(f"OBS source {self.source!r} must be Game Capture or "
                               f"Window Capture, not {kind!r}")
        window_id = f"{title}:{class_name}:{exe}"
        settings = {"window": window_id}
        if kind == "game_capture":
            settings["capture_mode"] = "window"
        self.client.set_input_settings(self.source, settings, overlay=True)
        actual = self.client.get_input_settings(self.source).input_settings
        if actual.get("window") != window_id:
            raise RuntimeError(f"OBS did not retain window target {window_id!r}: {actual}")
        if kind == "game_capture" and actual.get("capture_mode") != "window":
            raise RuntimeError("OBS Game Capture did not retain per-window capture mode")
        if self._saved_directory is None:
            self._saved_directory = self._record_directory()
        self._set_directory(self.output_dir)
        print(f"OBS {kind}: {window_id}; recording directory: {self.output_dir}", flush=True)
        print("NOTE: OBS WebSocket cannot verify image pixels; check preview on first run.", flush=True)
        return {"source": self.source, "input_kind": kind, "window": window_id}

    def begin(self, ride_name: str, stem: str, *, has_telemetry=True):
        """Called after station READY and just before issuing NL2 dispatch."""
        if self._active is not None:
            raise RuntimeError("Previous OBS ride recording has not finished")
        if self.client.get_record_status().output_active:
            raise RuntimeError("OBS already recording; cannot take ownership of another recording")
        context = {"schema": 1, "ride": ride_name, "stem": stem,
                   "telemetry_csv": f"{stem}.csv" if has_telemetry else None,
                   "state_events_csv": f"{stem}_state_events.csv",
                   "obs_source": self.source, "sync_calibrated": False,
                   "video_to_telemetry_offset_ms": None,
                   "sync_note": ("WebSocket start/stop times bracket OBS control, not first/last "
                                 "encoded frame PTS; align video to telemetry empirically."),
                   "events": {}}
        context["obs_target"] = self._configure(ride_name)
        context["events"]["obs_start_request"] = clock_marker()
        try:
            self.client.start_record()
        except Exception:
            # A request timeout could still have started recording: never leave
            # a recording running unnoticed, but do not touch an earlier session.
            try:
                if self.client.get_record_status().output_active:
                    self.client.stop_record()
            except Exception:
                pass
            raise
        self._active = context
        self._owned_recording = True
        context["events"]["obs_start_ack"] = clock_marker()
        deadline = time.monotonic() + 1.5
        while not self.client.get_record_status().output_active:
            if time.monotonic() >= deadline:
                self.finish(status="recording_failed", error="OBS did not report active recording")
                raise RuntimeError("OBS did not report active recording after StartRecord")
            time.sleep(0.05)
        print(f"OBS recording started for {ride_name}; dispatch follows.", flush=True)

    def mark(self, name: str, monotonic_s=None):
        if self._active is None:
            return
        marker = clock_marker()
        if monotonic_s is not None:
            marker["monotonic_s"] = float(monotonic_s)
        self._active["events"][name] = marker

    def finish(self, *, status: str, error="", frames=0):
        context = self._active
        if context is None:
            return None
        self._active = None  # Ensure an error cannot cause repeated stop commands.
        context["status"] = status
        context["frames"] = frames
        if error:
            context["error"] = error
        context["events"]["obs_stop_request"] = clock_marker()
        stop_error = None
        output_path = None
        try:
            response = self.client.stop_record()
            self._owned_recording = False
            context["events"]["obs_stop_ack"] = clock_marker()
            output_path = Path(response.output_path)
            if not output_path.is_file():
                raise FileNotFoundError(f"OBS returned recording path not found: {output_path}")
            # Preserve OBS-selected container, codec and file extension. Never
            # overwrite another capture or assume OBS uses MP4.
            target = self.output_dir / f"{context['stem']}{output_path.suffix}"
            saved_path = self._name_video_when_available(output_path, target, context)
            context["video_filename"] = saved_path.name
            context["video_path"] = str(saved_path)
            print(f"OBS recording saved: {saved_path}", flush=True)
        except Exception as exc:
            stop_error = exc
            context["recording_error"] = str(exc)
            if output_path is not None:
                context["obs_output_path"] = str(output_path)
        finally:
            t = context["events"]
            if "capture_elapsed_zero" in t and "obs_start_ack" in t:
                context["approx_csv_zero_after_obs_ack_s"] = round(
                    t["capture_elapsed_zero"]["monotonic_s"] -
                    t["obs_start_ack"]["monotonic_s"], 6)
            manifest = self.output_dir / f"{context['stem']}_video_sync.json"
            manifest.write_text(json.dumps(context, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print(f"OBS timing metadata: {manifest}", flush=True)
        if stop_error is not None:
            raise RuntimeError(f"OBS recording finalization failed: {stop_error}") from stop_error
        return context.get("video_path")

    def close(self):
        if self._active is not None:
            try:
                self.finish(status="interrupted", error="Capture ended without normal finalization")
            except Exception as exc:
                print(f"WARNING: OBS cleanup failed: {exc}", file=sys.stderr)
        if self._owned_recording:
            # StopRecord may have failed or timed out. Recheck before retrying;
            # never stop a recording that predated this capture session.
            try:
                if self.client.get_record_status().output_active:
                    self.client.stop_record()
                self._owned_recording = False
            except Exception as exc:
                print(f"WARNING: OBS may still be recording: {exc}", file=sys.stderr)
        if self._saved_directory is not None:
            try:
                # Restore the user's prior OBS recording directory when done.
                self._set_directory(self._saved_directory)
            except Exception as exc:
                print(f"WARNING: could not restore prior OBS directory: {exc}", file=sys.stderr)
