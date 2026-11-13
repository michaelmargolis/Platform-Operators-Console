"""Contracts for Task Workspace v0.2.6 overlay and canonical pre-roll UX."""
from pathlib import Path
from types import SimpleNamespace as NS
import numpy as np
import pytest

from tools.sim_player.canonical_sync import MediaBinding

PLAYER = Path(__file__).resolve().parents[1]


def _source(name):
    return (PLAYER / name).read_text(encoding='utf-8')


def test_overlay_uses_frame_mirror_not_qvideowidget_stacking():
    backend = _source('video_backend.py')
    ui = _source('player_ui.py')
    app = _source('app.py')
    assert 'class VideoFrameMirror(QWidget)' in backend
    assert 'self.video_stack.addWidget(self.video_screen)' in ui
    assert 'self.overlay_video_frame = VideoFrameMirror()' in ui
    assert 'self.video_overlay_layout.addWidget(self.overlay_video_frame)' in ui
    assert 'self.video_overlay_layout.addWidget(self.video_screen)' not in ui
    assert 'videoFrameChanged.connect(self.overlay_video_frame.set_frame)' in ui
    assert 'self.video_stack.setCurrentWidget(self.video_screen)' in app
    assert 'self.video_stack.setCurrentWidget(self.video_overlay_widget)' in app


def test_canonical_preroll_wording_distinguishes_available_video():
    workflow = _source('canonical_workflow.py')
    app = _source('app.py')
    assert 'Reference video available - begins at' in workflow
    assert 'Video starts in {remaining:.1f} s.' in workflow
    assert 'Reference video temporarily unavailable - telemetry alignment gap' in workflow
    assert "getattr(self, '_canonical_unavailable_message', None)" in app


def test_first_available_time_respects_video_start_offset():
    media = NS(video_time=lambda phase: float(phase) - 3.4)
    binding = MediaBinding(
        media=media,
        times=np.asarray([0.0, 1.0, 2.0, 3.0, 3.4, 4.0]),
        phase=np.asarray([0.0, 1.0, 2.0, 3.0, 3.4, 4.0]),
        valid=np.ones(6, dtype=bool),
        statuses=('xyz',) * 6,
        source='test',
        summary={},
    )
    assert binding.first_available_time() == pytest.approx(3.4)
