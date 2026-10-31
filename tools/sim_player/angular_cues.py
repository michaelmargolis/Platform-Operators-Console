# angular_cues.py ver 1
"""Compatibility re-export of POC NL2 angular mapping (one shared implementation)."""
from sims.nolimits2.angular_cues import (
    EULER, SINE, GRAVITY, MODES, MODE_LABELS,
    normalize_quaternion, gravity_up_local, map_angular_cue, remap_capture_rows,
)
