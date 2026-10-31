# test_v16_compact_layout.py ver 1
"""v16 regression rewritten to assert that v17 removed the v16 blank dock."""
from pathlib import Path
BASE = Path(__file__).resolve().parents[1]


def test_compact_right_docks_without_spacer():
    ui = (BASE / 'player_ui.py').read_text(encoding='utf-8')
    app = (BASE / 'app.py').read_text(encoding='utf-8')
    assert 'def _compact_right_docks(self):' in ui
    assert 'self.resizeDocks(rows, minimums + [10000], Qt.Orientation.Vertical)' in ui
    assert 'self.dock_space' not in ui
    assert 'self.splitDockWidget(lengths, self.dock_space' not in ui
    assert 'self.removeDockWidget(legacy_space)' in app
    assert 'QTimer.singleShot(0, self._initialize_layout_v17)' in app


def test_playback_controls_fixed_height_without_splitter():
    ui = (BASE / 'player_ui.py').read_text(encoding='utf-8')
    app = (BASE / 'app.py').read_text(encoding='utf-8')
    assert 'self.control_container.setFixedHeight(height)' in ui
    assert 'QSizePolicy.Policy.Fixed' in ui
    assert 'layout.addWidget(self.control_container, 0)' in ui
    assert 'self.video_splitter' not in ui + app
    assert 'self._fix_playback_controls_height()' in app
