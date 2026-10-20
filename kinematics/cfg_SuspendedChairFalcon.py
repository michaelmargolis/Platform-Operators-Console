#cfg_SuspendedChairFalcon.py  ver 1
from math import radians


class PlatformConfig(object):
    PLATFORM_NAME = "Falcon Platform"
    PLATFORM_TYPE = "Inverted Stewart Platform"
    PLATFORM_INVERTED = True
    MUSCLE_PRESSURE_MAPPING_FILE = "output/wheelchair_DtoP.csv"

    PLATFORM_CLEARANCE_OFFSET = 80  # mm
    PLATFORM_LOWEST_Z = -1085       # Legacy (unused now if mid-height is auto-computed)
    UPPER_ACTUATOR_Z_HEIGHT = 1070  # Existing Falcon geometry reference

    def __init__(self):
        self.MUSCLE_MAX_LENGTH = 1000
        self.MUSCLE_MIN_LENGTH = self.MUSCLE_MAX_LENGTH * 0.75
        self.FIXED_HARDWARE_LENGTH = 200

        self.MIN_ACTUATOR_LENGTH = self.MUSCLE_MIN_LENGTH + self.FIXED_HARDWARE_LENGTH
        self.MAX_ACTUATOR_LENGTH = self.MUSCLE_MAX_LENGTH + self.FIXED_HARDWARE_LENGTH
        self.MUSCLE_LENGTH_RANGE = self.MUSCLE_MAX_LENGTH - self.MUSCLE_MIN_LENGTH
        self.MUSCLE_MAX_ACTIVE_LENGTH = self.MUSCLE_MAX_LENGTH - self.PLATFORM_CLEARANCE_OFFSET
        self.MUSCLE_MIN_ACTIVE_LENGTH = self.MUSCLE_MIN_LENGTH

        self.UNLOADED_PLATFORM_WEIGHT = 40
        self.PAYLOAD_WEIGHTS = (50, 100, 150)
        self.MOTION_INTENSITY_RANGE = (10, 50, 150)

        self.LIMITS_1DOF_TRANFORM = (60, 60, 80, radians(8), radians(5), radians(10))

        self.DEACTIVATED_MUSCLE_LENGTHS = [self.MUSCLE_MAX_LENGTH] * 6
        self.PROPPING_MUSCLE_LENGTHS = [self.MUSCLE_MAX_LENGTH * 0.08] * 6
        self.DEACTIVATED_TRANSFORM = [0, 0, -self.LIMITS_1DOF_TRANFORM[2] - 50, 0, 0, 0]
        self.PROPPING_TRANSFORM = [0, 0, -self.LIMITS_1DOF_TRANFORM[2], 0, 0, 0]

        self.HAS_PISTON = False
        self.HAS_BRAKE = False

        # Existing Falcon geometry (left side only).
        self.base_coords = [
            [379.8, -515.1, self.UPPER_ACTUATOR_Z_HEIGHT],
            [258.7, -585.4, self.UPPER_ACTUATOR_Z_HEIGHT],
            [-636.0, -71.4, self.UPPER_ACTUATOR_Z_HEIGHT],
        ]

        self.platform_coords = [
            [617.0, -170.0, 0.0],
            [-256.2, -586.5, 0.0],
            [-377.6, -516.7, 0.0],
        ]
