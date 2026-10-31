# anomaly_detection.py ver 1
"""Headless kinematic anomaly detection for precomputed sim_player frames.

Reads the *existing POC analyser results*. Never solves kinematics, applies
filters, or alters the source capture. Consecutive anomalous samples form one
interval, making the timeline usable even during a long workspace intervention.
The event type can later be extended for a separate tension-feasibility solver.
"""
from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class AnomalyThresholds:
    muscle_step_mm: float = 50.0
    low_clearance_mm: float = 30.0

    def __post_init__(self):
        if not math.isfinite(self.muscle_step_mm) or self.muscle_step_mm <= 0:
            raise ValueError("Muscle step threshold must be positive and finite")
        if not math.isfinite(self.low_clearance_mm) or self.low_clearance_mm < 0:
            raise ValueError("Clearance threshold must be non-negative and finite")


@dataclass(frozen=True)
class AnomalyEvent:
    category: str
    start_s: float
    end_s: float
    seconds: float       # time of most significant sample, for seek/markers
    name: str
    color: str
    details: str
    muscles: str
    magnitude: float
    units: str
    frame_count: int
    severity: str

    @property
    def duration_s(self):
        return self.end_s - self.start_s


# Shared category registry. A future tension solver can emit the same event
# interface, but its force/feasibility results remain a separate category.
CATEGORIES = {
    "requested_range": ("Requested outside range", "#ef8a62", "warning", "mm"),
    "workspace_limited": ("Workspace limited", "#e8ad65", "warning", "%"),
    "final_clipped": ("Final length clipped", "#ef6d65", "alert", "mm"),
    "muscle_step": ("Large muscle step", "#f36b6b", "alert", "mm"),
    "low_clearance": ("Low applied clearance", "#f0b058", "warning", "mm"),
    "requested_floor": ("Requested floor violation", "#e77c9b", "warning", "mm"),
}


def _number(value):
    try:
        value = float(value)
    except (ValueError, TypeError):
        return None
    return value if math.isfinite(value) else None


def _group_samples(rows, category, duration):
    """Each row is (time, magnitude, affected-muscle tuple, details).

    A None row closes an interval. Very large capture timestamp gaps also
    split intervals, so an interrupted capture does not create a long event.
    """
    label, color, severity, units = CATEGORIES[category]
    events = []
    current = []

    def flush():
        if not current:
            return
        peak = max(current, key=lambda row: row[1])
        start_s = current[0][0]
        end_s = min(float(duration), current[-1][0])
        muscles = sorted(set(m for row in current for m in row[2]),
                         key=lambda m: int(m[1:]) if m[1:].isdigit() else 99)
        detail = peak[3]
        if len(current) > 1:
            detail += f"; {len(current)} frames / {end_s-start_s:.2f} s"
        events.append(AnomalyEvent(
            category=category, start_s=start_s, end_s=end_s,
            seconds=peak[0], name=label, color=color, details=detail,
            muscles=", ".join(muscles) if muscles else "—",
            magnitude=peak[1], units=units,
            frame_count=len(current), severity=severity,
        ))
        current.clear()

    previous_time = None
    for row in rows:
        if row is None:
            flush()
            previous_time = None
            continue
        if previous_time is not None and row[0] - previous_time > 0.25:
            flush()
        current.append(row)
        previous_time = row[0]
    flush()
    return events


def detect_anomalies(recording, thresholds=AnomalyThresholds()):
    """Return time-sorted anomaly episodes based on already analysed frames.

    Compare requested lengths against the platform's full working range;
    distinguish *request* violations from final output clipping. Step sizes
    use successive final permitted muscle lengths, not raw requests. The
    threshold is per telemetry sample (not a physical actuator speed limit).
    Applied floor clearance below the threshold is a warning, not a change to
    POC's floor-clearance constraints.
    """
    frames = recording.frames
    if not frames:
        return ()
    lo, hi = float(recording.min_length_mm), float(recording.max_length_mm)
    duration = float(recording.duration)
    rows = {category: [] for category in CATEGORIES}
    prior = None
    for frame in frames:
        info = frame.analysis
        t = float(frame.seconds)
        requested = tuple(info.get("requested", ()))
        out = [(f"M{i+1}", max(lo-float(v), float(v)-hi, 0.0))
               for i, v in enumerate(requested)]
        violating = [(name, excess) for name, excess in out if excess > 1e-7]
        if violating:
            muscle, excess = max(violating, key=lambda p: p[1])
            rows["requested_range"].append((
                t, excess, tuple(name for name, _ in violating),
                f"{muscle} exceeds permitted length by {excess:.1f} mm (request before limiting)"))
        else:
            rows["requested_range"].append(None)

        if bool(info.get("workspace_limited", frame.workspace_limited)):
            scale = _number(info.get("scale"))
            reduction_pct = max(0.0, (1.0-scale)*100) if scale is not None else 0.0
            reason = str(info.get("reason") or "workspace constraints")
            rows["workspace_limited"].append((
                t, reduction_pct, tuple(name for name, _ in violating),
                f"Workspace reduced pose {reduction_pct:.1f}% ({reason})"))
        else:
            rows["workspace_limited"].append(None)

        unclamped = info.get("workspace_lengths", ())
        permitted = frame.muscle_lengths_mm
        excesses = [(f"M{i+1}", abs(float(v)-float(p)))
                    for i, (v, p) in enumerate(zip(unclamped, permitted))]
        clipped = [(name, excess) for name, excess in excesses if excess > 1e-7]
        if clipped:
            muscle, excess = max(clipped, key=lambda p: p[1])
            rows["final_clipped"].append((
                t, excess, tuple(name for name, _ in clipped),
                f"{muscle} final clamp {excess:.1f} mm after workspace processing"))
        else:
            rows["final_clipped"].append(None)

        if prior is not None:
            diffs = [(f"M{i+1}", abs(float(a)-float(b)))
                     for i, (a, b) in enumerate(zip(permitted, prior.muscle_lengths_mm))]
            large = [(name, delta) for name, delta in diffs
                     if delta > thresholds.muscle_step_mm + 1e-9]
        else:
            large = []
        if large:
            muscle, delta = max(large, key=lambda p: p[1])
            delta_t = t-float(prior.seconds)
            rows["muscle_step"].append((
                t, delta, tuple(name for name, _ in large),
                f"{muscle} changed {delta:.1f} mm in {delta_t*1000:.0f} ms (final command)"))
        else:
            rows["muscle_step"].append(None)

        clearance = _number(frame.modeled_clearance_mm)
        if clearance is not None and clearance < thresholds.low_clearance_mm:
            # Rank lowest clearance highest, while displaying the true value.
            rows["low_clearance"].append((
                t, thresholds.low_clearance_mm - clearance, (),
                f"Applied clearance {clearance:.1f} mm (warning below {thresholds.low_clearance_mm:g} mm)"))
        else:
            rows["low_clearance"].append(None)

        if bool(info.get("below_floor", False)):
            requested_floor = _number(info.get("requested_floor"))
            rows["requested_floor"].append((
                t, 1.0, (),
                (f"Requested floor clearance {requested_floor:.1f} mm violated POC limit"
                 if requested_floor is not None else "Requested pose violated POC floor limit")))
        else:
            rows["requested_floor"].append(None)
        prior = frame

    events = []
    for category, values in rows.items():
        events.extend(_group_samples(values, category, duration))
    return tuple(sorted(events, key=lambda e: (e.seconds, e.category)))
