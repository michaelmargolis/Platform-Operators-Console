# test_mdx_roll_channels_v166.py ver 2
"""MDX rider-frame +Roll and CAD-indexed Festo valve assignments."""

import math
from pathlib import Path

import numpy as np
import pytest

from core.platform_controller import SopPlatformBackend
from kinematics.cfg_SuspendedPlatformMdx import PlatformConfig
from output.fstlib.pose_sender_logic import MemoryFesto
from sims.nolimits2 import config as nlconfig
from sims.nolimits2.sim import Sim
from tests.test_nolimits2_adapter import telemetry, axis_quaternion


ROOT = Path(__file__).resolve().parents[1]


def backend(monkeypatch):
    monkeypatch.chdir(ROOT)
    return SopPlatformBackend(
        "kinematics.cfg_SuspendedPlatformMdx",
        festo_interface=MemoryFesto(),
    )


def test_physical_channel_order_matches_cad():
    cfg = PlatformConfig()
    assert cfg.AXIS_FLIP_MASK == (1, 1, -1, 1, 1, 1)
    assert [p[1] for p in cfg.platform_coords[:3]] == [-166.0, -608.0, -536.0]
    assert all(pt[1] < 0 for pt in cfg.platform_coords[:3])
    assert all(pt[1] > 0 for pt in cfg.platform_coords[3:])
    assert cfg.platform_coords[0][:2] == [716.0, -166.0]
    assert cfg.base_coords[0][:2] == [413.0, -566.0]


def test_positive_roll_moves_right_side_down_and_left_side_up(monkeypatch):
    b = backend(monkeypatch)
    assert b.k.FLIP_ROTATION.tolist() == [1.0, 1.0, 1.0]
    half_limit = b.cfg.LIMITS_1DOF_TRANFORM[3] / 2.0
    neutral = np.asarray(b.k.inverse_kinematics([0.0]*6), dtype=float)
    roll_pose, roll_actuator = b.k.inverse_kinematics(
        [0.0, 0.0, 0.0, half_limit, 0.0, 0.0], return_lengths=True,
    )
    dz = np.asarray(roll_pose)[:,2] - neutral[:,2]
    # Each valve is on the physically documented side of the suspended deck.
    assert np.all(dz[:3] < 0), dz
    assert np.all(dz[3:] > 0), dz
    neutral_lengths = b.k.inverse_kinematics([0.0]*6,return_lengths=True)[1]
    length_delta = np.asarray(roll_actuator)-np.asarray(neutral_lengths)
    # Right-side muscles lengthen; left-side muscles contract.
    assert np.all(length_delta[:3] > 0), length_delta
    assert np.all(length_delta[3:] < 0), length_delta


def test_positive_roll_sends_pressure_to_physical_channels(monkeypatch):
    b = backend(monkeypatch)
    b.move([0., 0., 0., 0.5, 0., 0.])
    assert b.sent_pressures and len(b.sent_pressures) == 6
    # Compare against neutral to avoid assuming equal calibration across valves.
    roll_pressures = tuple(b.sent_pressures)
    b.move([0.]*6)
    neutral_pressures = tuple(b.sent_pressures)
    assert max(roll_pressures[:3]) < max(neutral_pressures[:3])
    assert min(roll_pressures[3:]) > min(neutral_pressures[3:])


def test_nl2_right_bank_becomes_positive_platform_roll():
    # The NL2 +Z quaternion produces the opposite-signed physical bank.
    # Sign reversal in the NL2 adapter is intentional and verified visually.
    sim = Sim.__new__(Sim)
    sim._heave_mode = "g-force"
    sim._yaw_rate_from_quaternion = lambda _quat, _at: 0.0
    tm = telemetry(quaternion=axis_quaternion(2, -0.2))
    transform = Sim._transform_from_telemetry(sim, tm)
    assert transform[3] > 0.0
    assert transform[3] == pytest.approx(
        0.2 / math.pi * nlconfig.ROLL_SCALE * nlconfig.TELEMETRY_INTENSITY_FACTOR
    )


def test_offline_nl2_analyzer_uses_mdx_axis_mask():
    # Avoid importing this optional GUI/Tkinter analysis tool on Pi OS Lite.
    source = (ROOT / "tools/analyze_nl2_csv.py").read_text(encoding="utf-8")
    assert 'axis_flip_mask = getattr(cfg, "AXIS_FLIP_MASK", None)' in source
    assert 'k.set_axis_flip_mask(axis_flip_mask)' in source
