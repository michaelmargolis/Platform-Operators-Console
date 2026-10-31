# angular_cues.py ver 1
"""Shared experimental NL2 attitude-to-platform cue mathematics.

Used by both the production NL2 adapter and the offline sim_player. No Qt,
filtering, kinematics, or output I/O. Candidate cues are research-only pending
physical validation. The established Euler mapping remains in the adapter;
all algorithms use its existing angle/sign conventions:
NL2 coordinates are X lateral, Y up, Z longitudinal; q rotates body to world.

These cues are research candidates, not validated motion-cueing algorithms.
"""
from __future__ import annotations

import csv
import math
from pathlib import Path

EULER = "euler"
SINE = "sine"
GRAVITY = "gravity"
MODES = (EULER, SINE, GRAVITY)
MODE_LABELS = {
    EULER: "Euler (current POC)",
    SINE: "Sinusoidal Roll",
    GRAVITY: "Quaternion gravity vector",
}


def normalize_quaternion(quat):
    if len(quat) != 4 or not all(math.isfinite(float(v)) for v in quat):
        raise ValueError("Invalid/nonfinite NL2 quaternion")
    x, y, z, w = map(float, quat)
    magnitude = math.sqrt(x*x + y*y + z*z + w*w)
    if magnitude < 1.e-10:
        raise ValueError("NL2 quaternion magnitude is zero")
    return x/magnitude, y/magnitude, z/magnitude, w/magnitude


def gravity_up_local(quat):
    """World-up unit vector expressed in the coaster's local coordinates.

    This is row Y of the quaternion body-to-world rotation matrix, so yaw
    about world up has no effect. It is continuous even during inversions.
    """
    x, y, z, w = normalize_quaternion(quat)
    lateral = 2.0 * (x*y + w*z)
    vertical = 1.0 - 2.0 * (x*x + z*z)
    longitudinal = 2.0 * (y*z - w*x)
    return lateral, vertical, longitudinal


def map_angular_cue(axes, quat, mode, *, roll_gain, pitch_gain):
    """Replace only normalized Roll/Pitch; preserve the other four axes.

    Existing Euler mode returns the recorded values byte-for-byte. The sine
    cue matches the linear Roll response for small angles; gravity mode uses
    local gravity components to avoid Euler singularities and wraps.

    roll_gain = NL2 TELEMETRY_INTENSITY_FACTOR * ROLL_SCALE / pi
    pitch_gain = NL2 TELEMETRY_INTENSITY_FACTOR * PITCH_SCALE * 0.6
    """
    if mode not in MODES:
        raise ValueError(f"Unknown experimental angular cue mode: {mode!r}")
    if len(axes) != 6:
        raise ValueError("Six normalized axes required")
    if mode == EULER:
        return tuple(axes)
    lateral, vertical, longitudinal = gravity_up_local(quat)
    result = list(axes)
    if mode == SINE:
        # NL2's current Euler Roll is atan2(lateral, vertical). Pure roll:
        # sin(roll) is smooth across +/-180 degrees. At pitch singularity,
        # Euler Roll is undefined, which is a limitation of this candidate.
        angle = math.atan2(lateral, vertical)
        result[3] = -float(roll_gain) * math.sin(angle)
        # Retain the original Euler Pitch so the two methods isolate Roll.
    elif mode == GRAVITY:
        # Local gravity: for pure roll, lateral=sin(roll); for pure pitch,
        # longitudinal=-sin(pitch). Same small-angle signs as POC.
        result[3] = -float(roll_gain) * lateral
        result[4] = float(pitch_gain) * longitudinal
    # Attitude-derived values can never exceed the axis's normal +/-1 range.
    result[3] = max(-1.0, min(1.0, result[3]))
    result[4] = max(-1.0, min(1.0, result[4]))
    return tuple(result)


def remap_capture_rows(csv_path, rows, mode, *, roll_gain, pitch_gain):
    """Prepare existing analyser input tuples, preserving timestamp/heave.

    Euler mode does not even read quaternion data, maintaining compatibility
    with older recordings. Other modes require the capture's real quaternions.
    """
    if mode == EULER:
        return rows
    if mode not in MODES:
        raise ValueError(f"Invalid cue mode: {mode}")
    modified = []
    with Path(csv_path).open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        fields = ("quat_x", "quat_y", "quat_z", "quat_w")
        if not reader.fieldnames or any(k not in reader.fieldnames for k in fields):
            raise ValueError("Selected angular cue requires quat_x/y/z/w in the capture")
        for n, (source, row) in enumerate(zip(reader, rows), start=2):
            try:
                quaternion = tuple(float(source[k]) for k in fields)
                new_axes = map_angular_cue(
                    row[2], quaternion, mode,
                    roll_gain=roll_gain, pitch_gain=pitch_gain,
                )
            except (ValueError, TypeError, KeyError) as exc:
                raise ValueError(f"Invalid quaternion at CSV line {n}: {exc}") from exc
            modified.append((row[0], row[1], new_axes, *row[3:]))
        if len(modified) != len(rows):
            raise ValueError("Mismatch between quaternion rows and telemetry frames")
    return modified
