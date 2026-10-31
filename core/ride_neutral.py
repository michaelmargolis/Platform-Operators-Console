# ride_neutral.py ver 1
"""Validated, ride-only physical-Z offset shared by POC and offline analysis.

Positive height raises the suspended platform; negative lowers it. These are
research adjustment bounds, not an assertion that every pose is physically
feasible: the selected platform/geometry must also validate neutral clearance.
"""
from __future__ import annotations

import math

MAX_RIDE_NEUTRAL_DELTA_MM = 100.0


def validated_ride_neutral_delta(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("neutral_height_delta_mm must be a number (mm)")
    result = float(value)
    if not math.isfinite(result) or abs(result) > MAX_RIDE_NEUTRAL_DELTA_MM:
        raise ValueError(
            f"neutral_height_delta_mm must be finite within "
            f"+/-{MAX_RIDE_NEUTRAL_DELTA_MM:g} mm")
    return result


def real_heave_for_physical_height(delta_mm, flip_z):
    """Return the real-transform Z component for a physical height change."""
    flip = float(flip_z)
    if not math.isfinite(flip) or abs(flip) < 1.0e-12:
        raise ValueError("Invalid kinematics Heave axis convention")
    return validated_ride_neutral_delta(delta_mm) / flip
