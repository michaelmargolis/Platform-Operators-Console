# test_festo_valve0_side_v250.py ver 1
"""Machine-local Festo valve-0 side geometry normalisation."""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from core.platform_controller import (
    SopPlatformBackend,
    normalize_festo_channel_geometry,
)
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
    "module,side",
    [
        ("kinematics.cfg_SuspendedPlatformMdx", "right"),
        ("kinematics.cfg_SuspendedChairV3", "left"),
        ("kinematics.cfg_SuspendedChair", "left"),
        ("kinematics.cfg_SuspendedChairFalcon", "right"),
    ],
)
def test_matching_valve0_side_is_exact_geometry_noop(monkeypatch, module, side):
    old = backend(monkeypatch, module)
    current = backend(monkeypatch, module, side)

    assert current.geometry_valve0_source_side == side
    assert current.festo_valve0_side == side
    assert current.geometry_y_mirrored is False
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


def test_opposite_side_mirrors_y_only_and_keeps_channel_indices(monkeypatch):
    right = backend(monkeypatch, "kinematics.cfg_SuspendedPlatformMdx", "right")
    left = backend(monkeypatch, "kinematics.cfg_SuspendedPlatformMdx", "left")

    assert left.geometry_valve0_source_side == "right"
    assert left.festo_valve0_side == "left"
    assert left.geometry_y_mirrored is True

    assert np.array_equal(left.k.base_coords[:, 0], right.k.base_coords[:, 0])
    assert np.array_equal(left.k.base_coords[:, 2], right.k.base_coords[:, 2])
    assert np.array_equal(left.k.base_coords[:, 1], -right.k.base_coords[:, 1])
    assert np.array_equal(
        left.k.platform_local_coords[:, 0], right.k.platform_local_coords[:, 0]
    )
    assert np.array_equal(
        left.k.platform_local_coords[:, 2], right.k.platform_local_coords[:, 2]
    )
    assert np.array_equal(
        left.k.platform_local_coords[:, 1], -right.k.platform_local_coords[:, 1]
    )

    # Index 0 remains index 0; only its rider-side coordinate changes.
    assert left.k.base_coords[0, 0] == right.k.base_coords[0, 0]
    assert left.k.base_coords[0, 1] == -right.k.base_coords[0, 1]
    assert left.k.platform_local_coords[0, 0] == right.k.platform_local_coords[0, 0]
    assert left.k.platform_local_coords[0, 1] == -right.k.platform_local_coords[0, 1]


def test_ground_loading_floor_hull_is_mirrored_with_channel_geometry(monkeypatch):
    right = backend(monkeypatch, "kinematics.cfg_SuspendedPlatformMdx", "right")
    left = backend(monkeypatch, "kinematics.cfg_SuspendedPlatformMdx", "left")

    right_hull = np.asarray(right._workspace_hull_xy)
    left_hull = np.asarray(left._workspace_hull_xy)
    assert np.array_equal(left_hull[:, 0], right_hull[:, 0])
    assert np.array_equal(left_hull[:, 1], -right_hull[:, 1])


def test_valve0_upper_and_lower_must_identify_same_side():
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
    with pytest.raises(RuntimeError, match="opposite Y sides"):
        normalize_festo_channel_geometry(cfg, "right")


def test_nonconsecutive_channel_side_order_fails_closed():
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
    with pytest.raises(RuntimeError, match="supported consecutive"):
        normalize_festo_channel_geometry(cfg, "right")
