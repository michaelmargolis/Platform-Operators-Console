# test_v8_range_slider.py ver 1
"""Optional GUI checks; run on Windows with PyQt6 installed."""
import os
import pytest


def test_markers_drag_without_playhead_seek():
    pytest.importorskip('PyQt6')
    from PyQt6.QtCore import Qt, QPoint
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication
    from tools.sim_player.panels import RangeJumpSlider

    app = QApplication.instance() or QApplication([])
    slider = RangeJumpSlider(Qt.Orientation.Horizontal)
    slider.resize(600, 32)
    slider.setRange(0, 10000)
    slider.setValue(5000)
    slider.set_playback_range(10, 2, 8)
    slider.show()
    app.processEvents()
    changes, seeks, presses = [], [], []
    slider.range_edited.connect(lambda which, when: changes.append((which, when)))
    slider.valueChanged.connect(lambda ms: seeks.append(ms))
    slider.sliderPressed.connect(lambda: presses.append(True))
    x0 = round(slider._x_at(2))
    x1 = round(slider._x_at(3))
    y = slider.height() // 2
    QTest.mousePress(slider, Qt.MouseButton.LeftButton, pos=QPoint(x0, y))
    QTest.mouseMove(slider, QPoint(x1, y))
    QTest.mouseRelease(slider, Qt.MouseButton.LeftButton, pos=QPoint(x1, y))
    assert changes and changes[-1][0] == 'start'
    assert abs(changes[-1][1] - 3) < .05
    assert not seeks and not presses
    assert slider.value() == 5000
    slider.close()


def test_controller_range_does_not_modify_video_seek_path():
    import inspect
    from pathlib import Path
    app_path = Path(__file__).resolve().parents[1] / 'app.py'
    text = app_path.read_text(encoding='utf8')
    section = text.split('    def _range_marker_edited(', 1)[1].split('    def _mark_range(', 1)[0]
    assert '_sync_video' not in section
    assert 'seek_seconds' not in section
    assert '_send_twin' not in section
