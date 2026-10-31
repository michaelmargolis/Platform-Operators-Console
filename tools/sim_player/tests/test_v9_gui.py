# test_v9_gui.py ver 1
"""Optional Windows Qt6 smoke test for the v9 compact range controls."""
import os
import sys
import pytest


def test_v9_controls_and_three_modes():
    if sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt = pytest.importorskip("PyQt6.QtWidgets")
    pytest.importorskip("PyQt6.QtMultimedia")
    from tools.sim_player.app import ReplayWindow
    app = qt.QApplication.instance() or qt.QApplication(["sim_player_v9_smoke"])
    window = ReplayWindow()
    try:
        assert [window.range_mode_box.itemData(i) for i in range(3)] == ["full", "range", "loop"]
        assert window.range_mode_box.currentText() == "Full ride"
        assert window.loop_delay_box.currentData() == 1.0
        assert not window.loop_delay_box.isEnabled()  # No capture loaded.
        assert not window.loop_feedback_label.isVisible()
        assert window.slider._mode == "full"
        assert window.loop_range.mode == "full"
        assert window.panel_button.text().startswith("Panels")
    finally:
        window.close()
