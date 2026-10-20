#config.py  ver 7
"""X-Plane adapter configuration.

Values are derived from the current SOP X-Plane telemetry configuration.  The
normalization factors remain simulator-specific; POC only receives normalized
six-axis motion values.
"""

SERVICE_PERIOD_MS = 100

TELEMETRY_EVT_PORT = 10022
TELEMETRY_CMD_PORT = 10023
HEARTBEAT_PORT = 10030

INIT_COMMS_INTERVAL_S = 1.0
HEARTBEAT_INTERVAL_S = 1.0
TELEMETRY_TIMEOUT_S = 1.0

# Retain the initial linear X-Plane response until flight testing provides a
# suitable simulator-specific threshold and curve.
FAN_CONFIG = {
    "speed_at_100": 50.0,
    "threshold_speed": 0.0,
    "threshold_power": 0.0,
    "max_power": 100.0,
    "speed_power_table": ((0.0, 0.0), (50.0, 100.0)),
}

TELEMETRY_KEYS = (
    "g_axil",
    "g_side",
    "g_nrml",
    "phi",
    "theta",
    "Rrad",
)

# SOP telemetry_config.ini values.
AIR_FACTORS = (1.0, 1.20, 0.50, -2.00, 2.00, 2.00)
GROUND_FACTORS = (4.0, 1.20, 0.50, -2.00, 2.00, 0.50)

# Retained for later platform/kinematics integration.  The current prototype
# kinematics path still owns the active mask.
AXIS_FLIP_MASK = (1, 1, -1, -1, 1, 1)

# Diagnostic switches. These are intentionally separate so pause/state tracing can
# stay enabled without logging routine transport traffic.
XPLANE_DEBUG = False
XPLANE_PAUSE_DEBUG = True
XPLANE_CONNECTION_DEBUG = True
XPLANE_LOAD_DEBUG = True
XPLANE_MISSING_PAUSE_LOG_INTERVAL_S = 2.0

# Flight choices mirror the three SOP situations. POC always owns a known,
# reloadable X-Plane experience; there is no "Current Flight" placeholder.
DEFAULT_EXPERIENCE_INDEX = 0
EXPERIENCES = (
    ("calm straight and level", 0),
    ("beginner takeoff", 1),
    ("easy approach", 2),
)
