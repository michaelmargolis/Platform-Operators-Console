# kinematics_V3.py ver 3
""" kinematics_V3
This module provides inverse kinematics for the Mdx motion platforms.
The core method, named inverse_kinematics, is passed the desired orientation as: [surge, sway, heave, roll, pitch, yaw]
and returns the platform pose as an array of coordinates for the attachment points.
Pose is converted to actuator lengths using the method: muscle_lengths_from_pose.

This version is for the suspended platform only
NOTE: All length values returned now represent actual **muscle lengths** instead of contraction amounts.
"""

import math
import numpy as np
from collections import namedtuple
import logging

log = logging.getLogger(__name__)


PlatformParams = namedtuple("PlatformParams", [
    "MUSCLE_MIN_LENGTH",
    "MUSCLE_MAX_LENGTH",
    "FIXED_HARDWARE_LENGTH",
    "LIMITS_1DOF_TRANSFORM"
])

class Kinematics(object):
    def __init__(self):
        np.set_printoptions(precision=3, suppress=True)
        self.intensity = 1.0
        self.set_axis_flip_mask([1,1,-1,-1,1,1]) # defualt, the sim will set the mask if necessary


    def clamp(self, n, minn, maxn):
        return max(min(maxn, n), minn)

    def set_geometry(
        self,
        base_coords,
        platform_coords,
        platform_params,
        clearance_offset=50,
        muscle_extension_reserve=None,
    ):
        """
        Set base and moving-platform attachment geometry and calculate the
        neutral platform height.

        ``base_coords`` and ``platform_coords`` may contain either three
        left-side points (which are mirrored) or six explicit points. Platform
        points may be XYZ or legacy XY; legacy XY points are treated as Z=0.

        Platform Z values are local offsets from the moving-platform reference
        plane. Legacy configurations retain the existing ``clearance_offset``
        lower boundary. Configurations that provide ``muscle_extension_reserve``
        instead derive the lower level-platform boundary from the maximum active
        actuator length. The upper boundary is where the first actuator reaches
        MIN_ACTUATOR_LENGTH. Their midpoint becomes the neutral reference-plane
        height.
        """

        # Unpack platform parameters.
        self.MIN_MUSCLE_LENGTH = platform_params.MUSCLE_MIN_LENGTH
        self.MAX_MUSCLE_LENGTH = platform_params.MUSCLE_MAX_LENGTH
        self.FIXED_HARDWARE_LENGTH = platform_params.FIXED_HARDWARE_LENGTH
        self.MUSCLE_LENGTH_RANGE = self.MAX_MUSCLE_LENGTH - self.MIN_MUSCLE_LENGTH
        self.MIN_ACTUATOR_LENGTH = self.MIN_MUSCLE_LENGTH + self.FIXED_HARDWARE_LENGTH
        self.MAX_ACTUATOR_LENGTH = self.MAX_MUSCLE_LENGTH + self.FIXED_HARDWARE_LENGTH
        self.limits_1dof = platform_params.LIMITS_1DOF_TRANSFORM

        def to_xyz(coords, label, allow_xy=False):
            result = []
            for point in coords:
                if len(point) == 3:
                    x, y, z = point
                elif allow_xy and len(point) == 2:
                    x, y = point
                    z = 0.0
                else:
                    expected = "XY or XYZ" if allow_xy else "XYZ"
                    raise ValueError(f"{label} points must be {expected}: {point!r}")
                result.append([float(x), float(y), float(z)])
            if len(result) not in (3, 6):
                raise ValueError(f"{label} must contain 3 or 6 points; found {len(result)}")
            return result

        def mirror(coords):
            return coords + [[x, -y, z] for x, y, z in reversed(coords)]

        base = to_xyz(base_coords, "base_coords")
        platform_local = to_xyz(platform_coords, "platform_coords", allow_xy=True)

        if len(base) == 3:
            base = mirror(base)
        if len(platform_local) == 3:
            platform_local = mirror(platform_local)
        if len(base) != len(platform_local):
            raise ValueError(
                f"base/platform point count mismatch: {len(base)} base, "
                f"{len(platform_local)} platform"
            )

        self.base_coords = np.asarray(base, dtype=float)
        self.platform_local_coords = np.asarray(platform_local, dtype=float)

        # Retain the legacy mean-height attribute for older diagnostics only.
        # Runtime geometry and the visualizer use the individual base Z values.
        self.UPPER_ACTUATOR_Z_HEIGHT = float(np.mean(self.base_coords[:, 2]))
        self.BASE_MAX_Z = float(np.max(self.base_coords[:, 2]))

        if muscle_extension_reserve is None:
            # Preserve the established behaviour for older platform configs.
            self.MUSCLE_EXTENSION_RESERVE = None
            self.MAX_ACTIVE_ACTUATOR_LENGTH = self.MAX_ACTUATOR_LENGTH
            z_min = float(clearance_offset)
            z_max = None
            z_step = 1.0
            for z in np.arange(z_min, 1000, z_step):
                candidate = self.platform_local_coords + np.array([0.0, 0.0, z])
                lengths = np.linalg.norm(candidate - self.base_coords, axis=1)
                if np.any(lengths <= self.MIN_ACTUATOR_LENGTH):
                    z_max = float(z)
                    break
            if z_max is None:
                raise ValueError(
                    "Unable to find mid-Z position where any actuator reaches minimum length."
                )
        else:
            # For the MDX platform, the reserve is a reduction in maximum active
            # muscle extension, not a Z offset. For a level platform the actuator
            # length equation can be solved directly for reference-plane Z, so no
            # iterative search is needed.
            reserve = max(0.0, float(muscle_extension_reserve))
            self.MUSCLE_EXTENSION_RESERVE = reserve
            self.MAX_ACTIVE_ACTUATOR_LENGTH = self.MAX_ACTUATOR_LENGTH - reserve

            def level_z_for_length(target_length):
                z_values = []
                for base_point, platform_point in zip(
                    self.base_coords, self.platform_local_coords
                ):
                    dx = platform_point[0] - base_point[0]
                    dy = platform_point[1] - base_point[1]
                    horizontal_sq = dx * dx + dy * dy
                    target_sq = target_length * target_length
                    if horizontal_sq >= target_sq:
                        raise ValueError(
                            "Actuator target length is shorter than the horizontal "
                            "attachment separation."
                        )
                    vertical = math.sqrt(target_sq - horizontal_sq)
                    z_values.append(base_point[2] - platform_point[2] - vertical)
                return z_values

            # All actuators must be no longer than the active maximum, so the
            # lowest level-platform Z is the largest of those six boundaries.
            z_min = max(level_z_for_length(self.MAX_ACTIVE_ACTUATOR_LENGTH))
            # As the platform rises, contraction increases. The first actuator to
            # reach the physical minimum determines the upper level-platform Z.
            z_max = min(level_z_for_length(self.MIN_ACTUATOR_LENGTH))

        if z_min >= z_max:
            raise ValueError(
                f"Invalid neutral-height bounds: lower={z_min:.3f}, upper={z_max:.3f}"
            )

        mid_z = (z_min + z_max) / 2.0
        self.PLATFORM_LEVEL_Z_MIN = float(z_min)
        self.PLATFORM_LEVEL_Z_MAX = float(z_max)

        # ``self.platform_coords`` is the neutral pose used by the existing IK.
        # Keeping the neutral reference-plane Z in these coordinates preserves
        # the established rotation/translation convention.
        self.platform_coords = self.platform_local_coords + np.array([0.0, 0.0, mid_z])
        self.PLATFORM_MID_HEIGHT = mid_z

        # Compute neutral actuator/muscle lengths.
        _, lengths_at_mid = self.inverse_kinematics([0, 0, 0, 0, 0, 0], return_lengths=True)
        self.PLATFORM_NEUTRAL_MUSCLE_LENGTHS = lengths_at_mid - self.FIXED_HARDWARE_LENGTH
        self.cached_muscle_lengths = self.PLATFORM_NEUTRAL_MUSCLE_LENGTHS.copy()

    def print_geometry_details(self, clearance_offset):
        print("debug info:")
        print(f"Clearance_offset: {clearance_offset}")
        print(f"PLATFORM_MID_HEIGHT: {self.PLATFORM_MID_HEIGHT}")
        print(f"Min active MUSCLE LENGTH: {self.MIN_MUSCLE_LENGTH}")
        print(f"Max active MUSCLE LENGTH: {self.MAX_MUSCLE_LENGTH}")
        print(f"PLATFORM_MID_HEIGHT: {self.PLATFORM_MID_HEIGHT}")


    def solve_heave_for_target_lengths(self, target_lengths, z_bounds=(100, 200)):
        """
        Solves for Z (heave) such that actuator lengths match the target_lengths.
        All other pose components are zero.
        """
        def objective(z):
            pose = [0, 0, z, 0, 0, 0]
            _, lengths = self.inverse_kinematics(pose, return_lengths=True)
            return np.sum((lengths - target_lengths) ** 2)

        result = minimize_scalar(objective, bounds=z_bounds, method='bounded')

        if not result.success:
            raise ValueError(f"Z-only IK solve failed: {result.message}")

        final_z = result.x
        final_pose = [0, 0, final_z, 0, 0, 0]
        platform_coords, lengths = self.inverse_kinematics(final_pose, return_lengths=True)
        return platform_coords, lengths
   
    def set_axis_flip_mask(self, axis_flip_mask):
        assert len(axis_flip_mask) == 6, "Axis flip mask must be 6 elements"
        self.AXIS_FLIP_MASK = np.asarray(axis_flip_mask, dtype=float)
        self.FLIP_TRANSLATION = self.AXIS_FLIP_MASK[:3]
        self.FLIP_ROTATION = self.AXIS_FLIP_MASK[3:]

    def calc_rotation(self, rpy):
        roll, pitch, yaw = rpy
        cos_roll, sin_roll = math.cos(roll), math.sin(roll)
        cos_pitch, sin_pitch = math.cos(pitch), math.sin(pitch)
        cos_yaw, sin_yaw = math.cos(yaw), math.sin(yaw)

        return np.array([
            [cos_yaw * cos_pitch,
             cos_yaw * sin_pitch * sin_roll - sin_yaw * cos_roll,
             cos_yaw * sin_pitch * cos_roll + sin_yaw * sin_roll],
            [sin_yaw * cos_pitch,
             sin_yaw * sin_pitch * sin_roll + cos_yaw * cos_roll,
             sin_yaw * sin_pitch * cos_roll - cos_yaw * sin_roll],
            [-sin_pitch,
             cos_pitch * sin_roll,
             cos_pitch * cos_roll]
        ])

    def inverse_kinematics(self, request, return_lengths=False):
        assert len(request) == 6, "Transform must be 6-element sequence"

        a = np.asarray(request, dtype=float)

        translation = a[:3] * self.FLIP_TRANSLATION
        rpy = a[3:6] * self.FLIP_ROTATION

        Rxyz = self.calc_rotation(rpy)

        pose = (Rxyz @ self.platform_coords.T).T + translation
        self.cached_pose = pose
        # print("lengths = ", np.linalg.norm(pose - self.base_coords, axis=1)  )      
        
        if return_lengths:
            actuator_lengths = np.linalg.norm(pose - self.base_coords, axis=1)
            return pose, actuator_lengths
        return pose

    def muscle_lengths(self, xyzrpy):
        _, actuator_lengths = self.inverse_kinematics(xyzrpy, return_lengths=True)
        self.cached_muscle_lengths = tuple(self.muscle_lengths_from_lengths(actuator_lengths)) # cache the calculated muscle lengths
        return self.muscle_lengths_from_lengths(actuator_lengths)

    def muscle_lengths_from_lengths(self, actuator_lengths):
        # return np.clip(actuator_lengths - self.FIXED_HARDWARE_LENGTH, 0, self.MAX_MUSCLE_LENGTH)
        return [
            min(int(round(length - self.FIXED_HARDWARE_LENGTH)), self.MAX_MUSCLE_LENGTH)
            for length in actuator_lengths
        ]
  
    def muscle_lengths_from_pose(self, pose):
        actuator_lengths = np.linalg.norm(pose - self.base_coords, axis=1)
        return self.muscle_lengths_from_lengths(actuator_lengths)

    def muscle_percents(self, xyzrpy, offset=0):
        pose, actuator_lengths = self.inverse_kinematics(xyzrpy, return_lengths=True)
        lengths = self.muscle_lengths_from_lengths(actuator_lengths)
        return self.percent_from_muscle_length(lengths, offset)

    def percent_from_muscle_length(self, lengths, offset=0):
        return [round(((l - offset) * 100.0) / self.MUSCLE_LENGTH_RANGE, 1) for l in lengths]

    def get_cached_pose(self):
        return self.cached_pose

    def get_cached_muscle_lengths(self):
        return self.cached_muscle_lengths
        
    def set_intensity(self, intensity):
        self.intensity = intensity
        log.info("Kinematics intensity set to %.1f", intensity)

    def norm_to_real(self, transform):
        xform = np.asarray(transform, dtype=float)
        np.clip(xform, -1, 1, xform)  # clip normalized values
        #  convert from normalized to real world values
        real_transform = np.multiply(xform, self.limits_1dof) 
        return real_transform        
        

