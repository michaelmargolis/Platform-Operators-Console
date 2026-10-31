# test_qt6_api_migration_v5.py ver 1
"""Regression check for the Qt5-to-Qt6 API migration.

Static checks run in environments without PyQt6; an optional GUI test exercises
real PyQt6 shortcuts and QMouseEvents when the binding is installed.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_migrated_qt6_api_names():
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    backend = (ROOT / "video_backend.py").read_text(encoding="utf-8")
    ui = (ROOT / "player_ui.py").read_text(encoding="utf-8")
    panels = (ROOT / "panels.py").read_text(encoding="utf-8")
    assert "QKeySequence(QKeySequence.StandardKey.Open)" in ui
    tree = ast.parse(app + "\n" + ui + "\n" + panels)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        attr = node.func
        # QMouseEvent.x() is a removed / deprecated Qt5-style access. Calls
        # to QPointF.x(), such as bar.center().x(), remain valid.
        if isinstance(attr.value, ast.Name) and attr.value.id == "event":
            assert attr.attr not in ("x", "y", "pos", "globalPos"), (attr.attr, node.lineno)
        if isinstance(attr.value, ast.Name) and attr.value.id == "QKeySequence":
            assert attr.attr not in ("Open", "Save", "Copy", "Paste", "Close"), node.lineno
    assert not any("PyQt5" in line for line in app.splitlines() if line.strip().startswith(("from ", "import ")))
    for source in (ui, panels, backend):
        assert not any("PyQt5" in line for line in source.splitlines()
                       if line.strip().startswith(("from ", "import ")))
    assert not any(old in app for old in (".exec_()", "Qt.Horizontal", "Qt.LeftButton", "QFileDialog.ExistingFile"))


def test_qt6_menu_and_mouse_handlers_with_real_widgets():
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    widgets = pytest.importorskip("PyQt6.QtWidgets")
    core = pytest.importorskip("PyQt6.QtCore")
    gui = pytest.importorskip("PyQt6.QtGui")
    test = pytest.importorskip("PyQt6.QtTest")

    from tools.sim_player.app import ReplayWindow, TimelineMarkers, JumpSlider

    application = widgets.QApplication.instance() or widgets.QApplication(["sim_player_qt6_api_smoke"])
    window = ReplayWindow()
    marker_widget = TimelineMarkers()
    slider = JumpSlider(core.Qt.Orientation.Horizontal)
    try:
        action = window.menuBar().actions()[0].menu().actions()[0]
        assert action.shortcut() == gui.QKeySequence(gui.QKeySequence.StandardKey.Open)

        marker_widget.resize(460, 52)
        marker_widget.show()
        recorded = []
        marker_widget.seek_requested.connect(recorded.append)
        test.QTest.mouseMove(marker_widget, core.QPoint(120, 18))
        test.QTest.mouseClick(marker_widget, core.Qt.MouseButton.LeftButton, pos=core.QPoint(120, 18))
        assert len(recorded) == 1 and recorded[0] > 0

        slider.setRange(0, 1000)
        slider.resize(460, 45)
        slider.show()
        test.QTest.mouseClick(slider, core.Qt.MouseButton.LeftButton, pos=core.QPoint(250, 20))
        assert slider.value() > 0
        application.processEvents()
    finally:
        marker_widget.close()
        slider.close()
        window.close()
