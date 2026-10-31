# test_v14_window_resize.py ver 1
"""Layout regression: Qt video natural size must not lock window height."""
import os
import sys
from pathlib import Path
import pytest

PLAYER = Path(__file__).resolve().parents[1]


def test_native_video_minimum_does_not_control_main_window():
    ui = (PLAYER / 'player_ui.py').read_text(encoding='utf-8')
    app = (PLAYER / 'app.py').read_text(encoding='utf-8')
    assert 'class CompactVideoStack(QStackedWidget)' in ui
    assert 'def minimumSizeHint(self):' in ui
    assert 'self.video_stack.setMinimumSize(320, 120)' in ui
    assert 'self.video_screen.setSizePolicy(QSizePolicy.Policy.Ignored' in ui
    assert 'self.control_container.setFixedHeight(height)' in ui  # v16 fixed controls
    assert 'layout.addWidget(self.video_stack, 1)' in ui
    assert 'layout.addWidget(self.control_container, 0)' in ui
    assert 'def _resize_window_to_video(' not in app


def _qt_app():
    if sys.platform.startswith('linux') and not (os.getenv('DISPLAY') or os.getenv('WAYLAND_DISPLAY')):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    qt = pytest.importorskip('PyQt6.QtWidgets')
    pytest.importorskip('PyQt6.QtMultimedia')
    return qt.QApplication.instance() or qt.QApplication(['sim_player_v14_window_test'])


def test_main_window_can_shrink_and_video_follows_window():
    app = _qt_app()
    from tools.sim_player.app import ReplayWindow
    w = ReplayWindow()
    try:
        w.show()
        w.resize(1320, 900)
        app.processEvents()
        assert w.video_stack.minimumSizeHint().height() <= 120
        old_controls = w.control_container.height()
        old_video = w.video_stack.height()
        # A larger window must not create the v16 empty block below controls.
        w.resize(1320, 970)
        app.processEvents()
        assert w.video_stack.height() >= old_video + 50
        assert w.control_container.height() == old_controls
        # In v12/v13 the native Qt video height prevented the window shrinking.
        w.resize(1320, 660)
        app.processEvents()
        assert w.height() <= 710, (w.height(), w.minimumSizeHint().height())
        assert w.video_stack.height() >= w.video_stack.minimumHeight()
        assert w.control_container.height() == old_controls
        central = w.centralWidget()
        bottom_margin = central.layout().contentsMargins().bottom()
        assert abs((central.height() - bottom_margin) - (w.control_container.y() + old_controls)) <= 3
    finally:
        w.close()
