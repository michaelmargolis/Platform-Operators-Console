# dynamic_source.py ver 4
"""Qt-free helpers for Ride Analyzer dynamic-source capture.

The actual NoLimits capture implementation remains in ``tools.nl2_timing_study``.
This module only defines Ride Analyzer terminology, plans, command construction,
and discovery of completed player-compatible source recordings.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
import sys
from typing import Iterable

from sims.nolimits2 import ride_config


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_ROOT = ROOT / "captures" / "dynamic_source"
PRESET_REPEATS = {"quick": 3, "standard": 3, "extended": 5}
QUICK_RIDES = ("blackbird", "fenrir", "tiltcoaster")
SCOPE_KEYS = ("current", "selected", "quick", "standard", "extended")


@dataclass(frozen=True)
class DynamicSourceRide:
    key: str
    name: str


@dataclass(frozen=True)
class DynamicSourcePlan:
    """One requested dynamic-study capture run."""

    scope: str
    rides: tuple[str, ...] = ()
    repeats: int = 3
    warmups: int = 0
    conditions: str = ""
    extend_uncertain: bool = False
    output_root: Path = DEFAULT_OUTPUT_ROOT

    def validate(self) -> None:
        if self.scope not in SCOPE_KEYS:
            raise ValueError(f"Unknown dynamic-study scope: {self.scope!r}")
        if self.scope in ("current", "selected") and not self.rides:
            raise ValueError("Select at least one ride for this dynamic study")
        if not 1 <= int(self.repeats) <= 10:
            raise ValueError("Measured recordings must be between 1 and 10")
        if not 0 <= int(self.warmups) <= 5:
            raise ValueError("Warm-up recordings must be between 0 and 5")

    def command_arguments(self) -> list[str]:
        """Arguments following the Python executable.

        Keep the internal module name for compatibility. Ride Analyzer supplies
        its own human-facing terminology and output location.
        """
        self.validate()
        args = ["-m", "tools.nl2_timing_study", "capture"]
        if self.scope == "quick":
            # Keep the established short diagnostic set and accurate preset provenance.
            args += ["--preset", "quick"]
        elif self.scope in ("standard", "extended"):
            # Ride Analyzer studies follow the current operator-visible park set,
            # not the timing-study tool's historical deferred-park preset.  Pass
            # the matching preset as provenance while overriding its ride list.
            ride_keys = operator_available_ride_keys()
            if not ride_keys:
                raise ValueError("No operator-available NoLimits rides are configured")
            args += ["--preset", self.scope, "--rides", ",".join(ride_keys)]
        else:
            args += ["--rides", ",".join(self.rides)]
        args += ["--repeats", str(int(self.repeats)),
                 "--warmups", str(int(self.warmups)),
                 "--output", str(Path(self.output_root))]
        if self.conditions.strip():
            args += ["--conditions", self.conditions.strip()]
        if self.extend_uncertain:
            args.append("--extend-uncertain")
        return args



_SCOPE_PROVENANCE = {
    "current": "current",
    "selected": "custom",
    "quick": "quick",
    "standard": "standard",
    "extended": "extended",
}
_VIRTUAL_DISPLAY_MARKERS = ("virtual", "remote display", "basic display")


def _host_details() -> dict:
    """Reuse the timing/benchmark host probe; keep imports lazy for startup."""
    from tools.nl2_sync.host import host_details
    return host_details()


def _windows_cpu_model() -> str:
    """Best-effort friendly Windows CPU name, without another dependency."""
    if sys.platform != "win32":
        return ""
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
        ) as key:
            return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
    except (ImportError, OSError, TypeError, ValueError):
        return ""


def default_test_conditions() -> str:
    """Build the default descriptive study conditions for the local PC.

    The timing-study stack already probes Windows display controllers.  Reuse
    that information here and supplement its generic Windows processor string
    with the friendly registry CPU model where available.  Failure to obtain
    hardware details never prevents a study.
    """
    try:
        info = _host_details() or {}
    except Exception:
        info = {}
    cpu = _windows_cpu_model() or str(info.get("cpu_model") or info.get("processor") or "").strip()
    controllers = info.get("display_controllers") or ()
    if isinstance(controllers, dict):
        controllers = (controllers,)
    names = []
    for controller in controllers:
        if not isinstance(controller, dict):
            continue
        name = str(controller.get("Name") or "").strip()
        if name and name not in names:
            names.append(name)
    physical = [
        name for name in names
        if not any(marker in name.casefold() for marker in _VIRTUAL_DISPLAY_MARKERS)
    ]
    gpu_names = physical
    parts = ["Normal deployed configuration"]
    if cpu:
        parts.append(f"CPU: {cpu}")
    if gpu_names:
        parts.append("GPU: " + " / ".join(gpu_names))
    return "; ".join(parts)


def annotate_session_scope(session_dir: Path | str | None, scope: str) -> bool:
    """Add Ride Analyzer study-scope provenance after the child process exits.

    The low-level timing-study preset remains its own capture-planning field.
    This separate field records the Analyzer workflow, including Current and
    Custom scopes that are not timing-study presets.
    """
    if session_dir is None or scope not in _SCOPE_PROVENANCE:
        return False
    path = Path(session_dir) / "session.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        data["study_scope"] = _SCOPE_PROVENANCE[scope]
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
        return True
    except (OSError, ValueError, TypeError):
        return False


@dataclass(frozen=True)
class DynamicSourceDurationEstimate:
    seconds: float
    missing: tuple[str, ...] = ()
    fallback: tuple[str, ...] = ()


def plan_ride_keys(plan: DynamicSourcePlan) -> tuple[str, ...]:
    """Resolve the ride keys represented by a dynamic-study plan."""
    if plan.scope == "quick":
        return tuple(key for key in QUICK_RIDES if key not in ride_config.POC_HIDDEN_PARKS)
    if plan.scope in ("standard", "extended"):
        return operator_available_ride_keys()
    return tuple(plan.rides)


def typical_ride_duration_s(park_key: str) -> tuple[float | None, str | None]:
    """Return an approximate full-capture duration for one ride.

    Prefer the installed canonical ride trajectory because it represents the
    local approved reference recording.  If canonical media is not installed
    for that ride, fall back to the conservative duration hint already used by
    the timing-study capture tool.  Duration lookup never blocks capture.
    """
    try:
        from .canonical_media import load_media
        media = load_media(park_key)
        if media is not None and len(media.trajectory.time) >= 2:
            seconds = float(media.trajectory.time[-1] - media.trajectory.time[0])
            if math.isfinite(seconds) and seconds > 0:
                return seconds, "canonical"
    except Exception:
        pass
    try:
        from tools.nl2_timing_study.presets import DURATION_HINTS
        seconds = float(DURATION_HINTS.get(park_key, 0.0))
        if math.isfinite(seconds) and seconds > 0:
            return seconds, "timing-study hint"
    except Exception:
        pass
    return None, None


def estimate_plan_duration(plan: DynamicSourcePlan) -> DynamicSourceDurationEstimate:
    """Estimate ride-running time for the requested capture sequence.

    Loading/reset/retry delays and optional uncertainty extensions are not
    predictable and are deliberately excluded.  Warm-ups are included when
    requested because they consume the same ride-running time.
    """
    keys = plan_ride_keys(plan)
    count = int(plan.repeats) + int(plan.warmups)
    total = 0.0
    missing = []
    fallback = []
    for key in keys:
        seconds, source = typical_ride_duration_s(key)
        if seconds is None:
            missing.append(key)
            continue
        total += seconds * count
        if source != "canonical":
            fallback.append(key)
    return DynamicSourceDurationEstimate(total, tuple(missing), tuple(fallback))


def capture_start_event(text: str) -> tuple[str, int, bool] | None:
    """Return (park_key, repeat, warmup) for a capture-tool ride-start line."""
    match = re.match(r"^(MEASURED|WARMUP) (.+) (\d+)$", str(text).strip())
    if not match:
        return None
    park_key = ride_config.park_key_for_capture(match.group(2))
    if not park_key:
        return None
    return park_key, int(match.group(3)), match.group(1) == "WARMUP"

def available_rides() -> tuple[DynamicSourceRide, ...]:
    """Return the current operator-available NoLimits parks.

    Ride Analyzer dynamic studies deliberately follow the POC operator-hidden
    list. Historical timing-study deferred-park rules do not apply here.
    """
    hidden = set(getattr(ride_config, "POC_HIDDEN_PARKS", getattr(ride_config, "OPERATOR_HIDDEN_PARKS", ())))
    return tuple(
        DynamicSourceRide(key, info["name"])
        for key, info in ride_config.PARKS.items()
        if key not in hidden
    )


def operator_available_ride_keys() -> tuple[str, ...]:
    """Stable keys for every ride included by Standard/Extended dynamic studies."""
    return tuple(ride.key for ride in available_rides())


def park_key_for_recording(recording) -> str | None:
    """Resolve a loaded RideRecording to the stable NL2 park key."""
    if recording is None:
        return None
    try:
        return ride_config.park_key_for_capture(recording.ride)
    except (AttributeError, KeyError, TypeError, ValueError):
        return None


def translated_capture_output(text: str) -> str:
    """Present the shared timing-capture tool using Ride Analyzer terminology."""
    line = str(text).rstrip("\r\n")
    line = line.replace("Timing study:", "Dynamic study:")
    line = line.replace("STUDY ERROR:", "DYNAMIC STUDY ERROR:")
    line = line.replace("Stopped. Partial capture is retained", "Stopped. Partial dynamic study is retained")
    line = re.sub(r"^(\d+) parks x (\d+) measured runs; (\d+) warmups per park\.$",
                  r"\1 rides x \2 dynamic source recordings; \3 warm-ups per ride.", line)
    line = re.sub(r"^MEASURED (.+) (\d+)$", r"DYNAMIC SOURCE \1 \2", line)
    line = re.sub(r"^(COMPLETE|INCOMPLETE): (.+) repeat (\d+):",
                  r"\1: \2 dynamic source \3:", line)
    return line


def session_path_from_output(text: str) -> Path | None:
    """Extract the newly created study directory from capture-tool output."""
    match = re.search(r"; output (.+)$", str(text).strip())
    return Path(match.group(1).strip()) if match else None


def report_path_from_output(text: str) -> Path | None:
    line = str(text).strip()
    if not line.startswith("Report:"):
        return None
    value = line.partition(":")[2].strip()
    return Path(value) if value else None


def _trial_recording(trial: Path) -> tuple[str, int, Path] | None:
    try:
        data = json.loads(trial.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError):
        return None
    if data.get("status") != "complete" or data.get("warmup"):
        return None
    files = data.get("files") or {}
    capture_name = files.get("capture")
    if not capture_name:
        return None
    capture = trial.parent / str(capture_name)
    if not capture.is_file():
        return None
    return str(data.get("ride") or trial.parent.name), int(data.get("repeat") or 0), capture


def source_recordings(session_dir: Path | str | None) -> tuple[Path, ...]:
    """Return complete, measured, player-compatible recordings from a study."""
    if session_dir is None:
        return ()
    root = Path(session_dir)
    if not root.is_dir():
        return ()
    found = []
    for trial in root.rglob("*_trial.json"):
        item = _trial_recording(trial)
        if item is not None:
            found.append(item)
    found.sort(key=lambda item: (item[0].casefold(), item[1], str(item[2]).casefold()))
    return tuple(item[2] for item in found)


def readable_ride_list(keys: Iterable[str]) -> str:
    names = []
    for key in keys:
        info = ride_config.PARKS.get(str(key))
        names.append(info["name"] if info else str(key))
    return ", ".join(names)
