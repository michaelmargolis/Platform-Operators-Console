# test_v10_gui.py ver 1
"""Run on Windows with PyQt6; event marker selection and anomaly dock wiring."""
import os
import sys
import pytest


def test_single_marker_lane_and_anomaly_dock():
    if sys.platform.startswith('linux') and not (os.getenv('DISPLAY') or os.getenv('WAYLAND_DISPLAY')):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    qt = pytest.importorskip('PyQt6.QtWidgets')
    pytest.importorskip('PyQt6.QtMultimedia')
    from tools.sim_player.app import ReplayWindow
    app = qt.QApplication.instance() or qt.QApplication(['sim_player_v10_smoke'])
    window = ReplayWindow()
    try:
        assert window.panels['anomalies'].widget() is window.anomaly_panel
        assert window.marker_mode_box.count() == 2
        assert [window.marker_mode_box.itemData(i) for i in range(2)] == ['ride','kinematic']
        assert window.marker_mode_box.currentData() == 'kinematic'
        assert window.markers.category == 'kinematic'
        assert 'KINEMATIC' in window.marker_title.text()
        window.marker_mode_box.setCurrentIndex(0)
        assert 'RIDE EVENTS' in window.marker_title.text()
        assert window.markers.category == 'ride'
        window.marker_mode_box.setCurrentIndex(1)
        assert window.markers.events == ()
        assert 'KINEMATIC' in window.marker_title.text()
        assert window.anomaly_panel.step_box.value() == 50
        assert window.anomaly_panel.floor_box.value() == 30
        assert window.panels['anomalies'].toggleViewAction() is not None
    finally:
        window.close()


def test_anomaly_panel_selection_and_examine_signals():
    if sys.platform.startswith('linux') and not (os.getenv('DISPLAY') or os.getenv('WAYLAND_DISPLAY')):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    qt = pytest.importorskip('PyQt6.QtWidgets')
    from tools.sim_player.anomaly_detection import AnomalyEvent
    from tools.sim_player.anomaly_panel import AnomalyPanel
    app = qt.QApplication.instance() or qt.QApplication(['sim_player_v10_panel'])
    panel = AnomalyPanel()
    ev = AnomalyEvent(category='muscle_step', start_s=5, end_s=5.05,
                      seconds=5.05, name='Large muscle step', color='#f00',
                      details='M2 changed 87 mm', muscles='M2', magnitude=87,
                      units='mm', frame_count=1, severity='alert')
    seeks, examines = [], []
    panel.seek_requested.connect(seeks.append)
    panel.examine_requested.connect(examines.append)
    panel.set_events((ev,))
    assert panel.table.rowCount() == 1
    panel.table.setCurrentCell(0, 0)
    assert seeks == [5.05]
    panel.examine_button.click()
    assert examines == [ev]
    panel.close()
