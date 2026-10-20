#config.py  ver 13
"""SpaceCoaster adapter configuration.

The Unity SpaceCoaster interface is intentionally treated as a legacy protocol.
Its state messages and xyzrpy messages are known to be unreliable, so this
adapter preserves the established SimpleSims state and telemetry behavior while
using the semantic Unity UDP controls.
"""

PORT = 10009
MAX_MSG_LEN = 65535
SOCKET_TIMEOUT_S = 0.25
FAN_SPEED_TIMEOUT_S = 0.5
# A running ride requires fresh Unity pacing telemetry. State/config packets do
# not refresh this timer because they do not advance the motion recording.
MOTION_DATA_TIMEOUT_S = 1.0

# Preserve the existing SpaceCoaster telemetry normalization.
MAX_VALUES = (80.0, 80.0, 80.0, 0.4, 0.4, 0.4)
START_FRAME = 0

# Provisional fan shaping: use the legacy NL2 chair values with speed derived
# from Unity's live WAGON position telemetry.
FAN_CONFIG = {
    "speed_at_100": 40.0,
    # Captured WAGON local-position speed is approximately nine times the
    # SpaceCoaster RollerCoaster speed used by the legacy fan curve.
    "position_speed_scale": 1.0 / 9.0,
    "position_speed_median_samples": 5,
    "position_speed_average_s": 1.0,
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

# Unity can report WAITING slightly before MenuMSU is ready to accept ready.
# Keep this non-blocking settling delay explicit and easy to tune.
SPACECOASTER_READY_DELAY_S = 1.0

# On reconnect, wait briefly for Unity to announce its current state. If it
# only sends telemetry, return it to the lobby once to re-establish state.
SPACECOASTER_STARTUP_SYNC_TIMEOUT_S = 1.0

# SpaceCoaster service remains coupled to POC's 20 Hz read path, matching the
# current SimpleSims implementation where read() calls service().
SERVICE_PERIOD_MS = 0

# State/UDP diagnostics. These are low-rate compared with NL2 telemetry debug.
SPACECOASTER_DEBUG = False
SPACECOASTER_UDP_DEBUG = False
SPACECOASTER_UDP_DEBUG_DATA = False
SPACECOASTER_UDP_DEBUG_UNKNOWN = False
