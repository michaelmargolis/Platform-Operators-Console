# test_v9_playback_modes.py ver 1
"""Headless tests for v9 range-controls and timer cancellation.

The controller methods are extracted so these regressions run without Qt6;
actual dock/widget interaction is additionally covered by optional GUI tests.
"""
import ast
from pathlib import Path
import time
import types

from tools.sim_player.loop_range import PlaybackRange

APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
METHODS = {"_range_marker_edited", "_mark_range", "_range_mode_changed",
           "_loop_delay_changed", "_reset_range", "_cancel_loop_hold", "_restart_loop"}


def methods():
    module = ast.parse(APP_PATH.read_text(encoding="utf-8"))
    window = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == "ReplayWindow")
    nodes = [n for n in window.body if isinstance(n, ast.FunctionDef) and n.name in METHODS]
    assert {n.name for n in nodes} == METHODS
    scope = {"time": time}
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), str(APP_PATH), 'exec'), scope)
    return {name: scope[name] for name in METHODS}


class FakeTimer:
    def __init__(self):
        self.running = False
        self.starts = []

    def start(self, millis=None):
        self.running = True
        self.starts.append(millis)

    def stop(self):
        self.running = False


class FakeVideo:
    def __init__(self):
        self.play_calls = 0

    def play(self):
        self.play_calls += 1


class FakeCombo:
    def __init__(self, value):
        self.value = value

    def currentData(self):
        return self.value


class FakeSpin:
    def value(self):
        return 2.0


class FakeWindow:
    def __init__(self):
        self.recording = types.SimpleNamespace(duration=20)
        self.loop_range = PlaybackRange()
        self.loop_range.reset(20)
        self.loop_range.mark(10, 2)
        self.current_s = 10
        self._loop_holding = False
        self.loop_hold_timer = FakeTimer()
        self.loop_feedback_label = types.SimpleNamespace(setVisible=lambda value: None)
        self.range_mode_box = FakeCombo("full")
        self.loop_delay_box = FakeCombo(1.0)
        self.range_duration_box = FakeSpin()
        self.playing = False
        self.has_video = True
        self.video_backend = FakeVideo()
        self.anchor_time = 0
        self.anchor_ride_s = 0
        self.refreshes = 0
        self.seeks = []
        for name, function in methods().items():
            setattr(self, name, types.MethodType(function, self))

    def _refresh_range_controls(self):
        self.refreshes += 1

    def seek_seconds(self, when, **kwargs):
        self.current_s = when
        self.seeks.append(when)

    def _set_playing(self, active):
        self.playing = active

    def _update_twin_indicator(self):
        pass

    def statusBar(self):
        return types.SimpleNamespace(showMessage=lambda *args: None)


def test_marker_drag_never_seeks_or_transmits():
    ui = FakeWindow()
    ui._range_marker_edited("start", 9.5)
    ui._range_marker_edited("end", 11.5)
    assert (ui.loop_range.start, ui.loop_range.end) == (9.5, 11.5)
    assert not ui.seeks


def test_mark_selects_loop_mode_and_duration():
    ui = FakeWindow()
    ui.loop_range.set_mode("full")
    ui._mark_range()
    assert (ui.loop_range.start, ui.loop_range.end, ui.loop_range.mode) == (9, 11, "loop")


def test_changing_mode_cancels_hold_and_no_unwanted_restart():
    ui = FakeWindow()
    ui.playing = True
    ui.current_s = 11
    ui._loop_holding = True
    ui.loop_hold_timer.start(1000)
    ui.range_mode_box.value = "full"
    ui._range_mode_changed()
    assert not ui._loop_holding
    assert not ui.loop_hold_timer.running
    assert ui.loop_range.mode == "full"
    assert ui.video_backend.play_calls == 1
    assert not ui.seeks
    ui._restart_loop()  # Even if an old timeout callback ran, it must not jump.
    assert not ui.seeks


def test_range_mode_selection_at_b_stops_without_looping():
    ui = FakeWindow()
    ui.playing = True
    ui.current_s = 11
    ui.range_mode_box.value = "range"
    ui._range_mode_changed()
    assert ui.loop_range.mode == "range"
    assert ui.seeks == [11]
    assert not ui.playing


def test_loop_delay_change_updates_pending_timer():
    ui = FakeWindow()
    ui._loop_holding = True
    ui.loop_delay_box.value = 2.0
    ui._loop_delay_changed()
    assert ui.loop_range.loop_delay_s == 2.0
    assert ui.loop_hold_timer.starts == [2000]


def test_reset_cancels_hold_and_restores_full_mode():
    ui = FakeWindow()
    ui._loop_holding = True
    ui.loop_hold_timer.start(1000)
    ui._reset_range()
    assert (ui.loop_range.start, ui.loop_range.end, ui.loop_range.mode) == (0, 20, "full")
    assert not ui.loop_hold_timer.running


def test_compact_ui_and_marker_hover_contract():
    ui = (APP_PATH.parent / "player_ui.py").read_text(encoding='utf-8')
    slider = (APP_PATH.parent / "panels.py").read_text(encoding='utf-8')
    assert 'self.range_mode_box = QComboBox()' in ui
    assert 'controls.addSpacing(22)' in ui
    assert 'self.loop_delay_box = QComboBox()' in ui
    assert 'setToolTip(f"{label}: {clock_label(timestamp)}' in slider
