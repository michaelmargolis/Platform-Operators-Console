"""Recording and analysis adapter for sim_player.

No Qt, network, SHS or Festo dependencies. Reuses the existing POC offline
analyser for identical kinematics, workspace limiting and telemetry filters.
The analysis result deliberately retains the full per-frame POC pose, so a
future optional tension solver can consume it without changes to the UI.
"""
from __future__ import annotations

from bisect import bisect_left
import csv
from dataclasses import dataclass, field
from datetime import datetime
import json
import math
from pathlib import Path
from typing import Any
import sys

AXES = ("Surge", "Sway", "Heave", "Roll", "Pitch", "Yaw")
EXTENSIONS = (".mp4", ".mkv", ".mov", ".avi", ".webm")

# Only lifecycle events useful on the initial ride timeline are shown.
TIMELINE_EVENTS = {
    "ride_dispatch_requested": ("Dispatch request", "#84cbb5"),
    "view_train_dispatched": ("Dispatched", "#84cbb5"),
    "view_train_departed": ("Departed", "#84cbb5"),
    "aux_dispatch_sent": ("Other train", "#f4b66c"),
    "aux_station_cleared": ("Station clear", "#84cbb5"),
    "view_train_returned": ("Returned", "#84cbb5"),
    "arrival_station_confirmed": ("Arrival", "#84cbb5"),
    "park_reset_requested": ("Reset requested", "#b3a5e0"),
    "view_train_identity_changed": ("Train changed", "#f4b66c"),
}


@dataclass(frozen=True)
class RideEvent:
    seconds: float
    name: str
    color: str
    details: str = ""


@dataclass(frozen=True)
class PlaybackFrame:
    seconds: float
    input_axes: tuple[float, ...]
    processed_axes: tuple[float, ...]
    muscle_lengths_mm: tuple[float, ...]
    workspace_limited: bool
    modeled_clearance_mm: float | None
    # Retain POC's complete per-frame result as a future tension-solver input.
    analysis: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class RideRecording:
    csv_path: Path
    ride: str
    frames: tuple[PlaybackFrame, ...]
    times: tuple[float, ...]
    events: tuple[RideEvent, ...]
    video_path: Path | None
    sync_path: Path | None
    video_offset_s: float
    sync_source: str
    min_length_mm: float
    max_length_mm: float
    analysis_settings: str
    diagnostics: str

    @property
    def duration(self) -> float:
        return self.times[-1] if self.times else 0.0

    def nearest_index(self, seconds: float) -> int:
        if not self.times:
            return 0
        index = bisect_left(self.times, seconds)
        if index == 0:
            return 0
        if index == len(self.times):
            return len(self.times) - 1
        return index if self.times[index] - seconds < seconds - self.times[index - 1] else index - 1

    def at(self, seconds: float) -> PlaybackFrame:
        return self.frames[self.nearest_index(seconds)]


def _datetime(value):
    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


def _read_capture_clock(csv_path):
    """Return approximate wall-clock time of CSV elapsed=0 for old captures."""
    with csv_path.open(newline="", encoding="utf-8-sig") as stream:
        row = next(csv.DictReader(stream), None)
    if not row:
        return None
    observed = _datetime(row.get("wall_time"))
    if observed is None:
        return None
    try:
        from datetime import timedelta
        return observed - timedelta(seconds=float(row["elapsed_s"]))
    except (KeyError, TypeError, ValueError):
        return None


def _read_sync(path):
    if not path.exists():
        return {}, None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, OSError) as exc:
        return {}, f"Cannot read timing manifest: {exc}"
    if not isinstance(data, dict):
        return {}, "Timing manifest must be a JSON object"
    return data, None


def _event_clock_offset(manifest, csv_path):
    """Event CSV zero is earlier than ride telemetry zero; use monotonic anchors."""
    markers = manifest.get("events", {})
    if isinstance(markers, dict):
        try:
            a = float(markers["state_event_elapsed_zero"]["monotonic_s"])
            b = float(markers["capture_elapsed_zero"]["monotonic_s"])
            if math.isfinite(a) and math.isfinite(b):
                return a - b, None
        except (TypeError, KeyError, ValueError):
            pass
    # Without a manifest, use both files' local wall-clock timestamps.
    return None, _read_capture_clock(csv_path)


def _load_events(event_path, manifest, csv_path, duration):
    if not event_path.is_file():
        return (), "No matching state-event CSV"
    offset, csv_zero_wall = _event_clock_offset(manifest, csv_path)
    found = []
    try:
        with event_path.open(newline="", encoding="utf-8-sig") as stream:
            for row in csv.DictReader(stream):
                event = row.get("event", "")
                if event in TIMELINE_EVENTS:
                    name, color = TIMELINE_EVENTS[event]
                elif event == "state" and row.get("state", "").upper() == "READY":
                    name, color = "READY", "#84cbb5"
                else:
                    continue
                try:
                    if offset is not None:
                        seconds = float(row["elapsed_s"]) + offset
                    elif csv_zero_wall is not None:
                        event_wall = _datetime(row.get("wall_time"))
                        if event_wall is None:
                            continue
                        seconds = (event_wall - csv_zero_wall).total_seconds()
                    else:
                        # Unanchored timelines must not be presented as aligned.
                        continue
                except (KeyError, TypeError, ValueError):
                    continue
                if not math.isfinite(seconds) or seconds < -1.5 or seconds > duration + 1:
                    continue
                found.append(RideEvent(max(0.0, min(duration, seconds)), name, color,
                                       row.get("details", "")))
    except (OSError, UnicodeError, csv.Error) as exc:
        return (), f"State-event CSV could not be read: {exc}"
    found.sort(key=lambda item: item.seconds)
    if offset is None and csv_zero_wall is None:
        return (), "State markers hidden: no reliable event/telemetry clock alignment"
    return tuple(found), ""


