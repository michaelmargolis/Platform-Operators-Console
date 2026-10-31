# test_pose_sender_v164.py ver 1
"""Regression checks for pressure-independent arming and output."""
from pathlib import Path


def test_arm_has_no_pressure_freshness_gate():
    source = (Path(__file__).resolve().parents[1] / "output/fstlib/pose_sender.py").read_text(encoding="utf-8")
    arm = source.split("    def _arm(self):", 1)[1].split("    def _unpark(self):", 1)[0]
    assert "monitor.fresh" not in arm
    assert "monitor.actual" not in arm
    assert "self.commanded = [0] * 6" in arm
    assert "self.armed = True" in arm


def test_tick_does_not_fault_on_missing_pressure_response():
    source = (Path(__file__).resolve().parents[1] / "output/fstlib/pose_sender.py").read_text(encoding="utf-8")
    tick = source.split("    def _tick(self):", 1)[1].split("    def _refresh_ui(self):", 1)[0]
    assert "monitor.fresh" not in tick
    assert "self.model.tick()" in tick
    assert 'self.model.mode == "parked"' in tick


def test_arm_button_no_longer_depends_on_feedback():
    source = (Path(__file__).resolve().parents[1] / "output/fstlib/pose_sender.py").read_text(encoding="utf-8")
    refresh = source.split("    def _refresh_ui(self):", 1)[1].split("    def _arm(self):", 1)[0]
    line = next(line for line in refresh.splitlines() if "self.arm_button.setEnabled" in line)
    assert "fresh" not in line
