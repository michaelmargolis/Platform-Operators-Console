# test_qt6_gui_smoke.py ver 1
"""Optional Qt6 runtime smoke test; runs on Windows with PyQt6 installed.

Linux CI/build environments without Qt6 skip this test. It validates Qt6
widget bindings and dock setup, not actual hardware video decoding.
"""
from __future__ import annotations

import os
import sys

import pytest


def test_qt6_window_constructs_and_docks_can_be_reset():
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PyQt6.QtWidgets")
    pytest.importorskip("PyQt6.QtMultimedia")
    from tools.sim_player.app import ReplayWindow

    app = qt_widgets.QApplication.instance() or qt_widgets.QApplication(["sim_player_smoke"])
    window = ReplayWindow()
    try:
        assert window.video_backend.player is not None
        assert window.panels["axes"].isVisible() or window.panels["axes"].parent() is window
        assert window.panels["muscles"].parent() is window
        assert window.video_backend.widget is window.video_screen
        from PyQt6.QtCore import Qt
        assert window.video_screen.aspectRatioMode() == Qt.AspectRatioMode.KeepAspectRatio
        file_actions = window.menuBar().actions()[0].menu().actions()
        assert any(action.text() == "Settings…" for action in file_actions)
        assert not any("Aspect" in action.text() for action in file_actions)
        window.restore_default_layout()
    finally:
        window.close()
        # The QApplication is intentionally kept alive until test end.
