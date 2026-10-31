# test_paused_video_preview.py ver 1
"""Headless Qt6 seek-policy tests (same filename as v3, for clean overwrite).

v3 used play()/pause() pulses to force preview after a seek. v4 must *never*
do that: verified Qt6/FFmpeg seeks render while the decoder stays paused.

The test extracts the real controller methods into a fake window so it can
run without GUI/codec dependencies in build environments.
"""
from __future__ import annotations

import ast
from pathlib import Path
import time
import types
from tools.sim_player.loop_range import PlaybackRange
from tools.sim_player.muscle_extrema import MuscleExtrema

APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
NAMES = {"_sync_video", "seek_seconds", "_set_playing", "_scrub_started", "_scrub_finished", "_tick",
         "_begin_loop_hold", "_restart_loop", "_cancel_loop_hold"}


def replay_methods():
    tree = ast.parse(APP_PATH.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ReplayWindow")
    nodes = [node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in NAMES]
    assert {node.name for node in nodes} == NAMES
    scope = {"time": time}
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), str(APP_PATH), "exec"), scope)
    return {name: scope[name] for name in NAMES}


class FakeTimer:
    def __init__(self):
        self.active = False
        self.starts = 0

    def isActive(self):
        return self.active

    def start(self, ms=None):
        self.active = True
        self.starts += 1
        self.last_ms = ms

    def stop(self):
        self.active = False


class FakeBackend:
    def __init__(self):
        self.pos = 0
        self.seeks = []
        self.play_calls = 0
        self.pause_calls = 0
        self.rates = []

    def position(self):
        return self.pos

    def seek(self, value):
        self.pos = value
        self.seeks.append(value)

    def set_speed(self, value):
        self.rates.append(value)

    def play(self):
        self.play_calls += 1

    def pause(self):
        self.pause_calls += 1


class FakeGuard:
    def __init__(self):
        self.seeks = []
        self.resumes = []

    def user_seek(self, now):
        self.seeks.append(now)

    def should_correct_drift(self, now, difference):
        return False

    def should_seek_on_resume(self, drift, now):
        return abs(drift) >= 1000 and (not self.seeks or now - self.seeks[-1] > .75)

    def resumed(self, now):
        self.resumes.append(now)


class FakeSlider:
    def __init__(self, down=False):
        self.down = down

    def isSliderDown(self):
        return self.down


class FakeButton:
    def __init__(self):
        self.text = ""

    def setText(self, text):
        self.text = text


class FakeWindow:
    def __init__(self, *, dragging=False, playing=False, has_video=True):
        self.has_video = has_video
        self.playing = playing
        self.current_s = 0.0
        self.video_offset_s = 0.0
        self.speed_ratio = 1.0
        self.recording = types.SimpleNamespace(duration=100.0, times=(0.0, 100.0))
        self.muscle_extrema = MuscleExtrema()
        self.peak_reset_count = 0
        self.loop_range = PlaybackRange()
        self.loop_range.reset(100.0)
        self._loop_holding = False
        self.loop_hold_timer = FakeTimer()
        self.loop_feedback_label = types.SimpleNamespace(setText=lambda value: None,
                                                         setVisible=lambda value: None)
        self.video_backend = FakeBackend()
        self.video_seek_guard = FakeGuard()
        self.slider = FakeSlider(dragging)
        self.scrub_seek_timer = FakeTimer()
        self.play_button = FakeButton()
        self.was_playing_before_scrub = False
        self.display_calls = 0
        self.anchor_ride_s = 0.0
        self.anchor_time = 0.0
        for name, func in replay_methods().items():
            setattr(self, name, types.MethodType(func, self))

    def _reset_muscle_extrema(self):
        self.peak_reset_count += 1
        self.muscle_extrema.clear()

    def _send_twin(self, *, force=False):
        pass  # No UDP in video policy unit tests

    def _update_twin_indicator(self):
        pass  # No widgets in headless video-policy tests

    def _display_current(self):
        self.display_calls += 1

    def statusBar(self):
        return types.SimpleNamespace(showMessage=lambda *args: None)

    def _video_target_ms(self):
        return max(0, round((self.current_s + self.video_offset_s) * 1000))


def test_paused_seek_updates_video_without_play_pulse():
    ui = FakeWindow()
    ui.seek_seconds(12.25)
    assert ui.current_s == 12.25
    assert ui.video_backend.seeks == [12250]
    assert ui.video_backend.play_calls == 0
    assert ui.video_backend.pause_calls == 0
    assert not ui.playing


def test_small_step_seeks_even_within_single_video_frame():
    ui = FakeWindow()
    ui.video_backend.pos = 15000
    ui.seek_seconds(15.020)
    assert ui.video_backend.seeks == [15020]
    assert ui.video_backend.play_calls == 0


