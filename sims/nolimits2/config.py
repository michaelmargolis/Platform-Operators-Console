#config.py  ver 18
# Simple NoLimits2 prototype configuration.

PORT = 15151
SERVICE_PERIOD_MS = 50
RECONNECT_PERIOD_S = 1.0
STATION_POLL_PERIOD_S = 0.25
TELEMETRY_STALE_S = 0.5
STATIONARY_SPEED_MPS = 0.1

# Initial fan response reproduces the legacy suspended-chair controller.
# The table output is interpolated, then scaled into threshold_power..max_power.
FAN_CONFIG = {
    "speed_at_100": 40.0,
    "threshold_speed": 1.4,
    "threshold_power": 13.0,
    "max_power": 70.0,
    "speed_power_table": (
        (0.0, 8.0),
        (2.5, 14.0),
        (5.0, 25.0),
        (10.0, 39.0),
        (20.0, 50.0),
        (30.0, 75.0),
        (40.0, 100.0),
    ),
}
INITIAL_LIFT_HEIGHT_M = 32.0
TELEMETRY_INTENSITY_FACTOR = 0.4

# Restore the angular response of the legacy NoLimits2 Transform(gain=0.6).
# The current NL2 adapter applies TELEMETRY_INTENSITY_FACTOR to every ordinary
# channel AFTER converting attitude to normalized Roll/Pitch. These factors
# compensate only those two axes so the effective gains are once again:
#     Roll:  -angle / pi * 0.6
#     Pitch: -angle * 0.6
# They apply before the final [-1, +1] clamp, and do not change the sign fix,
# other axes, downstream POC operator intensity, gains, or workspace limits.
ROLL_SCALE = 1.5
PITCH_SCALE = 2.5

# Session-only NL2 heave choices. Keep the established legacy cue as the
# startup default; the operator can also choose Vertical G or Normalized Height.
HEAVE_MODE_LEGACY_HEIGHT = "legacy_height"
HEAVE_MODE_VERTICAL_G = "vertical_g"
HEAVE_MODE_NORMALIZED_HEIGHT = "normalized_height"
HEAVE_MODE = HEAVE_MODE_NORMALIZED_HEIGHT

# Park data/calibrations belong to ride_config.py. These aliases keep the
# standalone capture/analyzer tools and existing configuration tests compatible.
from sims.nolimits2 import ride_config

NORMALIZED_HEIGHT_DEFAULT_AMPLITUDE = 0.80
NORMALIZED_HEIGHT_PROFILES = {
    park["name"]: dict(park["normalized_height"])
    for park in ride_config.PARKS.values() if "normalized_height" in park
}
LEGACY_HEIGHT_PROFILES = {
    park["name"]: dict(park["legacy_height"])
    for park in ride_config.PARKS.values() if "legacy_height" in park
}

# SOC supplies yaw angular velocity directly. NL2 supplies orientation only,
# so the adapter derives the equivalent local vertical-axis angular velocity
# from consecutive quaternions. This gain matches the SOC airborne yaw factor.
YAW_RATE_GAIN = 2.0
# Differentiating NL2 orientation magnifies small frame-to-frame changes. This
# first-order low-pass time constant smooths the derived yaw rate before gain
# and clipping, while retaining prompt response to a real turn.
YAW_RATE_LOW_PASS_TAU_S = 0.25

# The physical park list (one park per entry) is retained for the recorder.
# The POC dropdown gets variant labels from the selected platform JSON instead.
BASE_RIDE_PATH = ride_config.BASE_RIDE_PATH
RIDES = ride_config.park_rides()
DEFAULT_VARIANT_LABEL = ride_config.DEFAULT_VARIANT_LABEL
DEFAULT_RIDE_INDEX = 0  # compatibility; POC resolves preferred/default label in its store

# Connection/API health.  TCP connection and NL2 API connection are separate:
# after TCP connects, the adapter sends GET_VERSION immediately and treats a
# valid NL2 response as proof that the simulator datalink is alive.
CONNECT_TIMEOUT_S = 1.0
API_PROBE_PERIOD_S = 1.0
API_RESPONSE_STALE_S = 3.0

# Compatibility only. New settings belong in the selected platform JSON.
# Historical tests/utilities may monkeypatch this legacy dictionary in-session.
RIDE_ANGULAR_WASHOUT = {}


def angular_washout_overrides(ride_name):
    overrides = RIDE_ANGULAR_WASHOUT.get(ride_name)
    if overrides is None and ride_name not in RIDE_ANGULAR_WASHOUT:
        overrides = ride_config.angular_washout_overrides(ride_name)
    if (not isinstance(overrides, (tuple, list)) or len(overrides) != 2
            or any(v is not None and type(v) is not bool for v in overrides)):
        raise ValueError(f"Invalid Roll/Pitch washout policy for {ride_name!r}: {overrides!r}")
    return tuple(overrides)


# Per-simulator washout configuration.  The common POC motion pipeline will
# consume these per-axis values; they are intentionally not embedded in the
# NL2 protocol/state adapter.
