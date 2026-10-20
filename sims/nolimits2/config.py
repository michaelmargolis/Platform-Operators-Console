#config.py  ver 13
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

# NL2 starts with the established lift-height cue. A temporary session-only UI
# control can select the conventional vertical acceleration cue used by SOC.
HEAVE_MODE = "legacy_height"
HEAVE_MODE_LEGACY_HEIGHT = "legacy_height"
HEAVE_MODE_VERTICAL_G = "vertical_g"

# Temporary operator comparison of the established absolute-height lift cue
# against SOC-style vertical-G heave. Values are normalized platform Z commands.
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
    ("Flying Beast", f"{BASE_RIDE_PATH}/Flying Beast/Flying Beast.nl2park"),
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
