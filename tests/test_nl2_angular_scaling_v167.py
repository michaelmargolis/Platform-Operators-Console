# test_nl2_angular_scaling_v167.py ver 1
"""NL2 v167 angular scaling matches legacy without changing other axes.

Tests use synthetic NL2 axis quaternions; no simulator, Qt or Festo required.
"""
import math

import pytest

from sims.nolimits2 import config
from sims.nolimits2.sim import Sim
from tests.test_nolimits2_adapter import axis_quaternion, telemetry


def converted(quaternion, *, gx=0.0, gy=1.0, gz=0.0):
    sim = Sim.__new__(Sim)
    sim._heave_mode = config.HEAVE_MODE_VERTICAL_G
    sim._yaw_rate_from_quaternion = lambda _quat, _time: 0.0
    return sim._transform_from_telemetry(
        telemetry(quaternion=quaternion, gx=gx, gy=gy, gz=gz)
    )


def test_explicit_angular_scales_restore_legacy_gain():
    factor = config.TELEMETRY_INTENSITY_FACTOR
    assert factor == pytest.approx(0.4)
    assert config.ROLL_SCALE * factor == pytest.approx(0.6)
    assert 0.6 * config.PITCH_SCALE * factor == pytest.approx(0.6)


@pytest.mark.parametrize("angle", [-math.pi / 4, -math.pi / 6, math.pi / 6, math.pi / 4])
def test_roll_matches_legacy_magnitude_and_corrected_direction(angle):
    result = converted(axis_quaternion(2, angle))
    assert result[3] == pytest.approx(-0.6 * angle / math.pi)
    assert result[4] == pytest.approx(0.0, abs=1e-12)
    # v166 validated: a negative NL2 Z bank is right-side-down.
    if angle < 0:
        assert result[3] > 0


@pytest.mark.parametrize("angle", [-math.pi / 6, -math.pi / 12, math.pi / 12, math.pi / 6])
def test_pitch_matches_legacy_magnitude_and_direction(angle):
    result = converted(axis_quaternion(0, angle))
    assert result[4] == pytest.approx(-0.6 * angle)
    assert result[3] == pytest.approx(0.0, abs=1e-12)


def test_other_normalized_axes_unchanged():
    result = converted(axis_quaternion(2, -0.2), gx=0.25, gy=1.0, gz=0.36)
    assert result[0] == pytest.approx(math.sqrt(0.36) * config.TELEMETRY_INTENSITY_FACTOR)
    assert result[1] == pytest.approx(math.sqrt(0.25) * config.TELEMETRY_INTENSITY_FACTOR)
    assert result[2] == pytest.approx(0.0)
    assert result[5] == pytest.approx(0.0)


def test_positive_and_negative_angles_obey_normalized_clamp():
    # A 90-degree pitch should produce 0.942...; still below normalized limit.
    result = converted(axis_quaternion(0, math.pi / 2))
    assert -1.0 <= result[4] <= 1.0
    assert result[4] == pytest.approx(-0.6 * math.pi / 2)
