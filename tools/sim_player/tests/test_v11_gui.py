# test_v11_gui.py ver 1
"""Optional PyQt6-on-Windows tests for slider actions and red-request bars."""
from __future__ import annotations
import os
import sys
import pytest


def test_motion_tuning_dock_and_gain_change_signal():
    if sys.platform.startswith('linux') and not (os.getenv('DISPLAY') or os.getenv('WAYLAND_DISPLAY')):
        os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    widgets=pytest.importorskip('PyQt6.QtWidgets')
    pytest.importorskip('PyQt6.QtMultimedia')
    from tools.sim_player.app import ReplayWindow
    app=widgets.QApplication.instance() or widgets.QApplication(['sim_player_v11_smoke'])
    window=ReplayWindow()
    try:
        assert window.panels['tuning'].widget() is window.tuning_panel
        assert window.panels['tuning'].toggleViewAction() is not None
        assert len(window.tuning_panel.sliders)==6
        assert all(not slider.isEnabled() for slider in window.tuning_panel.sliders)
        window.tuning_panel.set_analysis_enabled(True)
        captured=[]
        window.tuning_panel.gain_changed.connect(captured.append)
        window.tuning_panel.sliders[3].setValue(150)
        window.tuning_panel._commit()
        assert captured[-1] == (100.,100.,100.,150.,100.,100.)
        window.tuning_panel.set_gains((100,)*6)
        assert window.tuning_panel.gain_percent() == (100.,)*6
    finally:
        window.close()


def test_length_bar_out_of_range_indication_and_no_fake_out_of_range():
    if sys.platform.startswith('linux') and not (os.getenv('DISPLAY') or os.getenv('WAYLAND_DISPLAY')):
        os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    widgets=pytest.importorskip('PyQt6.QtWidgets')
    from tools.sim_player.panels import ResponsiveBars
    app=widgets.QApplication.instance() or widgets.QApplication(['sim_player_v11_bars'])
    bars=ResponsiveBars(False)
    bars.update_values([750,800,900,900,900,900],750,1000,
                       requested=[740,800,900,900,900,900],
                       request_oob=[True,False,False,False,False,False])
    assert bars.request_oob[0]
    assert 'M1: request 740.0 mm' in bars.toolTip()
    bars.update_values([800]*6,750,1000,requested=[800]*6)
    assert not any(bars.request_oob)
    assert 'M1:' not in bars.toolTip()
    bars.close()
