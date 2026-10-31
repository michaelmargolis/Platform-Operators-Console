# test_pose_sender_v165.py ver 1
"""Checks for the v165 pose sender UI convention helpers."""

from pathlib import Path

from output.fstlib.pose_sender_logic import (
    internal_to_user_real,
    user_to_internal_normalized,
)


def test_user_to_internal_normalized_reverses_heave_only():
    values = (0.25, -0.50, 0.75, -0.10, 0.20, -0.30)
    converted = user_to_internal_normalized(values)
    assert converted == (0.25, -0.50, -0.75, -0.10, 0.20, -0.30)


def test_internal_to_user_real_reverses_heave_only():
    values = (10.0, -20.0, -30.0, 0.1, -0.2, 0.3)
    converted = internal_to_user_real(values)
    assert converted == (10.0, -20.0, 30.0, 0.1, -0.2, 0.3)


def test_pose_sender_uses_right_hand_side_layout_and_heave_conversion():
    source = (Path(__file__).resolve().parents[1] / 'output/fstlib/pose_sender.py').read_text(encoding='utf-8')
    assert 'root = QtWidgets.QHBoxLayout(root_widget)' in source
    assert 'side_col = QtWidgets.QVBoxLayout()' in source
    assert 'self.model.set_normalized(user_to_internal_normalized(user_values))' in source
