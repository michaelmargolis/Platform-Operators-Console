# test_chair_v3_neutral_v181.py ver 1
"""Chair V3 neutral-height test and offline/live module parity."""
import math
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from config.platforms import PLATFORMS
from kinematics.kinematics_V3 import Kinematics, PlatformParams
from tools.analyze_nl2_csv import make_backend
from tools.sim_player.platform_selection import platform_choices, platform_metadata


def test_chair_v3_isolated_from_legacy_v2_and_mdx():
    assert PLATFORMS['chair_v3']['sop_config_module'] == 'kinematics.cfg_SuspendedChairV3'
    assert PLATFORMS['chair_v2']['sop_config_module'] == 'kinematics.cfg_SuspendedChair'
    a, b, mdx = (make_backend(key) for key in ('chair_v3', 'chair_v2', 'platform_v2'))
    assert a.k.NEUTRAL_HEIGHT_OFFSET_MM == 100
    assert b.k.NEUTRAL_HEIGHT_OFFSET_MM == 0
    assert mdx.k.NEUTRAL_HEIGHT_OFFSET_MM == 0
    assert a.k.PLATFORM_MID_HEIGHT == pytest.approx(b.k.PLATFORM_MID_HEIGHT + 100)
    assert b.k.PLATFORM_MID_HEIGHT == pytest.approx(216.5)
    assert mdx.k.PLATFORM_MID_HEIGHT == pytest.approx(177.26466075505908)
    assert np.allclose(a.k.base_coords, b.k.base_coords)
    assert np.allclose(a.k.platform_local_coords, b.k.platform_local_coords)
    assert a.cfg.MUSCLE_MIN_LENGTH == b.cfg.MUSCLE_MIN_LENGTH == 600
    assert a.cfg.MUSCLE_MAX_LENGTH == b.cfg.MUSCLE_MAX_LENGTH == 800
    assert a.cfg.PLATFORM_CLEARANCE_OFFSET == 0
    assert not a._workspace_hull_xy


def test_initial_blackbird_like_heave_near_one_fifth_above_lowest_level():
    b = make_backend('chair_v3')
    k = b.k
    # Typical low-station input observed in the recorded Blackbird captures.
    heave_norm = .819
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
    # Kinematic input +Heave is physically downward (FLIP_TRANSLATION[2]=-1).
    height = k.PLATFORM_MID_HEIGHT + request[2]*k.FLIP_TRANSLATION[2]
    assert .15 < (height-lower)/(upper-lower) < .22


def test_platform_labels_remain_distinct_and_v3_reports_real_source_module():
    choices = dict(platform_choices())
    assert choices['chair_v3'] == 'Chair V3'
    assert choices['chair_v2'] == 'V2 Chair'
    metadata = platform_metadata('chair_v3')
    assert metadata['platform_config_module'] == PLATFORMS['chair_v3']['sop_config_module']
    assert len(metadata['platform_config_sha256']) == 64


def test_neutral_height_override_rejects_invalid_or_excessive_values():
    cfg = make_backend('chair_v2').cfg
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
    for norm in ([0]*6, [0,0,.82,0,0,0], [.1,0,-.1,.1,.1,.1]):
        real=offline.k.norm_to_real(norm)
        assert np.allclose(live.k.inverse_kinematics(real,return_lengths=True)[1],
                           offline.k.inverse_kinematics(real,return_lengths=True)[1])
