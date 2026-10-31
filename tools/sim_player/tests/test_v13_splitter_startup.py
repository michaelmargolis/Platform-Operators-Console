# test_v13_splitter_startup.py ver 1
"""Historical v13 regression: no longer has a splitter handle after v17."""
from pathlib import Path


def test_no_splitter_handle_or_handle_event_filter_in_player():
    ui = (Path(__file__).resolve().parents[1] / 'player_ui.py').read_text(encoding='utf-8')
    assert 'VideoResizeSplitter' not in ui
    assert 'self.video_splitter' not in ui
    assert 'watch_handle()' not in ui
    assert 'layout.addWidget(self.video_stack, 1)' in ui


def test_qt_vertical_layout_allocates_remaining_height_if_available():
    import os
    import sys
    import pytest
    if sys.platform.startswith('linux') and not (os.getenv('DISPLAY') or os.getenv('WAYLAND_DISPLAY')):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    qt = pytest.importorskip('PyQt6.QtWidgets')
    app = qt.QApplication.instance() or qt.QApplication(['sim_player_vertical_layout_check'])
    widget = qt.QWidget()
    layout = qt.QVBoxLayout(widget)
    upper = qt.QWidget()
    controls = qt.QWidget()
    controls.setFixedHeight(90)
    layout.addWidget(upper, 1)
    layout.addWidget(controls, 0)
    try:
        widget.resize(500, 460)
        widget.show()
        app.processEvents()
        before = upper.height()
        widget.resize(500, 560)
        app.processEvents()
        assert upper.height() >= before + 90
        assert controls.height() == 90
    finally:
        widget.close()