def test_drag_coalesces_to_latest_position_and_releases_exactly():
    ui = FakeWindow(dragging=True)
    for t in (10, 11, 12):
        ui.seek_seconds(t)
    assert ui.display_calls == 3
    assert ui.scrub_seek_timer.starts == 1
    assert ui.video_backend.seeks == []
    ui.scrub_seek_timer.stop()  # The scheduled timer fires; latest drag position wins.
    ui._sync_video(force=True)
    assert ui.video_backend.seeks == [12000]
    ui.seek_seconds(13)
    assert ui.scrub_seek_timer.starts == 2
    ui.slider.down = False
    ui._scrub_finished()
    assert ui.video_backend.seeks[-1] == 13000
    assert ui.video_backend.play_calls == 0


def test_play_pause_resume_does_not_force_seek_or_preview():
    ui = FakeWindow()
    ui.current_s = 10
    ui.video_backend.pos = 10100
    ui._set_playing(True)
    assert ui.video_backend.play_calls == 1
    assert ui.video_backend.seeks == []
    ui._set_playing(False)
    assert ui.video_backend.pause_calls == 1
    ui._set_playing(True)
    assert ui.video_backend.play_calls == 2
    assert ui.video_backend.seeks == []


def test_large_drift_can_be_corrected_once_on_resume():
    ui = FakeWindow()
    ui.current_s = 10
    ui.video_backend.pos = 0
    ui._set_playing(True)
    assert ui.video_backend.seeks == [10000]
    assert ui.video_backend.play_calls == 1


def test_csv_only_playback_never_calls_video_backend():
    ui = FakeWindow(has_video=False)
    ui.seek_seconds(12)
    ui._set_playing(True)
    assert ui.current_s == 12
    assert ui.video_backend.play_calls == 0
    assert ui.video_backend.seeks == []


def test_loop_boundary_holds_b_before_one_restart_seek():
    ui = FakeWindow()
    ui.loop_range.mark(10, 2)  # A=9, B=11, default delay 1 second
    ui.current_s = 10.5
    ui.playing = True
    ui.anchor_ride_s = 10.5
    ui.anchor_time = time.monotonic() - .55
    ui._tick()
    assert ui.current_s == 11
    assert ui.playing and ui._loop_holding
    assert ui.loop_hold_timer.last_ms == 1000
    assert ui.video_backend.pause_calls == 1
    assert ui.video_backend.seeks == [11000]
    assert ui.video_backend.play_calls == 0
    ui._tick()  # Waiting must not advance telemetry or trigger seeks.
    assert ui.current_s == 11
    assert ui.video_backend.seeks == [11000]
    ui._restart_loop()
    assert ui.current_s == 9
    assert not ui._loop_holding
    assert ui.video_backend.seeks == [11000, 9000]
    assert ui.video_backend.play_calls == 1
    assert ui.playing


def test_start_playback_outside_range_jumps_to_a():
    ui = FakeWindow()
    ui.loop_range.mark(10, 2)
    ui.current_s = 2
    ui._set_playing(True)
    assert ui.current_s == 9
    assert ui.playing
    assert ui.video_backend.pause_calls == 0


def test_play_range_stops_at_b_without_loop_timer():
    ui = FakeWindow()
    ui.loop_range.mark(10, 2)
    ui.loop_range.set_mode("range")
    ui.current_s = 10.5
    ui.playing = True
    ui.anchor_ride_s = 10.5
    ui.anchor_time = time.monotonic() - .55
    ui._tick()
    assert ui.current_s == 11
    assert not ui.playing
    assert ui.video_backend.seeks == [11000]
    assert ui.video_backend.pause_calls == 1
    assert ui.loop_hold_timer.starts == 0
    ui._set_playing(True)
    assert ui.current_s == 9


def test_full_ride_ignores_markers():
    ui = FakeWindow()
    ui.loop_range.mark(10, 2)
    ui.loop_range.set_mode("full")
    ui.current_s = 10.5
    ui.playing = True
    ui.anchor_ride_s = 10.5
    ui.anchor_time = time.monotonic() - .55
    ui._tick()
    assert ui.current_s > 11
    assert ui.loop_hold_timer.starts == 0


def test_pause_during_hold_cancels_restart():
    ui = FakeWindow()
    ui.loop_range.mark(10, 2)
    ui.playing = True
    ui._begin_loop_hold()
    assert ui._loop_holding
    ui._set_playing(False)
    assert not ui._loop_holding
    assert not ui.loop_hold_timer.active
    assert not ui.playing
    calls = ui.video_backend.play_calls
    ui._restart_loop()  # A stale event may never resume playback.
    assert not ui.playing
    assert ui.video_backend.play_calls == calls


def test_user_seek_during_hold_cancels_timer_and_seeks_once():
    ui = FakeWindow()
    ui.loop_range.mark(10, 2)
    ui.playing = True
    ui._begin_loop_hold()
    ui.seek_seconds(9.4)
    assert not ui._loop_holding
    assert not ui.loop_hold_timer.active
    assert ui.current_s == 9.4
    assert ui.video_backend.seeks[-1] == 9400


def test_zero_delay_does_not_pulse_pause():
    ui = FakeWindow()
    ui.loop_range.mark(10, 2)
    ui.loop_range.set_delay(0)
    ui.playing = True
    ui._begin_loop_hold()
    assert ui.current_s == 9
    assert ui.video_backend.pause_calls == 0
    assert ui.video_backend.seeks == [9000]
    assert ui.video_backend.play_calls == 1
