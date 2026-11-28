# test_canonical_motion.py ver 1
"""Independent rider-frame and release wiring checks with no network output."""
import math

import numpy as np
import pytest

from config.platforms import PLATFORMS
from core.platform_controller import SopPlatformBackend
from output.fstlib.pose_sender_logic import MemoryFesto, PoseMotion
from tools.analyze_nl2_csv import make_backend


@pytest.mark.parametrize("platform,side", [("platform_v2", "right"), ("chair_v3", "left")])
def test_reference_geometry_matches_approved_valve_order(platform, side):
    cfg_module = PLATFORMS[platform]["sop_config_module"]
    backend = SopPlatformBackend(cfg_module, festo_interface=MemoryFesto(),
                                 festo_valve0_side=side)
    offline = make_backend(platform)
    assert backend.festo_valve0_side == side
    assert backend.geometry_id == offline.geometry_id
    assert backend.channel_to_pair == offline.channel_to_pair == tuple(range(6))
    assert (backend.k.base_coords[0, 1] > 0) == (side == "left")
    np.testing.assert_array_equal(backend.k.base_coords, backend.cfg.base_coords)
    np.testing.assert_array_equal(backend.k.platform_local_coords, backend.cfg.platform_coords)


@pytest.mark.parametrize("platform,side", [("platform_v2", "left"), ("chair_v3", "right")])
def test_future_wiring_is_rejected_before_pressures_are_calculated(platform, side):
    sink = MemoryFesto()
    with pytest.raises(ValueError, match="valve 0"):
        SopPlatformBackend(PLATFORMS[platform]["sop_config_module"],
                           festo_interface=sink, festo_valve0_side=side)
    assert sink.generation == 0


@pytest.mark.parametrize("platform", ["platform_v2", "chair_v3"])
def test_canonical_axes_act_directly_on_attachment_points(platform):
    k = make_backend(platform).k
    zero = (0.0,) * 6
    neutral = k.inverse_kinematics(zero).copy()
    for i in range(3):
        xyz = [0.0] * 3
        xyz[i] = 20.0
        shifted = k.inverse_kinematics((*xyz, 0, 0, 0))
        np.testing.assert_allclose(shifted, neutral + xyz, rtol=0, atol=1e-6)
    angle = math.pi / 2
    for rpy, expected in (
        ((angle, 0, 0), np.column_stack((neutral[:, 0], -neutral[:, 2], neutral[:, 1]))),
        ((0, angle, 0), np.column_stack((neutral[:, 2], neutral[:, 1], -neutral[:, 0]))),
        ((0, 0, angle), np.column_stack((-neutral[:, 1], neutral[:, 0], neutral[:, 2]))),
    ):
        np.testing.assert_allclose(k.inverse_kinematics((0, 0, 0, *rpy)), expected,
                                   rtol=0, atol=1e-6)
    _, original_lengths = k.inverse_kinematics(zero, return_lengths=True)
    _, higher_lengths = k.inverse_kinematics((0, 0, 15, 0, 0, 0), return_lengths=True)
    assert np.all(higher_lengths < original_lengths)


def test_pose_sender_positive_heave_uses_canonical_real_z():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    pose = PoseMotion("chair_v3", root, "left")
    pose.set_normalized((0, 0, 0.2, 0, 0, 0))
    requested, limited = pose.preview()
    assert requested[2] == limited[2] == pytest.approx(20.0)
    assert pose.capture.generation == 0


def test_selectable_platforms_match_the_approved_test_hardware():
    from tools.sim_player.platform_selection import platform_choices

    assert set(PLATFORMS) == set(dict(platform_choices())) == {"chair_v3", "platform_v2"}



def test_legacy_and_canonical_capture_heave_are_converted_once(tmp_path):
    from tools.analyze_nl2_csv import read_capture

    header = "pos_y,elapsed_s,norm_surge,norm_sway,norm_heave,norm_roll,norm_pitch,norm_yaw,vertical_g_heave"
    legacy = tmp_path / "legacy.csv"
    canonical = tmp_path / "canonical.csv"
    legacy.write_text(header + "\n10,0,0.1,0.2,-0.3,0.4,0.5,0.6,0.7\n", encoding="utf-8")
    canonical.write_text(header + ",transform_convention\n"
                         "10,0,0.1,0.2,0.3,0.4,0.5,0.6,-0.7,rider_xyz_rpy_v1\n", encoding="utf-8")
    assert read_capture(legacy) == read_capture(canonical) == [(0.0, 10.0, (0.1, 0.2, 0.3, 0.4, 0.5, 0.6), -0.7)]
    canonical.write_text(header + ",transform_convention\n"
                         "10,0,0.1,0.2,0.3,0.4,0.5,0.6,-0.7,unknown\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Unknown motion convention"):
        read_capture(canonical)


@pytest.mark.parametrize("field,index,point_name", [
    ("platform_coords", 0, "lower"),
    ("base_coords", 1, "upper"),
])
def test_inconsistent_attachment_side_fails_before_festo_initialization(monkeypatch, field, index, point_name):
    from unittest.mock import patch
    from kinematics import cfg_SuspendedPlatformMdx

    cfg = cfg_SuspendedPlatformMdx.PlatformConfig()
    getattr(cfg, field)[index][1] *= -1
    monkeypatch.setattr(cfg_SuspendedPlatformMdx, "PlatformConfig", lambda: cfg)
    with patch("output.muscle_output.MuscleOutput") as output:
        with pytest.raises(ValueError, match=rf"{point_name} attachment point {index}.*valve order"):
            SopPlatformBackend(PLATFORMS["platform_v2"]["sop_config_module"], festo_valve0_side="right")
    output.assert_not_called()


def test_ground_loading_floor_hull_keeps_cfg_axes_and_clearance():
    from kinematics.cfg_SuspendedPlatformMdx import PlatformConfig

    backend = make_backend("platform_v2")
    pose = (0.0, 0.0, -80.0, math.radians(8), math.radians(5), 0.0)
    assert tuple(backend._workspace_hull_xy) == PlatformConfig.PLATFORM_UNDERSIDE_HULL_XY
    assert backend._floor_clearance_for_real_transform(pose) < 0.0
    corrected, _ = backend._limit_workspace(pose)
    assert corrected[2] > pose[2]
    assert backend._floor_clearance_for_real_transform(corrected) == pytest.approx(15.5, abs=1e-6)
