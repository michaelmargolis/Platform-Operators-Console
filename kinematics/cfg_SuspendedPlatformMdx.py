#cfg_SuspendedPlatformMdx.py  ver 3
from math import radians


class PlatformConfig(object):
    PLATFORM_NAME = "MDX Suspended Platform"
    PLATFORM_TYPE = "Inverted Stewart Platform"
    PLATFORM_INVERTED = True
    # MDX rider-frame transform: +roll means right side down (CAD -Y down).
    # Preserve the original heave convention; no change to chair platforms.
    AXIS_FLIP_MASK = (1, 1, -1, 1, 1, 1)
    # The six kinematic indices also identify physical EasyIP valve channels.
    # CAD: channel 0 is the front-right (+X, -Y) muscle.
    MUSCLE_PRESSURE_MAPPING_FILE = "output/wheelchair_DtoP.csv"

    # CAD reference: the nominal moving-attachment plane is 84 mm above floor.
    # Geometry below has already had this datum subtracted from every Z value.
    CAD_Z_REFERENCE_OFFSET = 84.0
    FLOOR_Z = -84.0
    PLATFORM_UNDERSIDE_Z = -80.0  # 4 mm above floor at level/full extension.

    # Reserve is used to choose the neutral level-platform height. In the normal
    # geometry-limited mode the platform may use more extension when the actual
    # floor outline and physical actuator limits show that the pose is safe.
    MUSCLE_EXTENSION_RESERVE = 60.0

    # One-switch fallback for platform testing:
    #   geometry = floor outline + physical actuator limits (normal)
    #   reserve  = fixed active max length from MUSCLE_EXTENSION_RESERVE
    #   off      = legacy behaviour; no workspace limiting
    WORKSPACE_LIMIT_MODE = "geometry"
    WORKSPACE_LIMIT_ITERATIONS = 6
    MIN_FLOOR_CLEARANCE = 15.0

    # The simplified moving-platform DXF was reduced to a compact working
    # convex-hull approximation. Point reduction can be optimistic by <0.4 mm,
    # so add a 0.5 mm calculation margin while retaining a 15 mm clearance.
    FLOOR_HULL_APPROX_MARGIN = 0.5
    PLATFORM_UNDERSIDE_HULL_XY = (
        (-646.935, 439.365),
        (-646.935, -439.359),
        (-245.446, -647.923),
        (-238.869, -650.615),
        (-228.024, -652.383),
        (-217.106, -651.134),
        (689.086, -411.995),
        (774.068, -384.485),
        (780.440, -381.872),
        (787.701, -377.838),
        (795.088, -370.321),
        (800.713, -361.512),
        (804.401, -351.733),
        (805.994, -341.404),
        (806.222, -8.117),
        (806.054, 339.211),
        (805.586, 345.789),
        (804.264, 352.249),
        (800.713, 361.518),
        (795.469, 369.843),
        (787.837, 377.731),
        (781.857, 381.162),
        (773.566, 384.664),
        (691.631, 411.272),
        (-216.904, 651.088),
        (-226.210, 652.391),
        (-235.972, 651.397),
        (-245.136, 648.088),
    )

    PLATFORM_LOWEST_Z = -1085  # Legacy compatibility only.

    def __init__(self):
        self.MUSCLE_MAX_LENGTH = 1000
        self.MUSCLE_MIN_LENGTH = self.MUSCLE_MAX_LENGTH * 0.75
        self.FIXED_HARDWARE_LENGTH = 200

        self.MIN_ACTUATOR_LENGTH = self.MUSCLE_MIN_LENGTH + self.FIXED_HARDWARE_LENGTH
        self.MAX_ACTUATOR_LENGTH = self.MUSCLE_MAX_LENGTH + self.FIXED_HARDWARE_LENGTH
        self.MUSCLE_LENGTH_RANGE = self.MUSCLE_MAX_LENGTH - self.MUSCLE_MIN_LENGTH
        self.MUSCLE_MAX_ACTIVE_LENGTH = (
            self.MUSCLE_MAX_LENGTH - self.MUSCLE_EXTENSION_RESERVE
        )
        self.MUSCLE_MIN_ACTIVE_LENGTH = self.MUSCLE_MIN_LENGTH
        self.MAX_ACTIVE_ACTUATOR_LENGTH = (
            self.MUSCLE_MAX_ACTIVE_LENGTH + self.FIXED_HARDWARE_LENGTH
        )

        self.UNLOADED_PLATFORM_WEIGHT = 40
        self.PAYLOAD_WEIGHTS = (50, 100, 150)
        self.MOTION_INTENSITY_RANGE = (10, 50, 150)

        self.LIMITS_1DOF_TRANFORM = (
            60,
            60,
            80,
            radians(8),
            radians(5),
            radians(10),
        )

        self.DEACTIVATED_MUSCLE_LENGTHS = [self.MUSCLE_MAX_LENGTH] * 6
        self.PROPPING_MUSCLE_LENGTHS = [self.MUSCLE_MAX_LENGTH * 0.08] * 6
        self.DEACTIVATED_TRANSFORM = [
            0,
            0,
            -self.LIMITS_1DOF_TRANFORM[2] - 50,
            0,
            0,
            0,
        ]
        self.PROPPING_TRANSFORM = [
            0,
            0,
            -self.LIMITS_1DOF_TRANFORM[2],
            0,
            0,
            0,
        ]

        self.HAS_PISTON = False
        self.HAS_BRAKE = False

        # Explicit CAD attachment pairs in PHYSICAL Festo channel order 0..5.
        # Earlier releases listed these pairs in reverse order (5..0), so the
        # numbers sent to the six EasyIP valve channels did not match the CAD.
        # CAD Z is shifted by CAD_Z_REFERENCE_OFFSET (84 mm).
        # Channel 0 is rider front-right (+X, -Y), then 1/2 on the right,
        # and 3/4/5 on the left (+Y).
        self.base_coords = [
            [413.0, -566.0, 1083.0],  # valve 0
            [289.0, -654.0, 1083.0],  # valve 1
            [-731.0, -76.0, 1037.0], # valve 2
            [-731.0, 76.0, 1037.0],  # valve 3
            [291.0, 622.0, 1083.0],  # valve 4
            [438.0, 585.0, 1083.0],  # valve 5
        ]
        self.platform_coords = [
            [716.0, -166.0, -8.0],  # valve 0
            [-221.0, -608.0, -2.0], # valve 1
            [-347.0, -536.0, -3.0], # valve 2
            [-348.0, 536.0, -2.0],  # valve 3
            [-222.0, 609.0, -2.0],  # valve 4
            [716.0, 166.0, -8.0],   # valve 5
        ]
