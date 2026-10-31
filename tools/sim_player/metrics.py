# metrics.py ver 1
"""Pure numerical per-ride diagnostics for experimental cue comparisons."""
from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class RideMetrics:
    samples: int
    peak_step_mm: float
    steps_over_10mm: int
    workspace_frames: int
    min_clearance_mm: float | None
    max_abs_roll_pct: float
    max_abs_pitch_pct: float


def measure_recording(recording):
    frames = recording.frames
    previous = None
    peak = 0.0
    over10 = 0
    limited = 0
    floor = None
    peak_roll = peak_pitch = 0.0
    for frame in frames:
        lengths = frame.muscle_lengths_mm
        if previous is not None:
            step = max(abs(a-b) for a,b in zip(lengths, previous))
            peak = max(peak, step)
            over10 += step > 10.0 + 1e-9
        previous = lengths
        limited += bool(frame.workspace_limited)
        clearance = frame.modeled_clearance_mm
        if clearance is not None and math.isfinite(clearance):
            floor = clearance if floor is None else min(floor, clearance)
        peak_roll = max(peak_roll, abs(frame.processed_axes[3])*100)
        peak_pitch = max(peak_pitch, abs(frame.processed_axes[4])*100)
    return RideMetrics(len(frames), peak, over10, limited, floor, peak_roll, peak_pitch)