def _find_video(csv_path, manifest):
    expected = manifest.get("video_path") or manifest.get("video_filename")
    if isinstance(expected, str) and expected:
        candidate = Path(expected)
        if candidate.is_file():
            return candidate, ""
        beside = csv_path.parent / candidate.name
        if beside.is_file():
            return beside, ""
    for suffix in EXTENSIONS:
        possible = csv_path.with_suffix(suffix)
        if possible.is_file():
            return possible, ""
    # Video must not be guessed by date or directory scanning; a match could be wrong.
    return None, "No matching video file (CSV playback remains available)"


def _video_offset(manifest):
    """Initial *uncalibrated* estimate of video PTS at telemetry t=0.

    OBS start acknowledgement is not first encoded video frame, so this is
    always marked approximate until the user calibrates the video alignment.
    """
    try:
        value = float(manifest["approx_csv_zero_after_obs_ack_s"])
        if math.isfinite(value) and -60.0 <= value <= 60.0:
            return value, "OBS-start estimate — adjust using video offset"
    except (KeyError, ValueError, TypeError):
        pass
    return 0.0, "No OBS clock anchor — adjust video offset manually"


def _calibration_path(csv_path):
    return csv_path.with_name(csv_path.stem + "_sim_player_sync.json")


def read_saved_calibration(csv_path):
    path = _calibration_path(csv_path)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema") != 1 or data.get("capture_csv") != csv_path.name:
            return None
        offset = float(data["video_offset_s"])
        return offset if math.isfinite(offset) and -60 <= offset <= 60 else None
    except (ValueError, TypeError, OSError, KeyError):
        return None


def write_saved_calibration(csv_path, offset_s):
    """Create a separate replay calibration sidecar; never overwrite capture files."""
    path = _calibration_path(csv_path)
    contents = {"schema": 1, "capture_csv": csv_path.name,
                "video_offset_s": round(float(offset_s), 4),
                "comment": "Video PTS at telemetry elapsed_s=0; calibrated in sim_player"}
    path.write_text(json.dumps(contents, indent=2) + "\n", encoding="utf-8")
    return path


def load_recording(csv_path, *, platform="platform_v2", heave="Recorded heave",
                   amplitude=80, intensity=100, apply_filters=True):
    """Load raw ride, compute POC-equivalent muscle commands, align events/video.

    The existing analyse_nl2_csv module is the numerical authority. This
    function only adapts its result for the UI; it does not implement another
    copy of kinematics or workspace limiting.
    """
    csv_path = Path(csv_path).resolve()
    if not csv_path.is_file():
        raise FileNotFoundError(csv_path)
    if csv_path.name.endswith("_state_events.csv"):
        raise ValueError("Select the telemetry CSV, not the state-event CSV")
    tools_dir = Path(__file__).resolve().parents[1]
    if str(tools_dir) not in sys.path:
        sys.path.insert(0, str(tools_dir))
    try:
        from analyze_nl2_csv import analyze, capture_ride_name, read_capture
    except ImportError as exc:
        raise RuntimeError("POC tools/analyze_nl2_csv.py is required for shared motion calculations") from exc

    raw_rows = read_capture(csv_path)
    ride = capture_ride_name(csv_path) or csv_path.stem
    calculated, stats = analyze(raw_rows, platform, heave, amplitude, intensity,
                                apply_filters, ride_name=ride)
    if len(raw_rows) != len(calculated):
        raise RuntimeError("Motion analysis returned a different number of frames")
    samples = tuple(PlaybackFrame(
        seconds=float(info["time"]),
        input_axes=tuple(float(v) for v in info["norm_input"]),
        processed_axes=tuple(float(v) for v in info["norm"]),
        muscle_lengths_mm=tuple(float(v) for v in info["permitted"]),
        workspace_limited=bool(info["workspace_limited"]),
        modeled_clearance_mm=(None if info["applied_floor"] is None else float(info["applied_floor"])),
        analysis=info,
    ) for info in calculated)
    times = tuple(frame.seconds for frame in samples)
    sync_path = csv_path.with_name(csv_path.stem + "_video_sync.json")
    manifest, sync_warning = _read_sync(sync_path)
    event_path = csv_path.with_name(csv_path.stem + "_state_events.csv")
    events, event_warning = _load_events(event_path, manifest, csv_path, times[-1])
    video_path, video_warning = _find_video(csv_path, manifest)
    video_offset, sync_source = _video_offset(manifest)
    calibrated = read_saved_calibration(csv_path)
    if calibrated is not None:
        video_offset = calibrated
        sync_source = "Saved user-calibrated video offset"
    problems = [p for p in (sync_warning, event_warning, video_warning) if p]
    return RideRecording(
        csv_path=csv_path, ride=ride, frames=samples, times=times,
        events=events, video_path=video_path,
        sync_path=sync_path if sync_path.is_file() else None,
        video_offset_s=video_offset, sync_source=sync_source,
        min_length_mm=float(stats["min_length"]), max_length_mm=float(stats["max_length"]),
        analysis_settings=(f"{platform} | {heave} | intensity {intensity}% | "
                           f"{'saved gains/washout' if apply_filters else 'no filters'}"),
        diagnostics="; ".join(problems),
    )
