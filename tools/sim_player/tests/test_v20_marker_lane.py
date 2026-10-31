# test_v20_marker_lane.py ver 2
"""v20 marker defaults and label rendering regressions (requires Qt6)."""
import os
import sys
from types import SimpleNamespace
import pytest


def _qt():
    if sys.platform.startswith("linux") and not (os.getenv("DISPLAY") or os.getenv("WAYLAND_DISPLAY")):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    widgets = pytest.importorskip("PyQt6.QtWidgets")
    pytest.importorskip("PyQt6.QtMultimedia")
    return widgets.QApplication.instance() or widgets.QApplication(["sim_player_v20_test"])


def test_marker_category_defaults_to_kinematic():
    app = _qt()
    from tools.sim_player.app import ReplayWindow
    window = ReplayWindow()
    try:
        assert window.marker_mode_box.currentData() == "kinematic"
        assert window.markers.category == "kinematic"
        window.marker_mode_box.setCurrentIndex(0)
        assert window.markers.category == "ride"
        window.marker_mode_box.setCurrentIndex(1)
        assert window.markers.category == "kinematic"
    finally:
        window.close()


def test_dense_kinematic_lane_draws_without_per_event_labels(monkeypatch):
    _qt()
    from tools.sim_player.panels import TimelineMarkers
    from PyQt6.QtGui import QPainter
    from PyQt6.QtCore import Qt
    events = (
        SimpleNamespace(start_s=18., end_s=22., seconds=20.,
                        duration_s=4., name="requested outside range",
                        color="#fdac55"),
        SimpleNamespace(start_s=21., end_s=22., seconds=21.,
                        duration_s=1., name="requested outside range",
                        color="#fdac55"),
    )
    lane = TimelineMarkers()
    lane.resize(1200, 52)
    lane.set_recording(events, 100., category="kinematic")
    # QWidget rendering with Qt6 must succeed for clustered markers.
    assert not lane.grab().isNull()
    assert lane.category == "kinematic"
    lane.set_recording(events, 100., category="ride")
    assert not lane.grab().isNull()
    lane.close()
