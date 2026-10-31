# cfg_SuspendedChairV3.py ver 2
"""Chair V3 geometry/tuning, distinct from the legacy chair_v2 selector.

For this test only, raise the original geometry-derived midpoint by 100 mm.
The existing Chair XYZ attachment coordinates and physical muscle limits are
inherited unchanged. The old chair_v2 module is unaffected.
"""
from math import radians
from kinematics.cfg_SuspendedChair import PlatformConfig as BaseChairConfig


class PlatformConfig(BaseChairConfig):
    PLATFORM_NAME = "Chair V3"

    # Positive raises the moving platform and shortens all six muscles at
    # zero rotation/translation. Candidate based on 19 recorded captures:
    # typical low-station start is near 18-20% above the lowest feasible
    # level-platform position. This is NOT a change to the floor-clearance
    # offset or muscle length limits. Validate on the physical chair first.
    NEUTRAL_HEIGHT_OFFSET_MM = 100.0

    def __init__(self):
        super().__init__()
        # The parent stores limits on the instance, so class-level overrides
        # would have no effect on live or offline kinematics.
        self.LIMITS_1DOF_TRANFORM = (
            90, 90, 100, radians(10), radians(8), radians(9),
        )
