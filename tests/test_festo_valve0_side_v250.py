# test_festo_valve0_side_v250.py ver 2
"""Machine-local Festo valve-0 side geometry normalisation."""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from core.platform_controller import SopPlatformBackend
from kinematics import cfg_SuspendedPlatformMdx
from output.fstlib.pose_sender_logic import MemoryFesto


ROOT = Path(__file__).resolve().parents[1]


def backend(monkeypatch, module, side=None):
    monkeypatch.chdir(ROOT)
    return SopPlatformBackend(
        module,
        festo_interface=MemoryFesto(),
        festo_valve0_side=side,
    )


@pytest.mark.parametrize(
    "module,side,supported",
    [
        ("kinematics.cfg_SuspendedPlatformMdx", "right", True),
        ("kinematics.cfg_SuspendedChairV3", "left", True),
        ("kinematics.cfg_SuspendedChair", "left", False),
        ("kinematics.cfg_SuspendedChairFalcon", "right", False),
    ],
)
def test_matching_valve0_side_is_exact_geometry_noop(monkeypatch, module, side, supported):
    if not supported:
        with pytest.raises(ValueError, match="Unsupported platform geometry"):
            backend(monkeypatch, module, side)
        return
    old = backend(monkeypatch, module)
    current = backend(monkeypatch, module, side)

    assert current.channel_to_pair == tuple(range(6))
    assert current.festo_valve0_side == side
    np.testing.assert_array_equal(current.k.base_coords, current.cfg.base_coords)
    np.testing.assert_array_equal(current.k.platform_local_coords, current.cfg.platform_coords)
    assert np.array_equal(current.k.base_coords, old.k.base_coords)
    assert np.array_equal(current.k.platform_local_coords, old.k.platform_local_coords)

    requests = (
        [0.0] * 6,
        [10.0, -7.0, 5.0, 0.03, -0.02, 0.04],
        [-8.0, 6.0, -4.0, -0.02, 0.03, -0.03],
    )
    for request in requests:
        old_lengths = old.k.inverse_kinematics(request, return_lengths=True)[1]
        new_lengths = current.k.inverse_kinematics(request, return_lengths=True)[1]
        assert np.array_equal(new_lengths, old_lengths)


def test_unapproved_opposite_side_preserves_cad_pair_indices(monkeypatch):
    from unittest.mock import patch

    module = "kinematics.cfg_SuspendedPlatformMdx"
    right = backend(monkeypatch, module, "right")
    upper, lower = right.k.base_coords.copy(), right.k.platform_local_coords.copy()

    with patch("output.muscle_output.MuscleOutput") as output:
        with pytest.raises(ValueError, match="valve 0"):
            backend(monkeypatch, module, "left")
    output.assert_not_called()
    np.testing.assert_array_equal(right.k.base_coords, upper)
    np.testing.assert_array_equal(right.k.platform_local_coords, lower)
    assert right.channel_to_pair == tuple(range(6))


def test_ground_loading_floor_hull_stays_in_the_rider_frame(monkeypatch):
    right = backend(monkeypatch, "kinematics.cfg_SuspendedPlatformMdx", "right")
    expected = cfg_SuspendedPlatformMdx.PlatformConfig.PLATFORM_UNDERSIDE_HULL_XY
    np.testing.assert_array_equal(right._workspace_hull_xy, expected)
    pose = [0, 0, -80, 0.1, 0.07, 0]
    clearance = right._floor_clearance_for_real_transform(pose)
    with pytest.raises(ValueError, match="valve 0"):
        backend(monkeypatch, "kinematics.cfg_SuspendedPlatformMdx", "left")
    np.testing.assert_array_equal(right._workspace_hull_xy, expected)
    assert right._floor_clearance_for_real_transform(pose) == clearance


def test_valve0_upper_and_lower_must_identify_same_side(monkeypatch):
    cfg = SimpleNamespace(
        base_coords=[
            [1.0, -3.0, 1.0],
            [0.0, -4.0, 1.0],
            [-1.0, -2.0, 1.0],
        ],
        platform_coords=[
            [1.0, 3.0, 0.0],
            [0.0, -4.0, 0.0],
            [-1.0, -2.0, 0.0],
        ],
    )
    # The active Ground-Loading CAD has six pairs. Keep the mismatched
    # front pair from the original fixture and supply the opposite side.
    cfg.base_coords.extend([list(point) for point in (
        [-1.0, 2.0, 1.0], [0.0, 4.0, 1.0], [1.0, 3.0, 1.0])])
    cfg.platform_coords.extend([list(point) for point in (
        [-1.0, 2.0, 0.0], [0.0, 4.0, 0.0], [1.0, 3.0, 0.0])])
    monkeypatch.setattr(cfg_SuspendedPlatformMdx, "PlatformConfig", lambda: cfg)
    with pytest.raises(ValueError, match="lower attachment point 0.*valve order"):
        backend(monkeypatch, "kinematics.cfg_SuspendedPlatformMdx", "right")


def test_nonconsecutive_channel_side_order_fails_closed(monkeypatch):
    cfg = SimpleNamespace(
        base_coords=[
            [1.0, -3.0, 1.0],
            [0.0, 4.0, 1.0],
            [-1.0, -2.0, 1.0],
            [-1.0, 2.0, 1.0],
            [0.0, 4.0, 1.0],
            [1.0, 3.0, 1.0],
        ],
        platform_coords=[
            [1.0, -3.0, 0.0],
            [0.0, -4.0, 0.0],
            [-1.0, -2.0, 0.0],
            [-1.0, 2.0, 0.0],
            [0.0, 4.0, 0.0],
            [1.0, 3.0, 0.0],
        ],
    )
    monkeypatch.setattr(cfg_SuspendedPlatformMdx, "PlatformConfig", lambda: cfg)
    with pytest.raises(ValueError, match="upper attachment point 1.*valve order"):
        backend(monkeypatch, "kinematics.cfg_SuspendedPlatformMdx", "right")
