#config.py  ver 15
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

# Calibrated from completed NL2 ride-study captures (October 2026). Minima
# and maxima are rounded slightly OUTWARD so repeated runs remain in range.
# Amplitude is a fraction of normalized heave, BEFORE POC gains and intensity.
# These are initial MDX-platform test settings, not validated dynamic limits.
# Leave incomplete/unrecorded rides out: do not guess their track heights.
NORMALIZED_HEIGHT_DEFAULT_AMPLITUDE = 0.80
NORMALIZED_HEIGHT_PROFILES = {
    "Mdx Logo Coaster": {"min_height_m": 3.2, "max_height_m": 32.7, "amplitude": 0.80},
    "Blackbird": {"min_height_m": 5.9, "max_height_m": 41.0, "amplitude": 0.90},
    "Fenrir": {"min_height_m": 2.7, "max_height_m": 45.6, "amplitude": 0.70},
    "Forest Mountain Park": {"min_height_m": 4.6, "max_height_m": 48.1, "amplitude": 0.90},
    "GrandNational": {"min_height_m": 2.5, "max_height_m": 21.2, "amplitude": 0.90},
    "Hybris": {"min_height_m": -3.9, "max_height_m": 87.8, "amplitude": 0.90},
    "Wilderness Park": {"min_height_m": -20.5, "max_height_m": 72.1, "amplitude": 0.90},
    "Scenic Run": {"min_height_m": 2.8, "max_height_m": 99.8, "amplitude": 0.90},
    }

# Established absolute-height lift cue retained unchanged alongside Vertical G
# and Normalized Height. Values are normalized platform Z commands.
# The Mdx Logo Coaster heights come from the 2026-09-10 captured run. The Z
# range is shifted for the provisional 1094 mm chair geometry while
# retaining the tested 0.32 normalized lift span. This minimizes balanced
# clipping in the 2026-09-10 Mdx Logo Coaster legacy capture.
LEGACY_HEIGHT_PROFILES = {
    "Mdx Logo Coaster": {
        "bottom_height_m": 7.4,
        "lift_top_height_m": 32.6,
        "z_at_bottom": -0.50,
        "z_at_lift_top": -0.18,
    },
}

# SOC supplies yaw angular velocity directly. NL2 supplies orientation only,
# so the adapter derives the equivalent local vertical-axis angular velocity
# from consecutive quaternions. This gain matches the SOC airborne yaw factor.
YAW_RATE_GAIN = 2.0
# Differentiating NL2 orientation magnifies small frame-to-frame changes. This
# first-order low-pass time constant smooths the derived yaw rate before gain
# and clipping, while retaining prompt response to a real turn.
YAW_RATE_LOW_PASS_TAU_S = 0.25

# NoLimits2 parks available to the generic POC ride selector. Paths are Windows
# paths interpreted by NL2 on the simulator PC; they do not need to exist on
# the Pi running POC. Add deployed rides here.
BASE_RIDE_PATH = r"C:/Program Files/NoLimits 2/parks/MDX"
RIDES = (
    ("Mdx Logo Coaster", f"{BASE_RIDE_PATH}/MdxLogoCoaster/MdxLogoCoaster.nl2park"),
    ("Blackbird", f"{BASE_RIDE_PATH}/Blackbird/Blackbird.nl2park"),
    ("Fenrir", f"{BASE_RIDE_PATH}/Fenrir/Fenrir.nl2park"),
    ("Forest Mountain Park", f"{BASE_RIDE_PATH}/Forest Mountain Park/Forest Mountain Park.nl2park"),
    ("GrandNational", f"{BASE_RIDE_PATH}/GrandNational/GrandNational.nl2park"),
    ("Hybris", f"{BASE_RIDE_PATH}/Hybris/Hybris.nl2park"),
    ("Scenic Run", f"{BASE_RIDE_PATH}/Scenic Run/Scenic Run.nl2park"),
    ("Tilt Coaster", f"{BASE_RIDE_PATH}/Tilt Coaster/Tilt Coaster.nl2park"),
    ("Wilderness Park", f"{BASE_RIDE_PATH}/Wilderness Park/Wilderness Park.nl2park"),
)
DEFAULT_RIDE_INDEX = 0  # this ride loaded at startup

# Connection/API health.  TCP connection and NL2 API connection are separate:
# after TCP connects, the adapter sends GET_VERSION immediately and treats a
# valid NL2 response as proof that the simulator datalink is alive.
CONNECT_TIMEOUT_S = 1.0
API_PROBE_PERIOD_S = 1.0
API_RESPONSE_STALE_S = 3.0

# Per-simulator washout configuration.  The common POC motion pipeline will
# consume these per-axis values; they are intentionally not embedded in the
# NL2 protocol/state adapter.
