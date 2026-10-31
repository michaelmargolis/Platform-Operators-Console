# test_v12_controller.py ver 1
"""Video layout and muscle peak lifecycle checks without requiring Qt6."""
import ast
from pathlib import Path
from types import SimpleNamespace

from tools.sim_player.muscle_extrema import MuscleExtrema
from tools.sim_player.loop_range import PlaybackRange

SOURCE = Path(__file__).resolve().parents[1]


def test_video_and_controls_use_fixed_strip_without_obsolete_splitter():
    """v17 replaces the v12 divider, which v16 had disabled but retained."""
    ui = (SOURCE / 'player_ui.py').read_text(encoding='utf-8')
    app = (SOURCE / 'app.py').read_text(encoding='utf-8')
    assert 'layout.addWidget(self.video_stack, 1)' in ui
    assert 'layout.addWidget(self.control_container, 0)' in ui
    assert 'self.video_stack.setMinimumSize(320, 120)' in ui
    assert 'self.video_screen.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)' in ui
    assert 'self.control_container.setFixedHeight(height)' in ui
    assert 'self.video_splitter' not in ui + app
    assert 'saveState()' in app  # QMainWindow docks, not a video splitter


def test_play_from_ride_end_resets_peaks_but_automatic_loop_does_not():
    from test_paused_video_preview import FakeWindow
    ui = FakeWindow(has_video=False)
    ui.muscle_extrema.record((800,) * 6)
    ui.muscle_extrema.record((900,) * 6)
    ui.current_s = ui.recording.duration
    ui._set_playing(True)
    assert ui.peak_reset_count == 1
    assert ui.current_s == 0.0
    ui.muscle_extrema.record((830,) * 6)
    ui.muscle_extrema.record((930,) * 6)
    ui.loop_range.mark(20, 2)
    ui.current_s = ui.loop_range.end
    ui.playing = True
    ui._restart_loop()
    assert ui.peak_reset_count == 1
    assert ui.muscle_extrema.extrema()[0] == (830., 930.)


def test_reanalysis_resets_peaks_without_tweaking_video():
    source = (SOURCE / 'app.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ReplayWindow')
    apply = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == '_apply_reanalysis')
    calls = [n.func.attr for n in ast.walk(apply) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
    assert '_reset_muscle_extrema' in calls
    assert 'seek' not in calls
    assert 'load' not in calls
