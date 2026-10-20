#cfg_SuspendedChair.py  ver 6
from math import radians


class PlatformConfig(object):
    PLATFORM_NAME = "Chair V3"
    PLATFORM_TYPE = "Inverted Stewart Platform"
    PLATFORM_INVERTED = True
    MUSCLE_PRESSURE_MAPPING_FILE = "output/chair_DtoP_synthesized.csv"

    PLATFORM_CLEARANCE_OFFSET = 0   # mm
    PLATFORM_LOWEST_Z = -1085       # Legacy (unused now if mid-height is auto-computed)

    # CAD-derived rigid moving-platform geometry.  The reference/top plane is
    # local Z=0 and the platform is 40 mm thick.  The small relaxed-platform
    # tilt measured in the assembly is a kinematic result and is deliberately
    # not baked into these local coordinates.
    PLATFORM_UNDERSIDE_Z = -40.0
    PLATFORM_UNDERSIDE_HULL_XY = (
        (-408.641, -542.631),
        (-265.747, -625.131),
        (824.516, -210.044),
        (824.516, 209.956),
        (-265.747, 625.043),
        (-408.641, 542.543),
    )

    # Keep the existing chair behaviour.  The hull above is retained for CAD
    # comparison and future workspace-limiter work; it is not enabled here.
    WORKSPACE_LIMIT_MODE = "off"

    def __init__(self):
        self.MUSCLE_MAX_LENGTH = 800
        self.MUSCLE_MIN_LENGTH = self.MUSCLE_MAX_LENGTH * 0.75
        self.FIXED_HARDWARE_LENGTH = 200

        self.MIN_ACTUATOR_LENGTH = self.MUSCLE_MIN_LENGTH + self.FIXED_HARDWARE_LENGTH
        self.MAX_ACTUATOR_LENGTH = self.MUSCLE_MAX_LENGTH + self.FIXED_HARDWARE_LENGTH
        self.MUSCLE_LENGTH_RANGE = self.MUSCLE_MAX_LENGTH - self.MUSCLE_MIN_LENGTH
        self.MUSCLE_MAX_ACTIVE_LENGTH = self.MUSCLE_MAX_LENGTH - self.PLATFORM_CLEARANCE_OFFSET
        self.MUSCLE_MIN_ACTIVE_LENGTH = self.MUSCLE_MIN_LENGTH

        # self.DEFAULT_PAYLOAD_WEIGHT = 65
        # self.PAYLOAD_WEIGHT_RANGE = (20, 90)
        self.UNLOADED_PLATFORM_WEIGHT = 25
        self.PAYLOAD_WEIGHTS = (50, 60, 150)
        self.MOTION_INTENSITY_RANGE = (10, 50, 150)

        self.LIMITS_1DOF_TRANFORM = (90, 90, 100, radians(12), radians(10), radians(12))

        self.DEACTIVATED_MUSCLE_LENGTHS = [self.MUSCLE_MAX_LENGTH] * 6
        # Clearance decision uses contraction because this platform's muscles
        # shorten as the suspended platform rises. The actual clearance pose is
        # configured independently and will be refined during platform testing.
        self.PROPPING_MUSCLE_CONTRACTION_MM = self.MUSCLE_MAX_LENGTH * 0.08
        self.PROP_CLEARANCE_Z_MM = 0
        self.PISTON_MOVE_SECONDS = 0.5
        self.DEACTIVATED_TRANSFORM = [0, 0, -self.LIMITS_1DOF_TRANFORM[2] - 50, 0, 0, 0]
        self.PROPPING_TRANSFORM = [0, 0, -self.LIMITS_1DOF_TRANFORM[2], 0, 0, 0]

        self.HAS_PISTON = True
        self.HAS_BRAKE = False

        # Six explicit CAD fixed/base attachment points in upper 0..5 order.
        # These are assembly/platform coordinates in mm.
        self.base_coords = [
            [380.0, 518.0, 1048.0],
            [259.0, 588.0, 1048.0],
            [-638.0, 70.0, 1048.0],
            [-638.0, -70.0, 1048.0],
            [259.0, -588.0, 1048.0],
            [380.0, -518.0, 1048.0],
        ]

        # Six explicit moving-platform attachment points in lower 0..5 order.
        # X/Y are from the moving-platform CAD/DXF.  Z is local to the rigid
        # moving-platform reference plane, so the measured relaxed tilt is not
        # encoded here; it emerges from the six actuator lengths/geometry.
        self.platform_coords = [
            [760.517, 173.045, 0.0],
            [-252.588, 582.657, 0.0],
            [-378.513, 509.955, 0.0],
            [-378.513, -510.043, 0.0],
            [-252.588, -582.746, 0.0],
            [760.517, -169.448, 0.0],
        ]
