# test_chair_v3_neutral_v181.py ver 4
"""Chair V3 neutral-height test and offline/live module parity."""
import math
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from config.platforms import PLATFORMS
from kinematics.cfg_SuspendedChair import PlatformConfig as BaseChairConfig
from kinematics.kinematics_V3 import Kinematics, PlatformParams
from tools.analyze_nl2_csv import make_backend
from tools.sim_player.platform_selection import platform_choices, platform_metadata


def test_chair_v3_isolated_from_legacy_v2_and_mdx():
    assert PLATFORMS["chair_v3"]["sop_config_module"] == "kinematics.cfg_SuspendedChairV3"
    chair = make_backend("chair_v3")
    mdx = make_backend("platform_v2")
    base = BaseChairConfig()
    old_neutral = Kinematics()
    params = PlatformParams(base.MUSCLE_MIN_LENGTH, base.MUSCLE_MAX_LENGTH,
                            base.FIXED_HARDWARE_LENGTH, base.LIMITS_1DOF_TRANFORM)
    old_neutral.set_geometry(base.base_coords, base.platform_coords, params, 0, None)
    assert chair.k.NEUTRAL_HEIGHT_OFFSET_MM == 100
    assert old_neutral.NEUTRAL_HEIGHT_OFFSET_MM == 0
    assert mdx.k.NEUTRAL_HEIGHT_OFFSET_MM == 0
    assert chair.k.PLATFORM_MID_HEIGHT == pytest.approx(old_neutral.PLATFORM_MID_HEIGHT + 100)
    assert old_neutral.PLATFORM_MID_HEIGHT == pytest.approx(216.5)
    assert mdx.k.PLATFORM_MID_HEIGHT == pytest.approx(177.26466075505908)
    np.testing.assert_array_equal(chair.k.base_coords, old_neutral.base_coords)
    np.testing.assert_array_equal(chair.k.platform_local_coords, old_neutral.platform_local_coords)
    assert chair.cfg.MUSCLE_MIN_LENGTH == base.MUSCLE_MIN_LENGTH == 600
    assert chair.cfg.MUSCLE_MAX_LENGTH == base.MUSCLE_MAX_LENGTH == 800
    assert chair.cfg.PLATFORM_CLEARANCE_OFFSET == 0
    assert not chair._workspace_hull_xy


@pytest.mark.parametrize("axis", range(6))
def test_chair_v3_canonical_pose_moves_geometry_in_the_commanded_direction(axis):
    chair = make_backend("chair_v3")
    k = chair.k
    request = [0.0] * 6
    angle_or_distance = chair.cfg.LIMITS_1DOF_TRANFORM[axis] * 0.25
    request[axis] = angle_or_distance
    x, y, z = k.platform_coords.T
    c, s = math.cos(angle_or_distance), math.sin(angle_or_distance)
    rotations = ((x, c*y-s*z, s*y+c*z), (c*x+s*z, y, -s*x+c*z), (c*x-s*y, s*x+c*y, z))
    expected = k.platform_coords + request[:3] if axis < 3 else np.column_stack(rotations[axis-3])
    pose, lengths = k.inverse_kinematics(request, return_lengths=True)
    np.testing.assert_allclose(pose, expected, rtol=0, atol=1e-6)
    np.testing.assert_allclose(lengths, np.linalg.norm(expected-k.base_coords, axis=1), rtol=0, atol=1e-6)


def test_initial_blackbird_like_heave_near_one_fifth_above_lowest_level():
    b = make_backend('chair_v3')
    k = b.k
    # Typical low-station input observed in the recorded Blackbird captures.
    heave_norm = -.819
    request = k.norm_to_real([0, 0, heave_norm, 0, 0, 0])
    _, actuator = k.inverse_kinematics(request, return_lengths=True)
    muscle = actuator - b.cfg.FIXED_HARDWARE_LENGTH
    assert 750 < float(max(muscle)) < 800
    assert float(min(muscle)) >= 600
    max_act = b.cfg.MUSCLE_MAX_LENGTH + b.cfg.FIXED_HARDWARE_LENGTH
    min_act = b.cfg.MUSCLE_MIN_LENGTH + b.cfg.FIXED_HARDWARE_LENGTH
    def plane_z(length):
        out=[]
        for base, local in zip(k.base_coords,k.platform_local_coords):
            r2 = (base[0]-local[0])**2 + (base[1]-local[1])**2
            out.append(base[2]-local[2]-math.sqrt(length**2-r2))
        return out
    lower = max(plane_z(max_act))
    upper = min(plane_z(min_act))
    height = k.PLATFORM_MID_HEIGHT + request[2]
    assert .15 < (height-lower)/(upper-lower) < .22


def test_platform_labels_remain_distinct_and_v3_reports_real_source_module():
    choices = dict(platform_choices())
    assert choices['chair_v3'] == 'Chair V3'
    assert set(choices) == {'chair_v3', 'platform_v2'}
    metadata = platform_metadata('chair_v3')
    assert metadata['platform_config_module'] == PLATFORMS['chair_v3']['sop_config_module']
    assert len(metadata['platform_config_sha256']) == 64


def test_neutral_height_override_rejects_invalid_or_excessive_values():
    cfg = BaseChairConfig()
    params = PlatformParams(cfg.MUSCLE_MIN_LENGTH,cfg.MUSCLE_MAX_LENGTH,
                            cfg.FIXED_HARDWARE_LENGTH,cfg.LIMITS_1DOF_TRANFORM)
    for value in (float('nan'), float('inf'), 1000.,-1000.):
        with pytest.raises(ValueError,match='neutral'):
            Kinematics().set_geometry(cfg.base_coords,cfg.platform_coords,params,0,None,
                                      neutral_height_offset_mm=value)


def test_live_backend_and_offline_use_same_midpoint():
    from core.platform_controller import SopPlatformBackend
    class FakeMuscleOutput:
        def __init__(self,*args,**kwargs):
            self.sent_pressures=[0]*6
        def set_muscle_lengths(self, lengths):
            pass
    with patch('output.muscle_output.MuscleOutput', FakeMuscleOutput):
        live=SopPlatformBackend(PLATFORMS['chair_v3']['sop_config_module'],festo_ip='192.0.2.1')
    offline=make_backend('chair_v3')
    assert live.k.PLATFORM_MID_HEIGHT == pytest.approx(offline.k.PLATFORM_MID_HEIGHT)
    for norm in ([0]*6, [0,0,-.82,0,0,0], [.1,0,-.1,.1,.1,.1]):
        real=offline.k.norm_to_real(norm)
        assert np.allclose(live.k.inverse_kinematics(real,return_lengths=True)[1],
                           offline.k.inverse_kinematics(real,return_lengths=True)[1])
