# tuning.py ver 2
"""Headless sim_player motion-tuning helpers (research only).

Multipliers are relative to POC's current saved per-axis gains, and applied
before POC's existing washout processing. No POC configuration is modified.
The source CSV always retains the six original normalized axes.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path

from .angular_cues import EULER, MODES
from .metrics import measure_recording

AXIS_NAMES = ("Surge", "Sway", "Heave", "Roll", "Pitch", "Yaw")
DEFAULT_GAINS = (100, 100, 100, 100, 100, 100)


def validate_gain_percent(values):
    """Six finite experimental percentages 0..200 (100 = saved gain)."""
    try:
        values = tuple(values)
    except TypeError as exc:
        raise ValueError("Exactly six gain percentages are required") from exc
    if len(values) != 6:
        raise ValueError("Exactly six gain percentages are required")
    result = []
    for axis, value in zip(AXIS_NAMES, values):
        if isinstance(value, bool):
            raise ValueError(f"{axis} gain must be numeric")
        try:
            number = float(value)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"{axis} gain must be numeric") from exc
        if not math.isfinite(number) or not 0.0 <= number <= 200.0 or not number.is_integer():
            raise ValueError(f"{axis} gain must be a whole percentage between 0 and 200%")
        result.append(number)
    return tuple(result)


def scale_capture_rows(rows, gain_percent):
    """Gain multiplication occurs before POC's saved gains and washout.

    Exact for the sim_player's Recorded-heave mode: linear gain multiplication
    commutes with the existing saved per-axis gain. No heave/kinematics math is
    reimplemented. CSV frame time, height and optional vertical-G are retained.
    """
    gain = validate_gain_percent(gain_percent)
    if gain == DEFAULT_GAINS:
        return rows
    return [
        (row[0], row[1], tuple(float(a) * g / 100.0 for a, g in zip(row[2], gain)), *row[3:])
        for row in rows
    ]


def muscle_flags(frame, lo, hi):
    """Per-channel (requested outside working range, final length clamped).

    A final permitted length is normally inside the legal range even if the
    *request* was impossible. Both conditions must remain visible in the UI.
    """
    requested = frame.analysis.get("requested", ())
    workspace = frame.analysis.get("workspace_lengths", ())
    permitted = frame.muscle_lengths_mm
    if len(requested) != 6 or len(workspace) != 6 or len(permitted) != 6:
        return (False,) * 6, (False,) * 6
    out = tuple(float(v) < lo - 1.e-7 or float(v) > hi + 1.e-7 for v in requested)
    clipped = tuple(abs(float(v) - float(p)) > 1.e-7
                    for v, p in zip(workspace, permitted))
    return out, clipped


@dataclass(frozen=True)
class TuningMetrics:
    peak_step_mm: float
    min_clearance_mm: float | None
    workspace_frames: int
    requested_oob_frames: int
    final_clipped_frames: int
    worst_requested_excess_mm: float
    frames: int
    short_clip_frames: int = 0
    long_clip_frames: int = 0
    worst_short_clip_mm: float = 0.0
    worst_long_clip_mm: float = 0.0
    initial_muscle_min_mm: float | None = None
    initial_muscle_max_mm: float | None = None


def tuning_metrics(recording):
    basic = measure_recording(recording)
    oob_frames = clamp_frames = short_frames = long_frames = 0
    max_excess = max_short = max_long = 0.0
    lo, hi = recording.min_length_mm, recording.max_length_mm
    for frame in recording.frames:
        req = tuple(frame.analysis.get("requested", ()))
        workspace = tuple(frame.analysis.get("workspace_lengths", ()))
        if len(req) == 6:
            extra = max(max(lo - float(v), float(v) - hi, 0.0) for v in req)
            oob_frames += extra > 1.e-7
            max_excess = max(max_excess, extra)
        if len(workspace) == 6:
            clamp_frames += any(abs(float(w) - float(f)) > 1.e-7
                                for w, f in zip(workspace, frame.muscle_lengths_mm))
            short = max(lo - float(v) for v in workspace)
            long = max(float(v) - hi for v in workspace)
            short_frames += short > 1.e-7
            long_frames += long > 1.e-7
            max_short = max(max_short, short)
            max_long = max(max_long, long)
    return TuningMetrics(
        peak_step_mm=basic.peak_step_mm,
        min_clearance_mm=basic.min_clearance_mm,
        workspace_frames=basic.workspace_frames,
        requested_oob_frames=oob_frames,
        final_clipped_frames=clamp_frames,
        worst_requested_excess_mm=max_excess,
        frames=basic.samples,
        short_clip_frames=short_frames,
        long_clip_frames=long_frames,
        worst_short_clip_mm=max_short,
        worst_long_clip_mm=max_long,
        initial_muscle_min_mm=(min(recording.frames[0].muscle_lengths_mm)
                               if recording.frames else None),
        initial_muscle_max_mm=(max(recording.frames[0].muscle_lengths_mm)
                               if recording.frames else None),
    )


@dataclass(frozen=True)
class ExperimentSettings:
    cue_mode: str = EULER
    gain_percent: tuple[float, ...] = DEFAULT_GAINS
    step_warning_mm: float = 50.0
    clearance_warning_mm: float = 30.0
    variant_label: str | None = None
    variant_snapshot: dict | None = None
    global_settings: dict | None = None
    platform_key: str | None = None
    platform_config_module: str | None = None
    platform_config_sha256: str | None = None

    def __post_init__(self):
        if self.platform_key is not None:
            from config.platforms import PLATFORMS
            if self.platform_key not in PLATFORMS:
                raise ValueError(f"Unknown experiment platform: {self.platform_key!r}")
        if self.cue_mode not in MODES:
            raise ValueError("Unknown angular cue mode")
        object.__setattr__(self, "gain_percent", validate_gain_percent(self.gain_percent))
        if not math.isfinite(self.step_warning_mm) or self.step_warning_mm <= 0:
            raise ValueError("Step warning must be positive and finite")
        if not math.isfinite(self.clearance_warning_mm) or self.clearance_warning_mm < 0:
            raise ValueError("Clearance warning must be non-negative and finite")
        if self.variant_label is not None and (not isinstance(self.variant_label, str) or not self.variant_label):
            raise ValueError("Variant label must be a nonempty string")
        if self.variant_snapshot is not None and not isinstance(self.variant_snapshot, dict):
            raise ValueError("Variant snapshot must be a dictionary")
        if self.global_settings is not None and not isinstance(self.global_settings, dict):
            raise ValueError("Global settings snapshot must be a dictionary")


def save_experiment(path, settings: ExperimentSettings):
    """Separate study settings; never write to POC configs or source captures."""
    dest = Path(path)
    value = {
        "schema": "sim_player_experiment_v3",
        "platform_key": settings.platform_key,
        "platform_config_module": settings.platform_config_module,
        "platform_config_sha256": settings.platform_config_sha256,
        "variant_label": settings.variant_label,
        "variant_snapshot": settings.variant_snapshot,
        "poc_global_settings_snapshot": settings.global_settings,
        "angular_cue": settings.cue_mode,
        "relative_gains_percent": list(settings.gain_percent),
        "anomaly_step_warning_mm": settings.step_warning_mm,
        "anomaly_clearance_warning_mm": settings.clearance_warning_mm,
        "comment": "Research only; 100% per axis inherits the current POC gain",
    }
    dest.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    return dest


def load_experiment(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") not in (
            "sim_player_experiment_v1", "sim_player_experiment_v2", "sim_player_experiment_v3"):
        raise ValueError("Not a sim_player experiment file")
    return ExperimentSettings(
        cue_mode=data["angular_cue"],
        gain_percent=tuple(data["relative_gains_percent"]),
        step_warning_mm=float(data["anomaly_step_warning_mm"]),
        clearance_warning_mm=float(data["anomaly_clearance_warning_mm"]),
        variant_label=data.get("variant_label"),
        variant_snapshot=data.get("variant_snapshot"),
        global_settings=data.get("poc_global_settings_snapshot"),
        platform_key=data.get("platform_key"),
        platform_config_module=data.get("platform_config_module"),
        platform_config_sha256=data.get("platform_config_sha256"),
    )


def current_poc_global_settings():
    """Snapshot current simulator-wide values; never save into the INI here."""
    from core.telemetry_config import TelemetryConfig
    path = Path(__file__).resolve().parents[2] / "sims/nolimits2/telemetry_config.ini"
    return TelemetryConfig(path).snapshot()

