# test_v12_gui.py ver 1
"""Optional Windows/Qt6 smoke checks: fixed controls, video and peak rendering."""
import os
import sys
import pytest


def app_or_skip():
    if sys.platform.startswith('linux') and not (os.getenv('DISPLAY') or os.getenv('WAYLAND_DISPLAY')):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    widgets = pytest.importorskip('PyQt6.QtWidgets')
    pytest.importorskip('PyQt6.QtMultimedia')
    return widgets.QApplication.instance() or widgets.QApplication(['sim_player_v12_gui'])


def test_main_window_resize_changes_video_not_control_strip():
    app = app_or_skip()
    from tools.sim_player.app import ReplayWindow
    from PyQt6.QtWidgets import QSizePolicy
    window = ReplayWindow()
    try:
        window.show()
        window.resize(1320, 820)
        app.processEvents()
        before_video = window.video_stack.height()
        before_controls = window.control_container.height()
        assert window.video_stack.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Expanding
        assert window.control_container.minimumHeight() == window.control_container.maximumHeight()
        window.resize(1320, 940)
        app.processEvents()
        assert window.video_stack.height() >= before_video + 90
        assert window.control_container.height() == before_controls
        assert window.control_container.isVisible()
        assert window.video_stack.minimumHeight() == 120
    finally:
        window.close()


def test_muscle_peak_lines_and_red_overrange_flags_render():
    app_or_skip()
    from tools.sim_player.panels import ResponsiveBars
    bars = ResponsiveBars(False)
    try:
        bars.resize(520, 330)
        bars.update_values((750, 850, 900, 950, 1000, 850), 750, 1000,
                           requested=(730, 1025, 900, 950, 1000, 850),
                           request_oob=(True, True, False, False, False, False),
                           extrema=((730, 800), (800, 1025), (850, 910),
                                    (800, 950), (875, 1000), (790, 900)),
                           plot_bounds=(710, 1040))
        assert bars.extrema[0] == (730, 800)
        assert bars.plot_bounds == (710, 1040)
        assert 'requested min 730.0' in bars.toolTip()
        image = bars.grab().toImage()
        assert not image.isNull()
        bars.clear_extrema()
        assert bars.extrema is None
    finally:
        bars.close()
