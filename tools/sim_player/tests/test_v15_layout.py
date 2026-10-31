# test_v15_layout.py ver 1
"""Lightweight source-contract checks; GUI rendering is verified on Windows."""
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]


def test_sync_controls_are_only_in_dock():
    ui = (BASE / 'player_ui.py').read_text()
    assert 'self.video_sync_widget.setLayout(video_row)' in ui
    assert 'self._add_dock("video_sync", "Video synchronisation"' in ui
    assert 'video_sync.hide()' in ui
    assert 'control_layout.addLayout(video_row)' not in ui
    assert 'self.panels["video_sync"].hide()' in ui


def test_common_scale_grid_and_bounds():
    ui = (BASE / 'panels.py').read_text()
    assert 'range(first_tick, last_tick + 1, 50)' in ui
    assert 'for limit in (self.lo, self.hi)' in ui
    assert 'left = 46 if not self.signed else 72' in ui
    assert 'right = (113 if width >= 410 else 104)' in ui


def test_video_and_processing_untouched():
    ui = (BASE / 'player_ui.py').read_text()
    assert 'Qt.AspectRatioMode.KeepAspectRatio' in ui
    assert 'self.offset_box.valueChanged.connect(self._offset_changed)' in ui
