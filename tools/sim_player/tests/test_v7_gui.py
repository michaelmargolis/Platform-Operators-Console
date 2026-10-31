# test_v7_gui.py ver 1
"""Qt6 Settings, menu and research-dock checks (Windows; skipped if no Qt6)."""
from __future__ import annotations

import os
import sys

import pytest


def test_settings_are_visible_and_angular_docks_available():
    if sys.platform.startswith('linux') and not (os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY')):
        os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    qt = pytest.importorskip('PyQt6.QtWidgets')
    pytest.importorskip('PyQt6.QtMultimedia')
    from tools.sim_player.app import ReplayWindow
    from tools.sim_player.options_dialog import PlayerOptionsDialog
    from tools.sim_player.settings import VisualizerSettings
    app=qt.QApplication.instance() or qt.QApplication(['sim_player_v7_gui_test'])
    window=ReplayWindow()
    try:
        file_actions=[action.text() for action in window.menuBar().actions()[0].menu().actions()]
        assert 'Settings…' in file_actions
        assert 'Options…' not in file_actions
        assert 'comparison' in window.panels
        assert window.cue_box.count()==3
        assert window.cue_box.itemData(0)=='euler'
        assert window.cue_box.itemData(1)=='sine'
        assert window.cue_box.itemData(2)=='gravity'
        assert window.twin_status.text().startswith('TWIN ')
        window.restore_default_layout()
        dialog=PlayerOptionsDialog(VisualizerSettings())
        try:
            assert not dialog.enabled_box.currentData()
            assert dialog.enabled_box.currentText().startswith('Off')
            assert not dialog.address.isEnabled()
            assert not dialog.port.isEnabled()
            dialog.enabled_box.setCurrentIndex(1)
            assert dialog.address.isEnabled()
            assert dialog.port.isEnabled()
            dialog._accept_validated()
            assert dialog.selected_settings().enabled
        finally:
            dialog.close()
        app.processEvents()
    finally:
        window.close()
