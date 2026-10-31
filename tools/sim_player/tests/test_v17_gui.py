# test_v17_gui.py ver 1
"""Qt6 integration checks for v17 startup, window resizing and v16 layout migration.

Run on Windows with the normal POC checkout and PyQt6 installed.  QSettings is
redirected to a temporary INI so tests never overwrite the player's real UI.
"""
from __future__ import annotations
import os
import sys

import pytest


def _gui_or_skip():
    if sys.platform.startswith('linux') and not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    widgets = pytest.importorskip('PyQt6.QtWidgets')
    qtcore = pytest.importorskip('PyQt6.QtCore')
    pytest.importorskip('PyQt6.QtMultimedia')
    app = widgets.QApplication.instance() or widgets.QApplication(['sim_player_v17_gui'])
    return app, widgets, qtcore


def _isolated_settings(monkeypatch, tmp_path):
    from tools.sim_player import app as player_app
    from PyQt6.QtCore import QSettings
    filepath = tmp_path / 'sim_player.ini'
    monkeypatch.setattr(player_app, 'QSettings',
                        lambda _org, _app: QSettings(str(filepath), QSettings.Format.IniFormat))
    return player_app


def test_video_uses_extra_height_while_controls_stay_bottom(tmp_path, monkeypatch):
    app, _widgets, _qtcore = _gui_or_skip()
    player_app = _isolated_settings(monkeypatch, tmp_path)
    w = player_app.ReplayWindow()
    try:
        w.show()
        w.resize(1350, 840)
        app.processEvents()
        initial_video = w.video_stack.height()
        control_h = w.control_container.height()
        w.resize(1350, 1010)
        app.processEvents()
        assert w.video_stack.height() >= initial_video + 145
        assert w.control_container.height() == control_h
        central = w.centralWidget()
        expected_bottom = central.height() - central.layout().contentsMargins().bottom()
        actual_bottom = w.control_container.y() + control_h
        assert abs(actual_bottom - expected_bottom) <= 3
        # The lowest right-hand dock occupies the remainder to just above the
        # status bar rather than ending at the old spacer boundary.
        right = [d for d in w.panels.values() if d.isVisible() and not d.isFloating()
                 and w.dockWidgetArea(d) == _qtcore.Qt.DockWidgetArea.RightDockWidgetArea]
        assert right
        assert abs(max(d.geometry().bottom() for d in right) - w.statusBar().geometry().top()) <= 25
    finally:
        w.close()


def test_v16_spacer_saved_state_is_removed_without_losing_docks(tmp_path, monkeypatch):
    app, widgets, qtcore = _gui_or_skip()
    player_app = _isolated_settings(monkeypatch, tmp_path)
    w = player_app.ReplayWindow()
    w2 = None
    try:
        w.show()
        app.processEvents()
        # Reproduce an old, separately stacked v16 layout and persist its
        # dummy spacer just as QSettings would from a Windows installation.
        tuning = w.panels['tuning']
        tuning.setFloating(False)
        w.splitDockWidget(w.panels['muscles'], tuning, qtcore.Qt.Orientation.Vertical)
        spacer = widgets.QDockWidget('', w)
        spacer.setObjectName('sim_player_right_spacer_v16')
        spacer.setWidget(widgets.QWidget(spacer))
        w.splitDockWidget(tuning, spacer, qtcore.Qt.Orientation.Vertical)
        w.resize(1350, 980)
        app.processEvents()
        w.settings.setValue('dock_layout', w.saveState())
        w.settings.setValue('geometry', w.saveGeometry())
        w.settings.sync()
        w.close()
        w2 = player_app.ReplayWindow()
        w2.show()
        app.processEvents()
        assert w2.panels['tuning'].isVisible()
        assert w2.panels['axes'].isVisible()
        assert w2.panels['muscles'].isVisible()
        # No spacer is in the QMainWindow's dock area, even if deleteLater()
        # has not yet destroyed its QObject instance.
        legacy = w2.findChildren(widgets.QDockWidget, 'sim_player_right_spacer_v16')
        assert all(w2.dockWidgetArea(d) == qtcore.Qt.DockWidgetArea.NoDockWidgetArea for d in legacy)
        right = [d for d in w2.panels.values() if d.isVisible() and not d.isFloating()
                 and w2.dockWidgetArea(d) == qtcore.Qt.DockWidgetArea.RightDockWidgetArea]
        assert abs(max(d.geometry().bottom() for d in right) - w2.statusBar().geometry().top()) <= 25
    finally:
        if w2 is not None:
            w2.close()
        w.close()