if __name__ == "__main__":
    from collections import namedtuple
    from cfg_SuspendedPlatformMdx import PlatformConfig

    # NamedTuple for platform params
    PlatformParams = namedtuple("PlatformParams", [
        "MUSCLE_MIN_LENGTH",
        "MUSCLE_MAX_LENGTH",
        "FIXED_HARDWARE_LENGTH",
        "LIMITS_1DOF_TRANSFORM"
    ])

    cfg = PlatformConfig()
    params = PlatformParams(
        cfg.MUSCLE_MIN_LENGTH,
        cfg.MUSCLE_MAX_LENGTH,
        cfg.FIXED_HARDWARE_LENGTH,
        cfg.LIMITS_1DOF_TRANFORM
    )

    print("=== Platform Muscle Configuration ===")
    print(f"MUSCLE_MAX_LENGTH         = {cfg.MUSCLE_MAX_LENGTH}")
    print(f"MUSCLE_MIN_LENGTH         = {cfg.MUSCLE_MIN_LENGTH}")
    print(f"MUSCLE_MAX_ACTIVE_LENGTH  = {cfg.MUSCLE_MAX_ACTIVE_LENGTH}")
    print(f"MUSCLE_MIN_ACTIVE_LENGTH  = {cfg.MUSCLE_MIN_ACTIVE_LENGTH}")
    print(f"FIXED_HARDWARE_LENGTH     = {cfg.FIXED_HARDWARE_LENGTH}")
    print(f"MIN_ACTUATOR_LENGTH       = {cfg.MIN_ACTUATOR_LENGTH}")
    print(f"MAX_ACTUATOR_LENGTH       = {cfg.MAX_ACTUATOR_LENGTH}")

    k = Kinematics()

    # Apply geometry and mid-height calculation
    k.set_geometry(
        cfg.base_coords,
        cfg.platform_coords,
        params,
        getattr(cfg, "PLATFORM_CLEARANCE_OFFSET", 50),
        getattr(cfg, "MUSCLE_EXTENSION_RESERVE", None),
    )

    # Output diagnostics
    print(f"PLATFORM_MID_HEIGHT = {k.PLATFORM_MID_HEIGHT:.2f}")
    print(f"NEUTRAL_MUSCLE_LENGTHS = {[int(l) for l in k.get_cached_muscle_lengths()]}")
    print(f"NEUTRAL_MUSCLE_LENGTHS = {k.PLATFORM_NEUTRAL_MUSCLE_LENGTHS}\n")

    # Axis flip and motion intensity setup
    axis_flip_mask = [1, 1, 1, 1, 1, -1]
    k.set_axis_flip_mask(axis_flip_mask)
    
    ##swap_roll_pitch = cfg.SWAP_ROLL_PITCH

    """
    # Heave-only test
    for z_norm in [-1.0, 0.0, 1.0]:
        transform = [0, 0, z_norm, 0, 0, 0]
        real_transform = k.norm_to_real(transform)
        muscle_lengths = k.muscle_lengths(real_transform)
        print(f"heave = {z_norm:+.1f} → lengths = {[int(l) for l in muscle_lengths]}")
        
    """
    dof_labels = ["surge", "sway", "heave", "roll", "pitch", "yaw"]
    test_values = [-1.0, 0.0, 1.0] 
   
    def test_all_dofs():
        for i in range(6):
            print(f"--- {dof_labels[i]} ---")
            for val in test_values:
                transform = [0.0] * 6
                transform[i] = val
                real_transform = k.norm_to_real(transform)
                muscle_lengths = k.muscle_lengths(real_transform)
                print(f"{dof_labels[i]} = {val:+.1f} → lengths = {[int(l) for l in muscle_lengths]}")
            print()

    # Run test
    test_all_dofs() 
   
