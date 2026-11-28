# test_pose_sender_v165.py ver 2
"""Checks for the v165 pose sender UI convention helpers."""

from pathlib import Path

import pytest

from output.fstlib.pose_sender_logic import PoseMotion

ROOT = Path(__file__).resolve().parents[1]


def test_user_to_internal_preserves_all_canonical_dofs():
    values = (0.25, -0.50, 0.75, -0.10, 0.20, -0.30)
    pose = PoseMotion("chair_v3", ROOT, "left")
    pose.set_normalized(values)
    assert pose.normalized == values


def test_real_preview_preserves_all_canonical_dofs():
    values = (10.0, -20.0, 30.0, 0.1, -0.1, 0.12)
    pose = PoseMotion("chair_v3", ROOT, "left")
    pose.set_normalized(value / limit for value, limit in zip(values, pose.backend.k.limits_1dof))
    requested, applied = pose.preview()
    assert requested == pytest.approx(values)
    assert applied == pytest.approx(values)
    assert pose.capture.generation == 0


def test_pose_sender_uses_right_hand_side_layout_and_canonical_input():
    source = (Path(__file__).resolve().parents[1] / 'output/fstlib/pose_sender.py').read_text(encoding='utf-8')
    assert 'root = QtWidgets.QHBoxLayout(root_widget)' in source
    assert 'side_col = QtWidgets.QVBoxLayout()' in source
    assert 'self.model.set_normalized(user_values)' in source
