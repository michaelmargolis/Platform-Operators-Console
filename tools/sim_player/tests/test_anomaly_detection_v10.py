# test_anomaly_detection_v10.py ver 1
"""No Qt required: synthetic POC results and real replay anomaly integration."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import sys
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from sim_player.anomaly_detection import AnomalyThresholds, detect_anomalies
from sim_player.loop_range import PlaybackRange
from sim_player.recording import PlaybackFrame, load_recording
from sim_player.angular_cues import EULER, SINE, GRAVITY


def frame(t, *, request=800, final=800, workspace=800, floor=90,
          limited=False, reason="", scale=1.0, below=False, requested_floor=50):
    info = dict(requested=(request, 800, 800, 800, 800, 800),
                workspace_lengths=(workspace, 800, 800, 800, 800, 800),
                requested_floor=requested_floor, below_floor=below,
                workspace_limited=limited, reason=reason, scale=scale)
    return PlaybackFrame(
        seconds=t, input_axes=(0,)*6, source_axes=(0,)*6, processed_axes=(0,)*6,
        muscle_lengths_mm=(final, 800, 800, 800, 800, 800),
        workspace_limited=limited, modeled_clearance_mm=floor, analysis=info,
    )


def recording(*frames):
    return SimpleNamespace(frames=tuple(frames), duration=frames[-1].seconds,
                           min_length_mm=750.0, max_length_mm=1000.0)


def kinds(events):
    return {ev.category for ev in events}


def test_request_outside_range_is_not_final_clipping():
    ride = recording(frame(0, request=800),
                     frame(.05, request=740, workspace=750, final=750,
                           limited=True, reason="LENGTH", scale=.8),
                     frame(.10, request=800))
    events = detect_anomalies(ride)
    assert "requested_range" in kinds(events)
    assert "workspace_limited" in kinds(events)
    assert "final_clipped" not in kinds(events)
    raw = next(e for e in events if e.category == "requested_range")
    assert raw.magnitude == pytest.approx(10)
    assert raw.muscles == "M1"
    assert raw.seconds == pytest.approx(.05)
    assert "before limiting" in raw.details


def test_post_workspace_clipping_is_separate_event():
    events = detect_anomalies(recording(frame(0, request=740, workspace=745, final=750)))
    assert "final_clipped" in kinds(events)
    clamp = next(e for e in events if e.category == "final_clipped")
    assert clamp.magnitude == pytest.approx(5)
    assert clamp.muscles == "M1"


def test_step_uses_final_permitted_lengths_and_threshold():
    ride = recording(frame(0, request=740, final=800),
                     frame(.05, request=950, final=800),  # raw request varies; output does not
                     frame(.10, request=900, final=889),
                     frame(.15, request=900, final=890))
    events = detect_anomalies(ride)
    steps = [e for e in events if e.category == "muscle_step"]
    assert len(steps) == 1
    assert steps[0].magnitude == pytest.approx(89)
    assert steps[0].seconds == pytest.approx(.1)
    assert "50 mm" not in steps[0].details
    assert not [e for e in detect_anomalies(ride, AnomalyThresholds(100, 30))
                if e.category == "muscle_step"]


def test_contiguous_anomalies_grouped_with_peak_and_time():
    ride = recording(frame(0), frame(.05, final=860), frame(.10, final=925),
                     frame(.15, final=926), frame(.20, final=990))
    steps = [e for e in detect_anomalies(ride) if e.category == "muscle_step"]
    assert len(steps) == 2
    assert steps[0].frame_count == 2
    assert steps[0].start_s == pytest.approx(.05)
    assert steps[0].end_s == pytest.approx(.10)
    assert steps[0].seconds == pytest.approx(.10)
    assert steps[1].frame_count == 1


def test_floor_categories_and_absent_floor_data():
    ride = recording(frame(0, floor=None),
                     frame(.05, floor=27, below=True, requested_floor=12),
                     frame(.10, floor=25, below=True, requested_floor=10),
                     frame(.15, floor=31))
    events = detect_anomalies(ride)
    low = [ev for ev in events if ev.category == "low_clearance"]
    assert len(low) == 1 and low[0].frame_count == 2
    assert low[0].seconds == pytest.approx(.10)
    assert "25.0 mm" in low[0].details
    assert len([ev for ev in events if ev.category == "requested_floor"]) == 1
    assert not [ev for ev in detect_anomalies(ride, AnomalyThresholds(50, 20))
                if ev.category == "low_clearance"]


def test_validation_and_time_gap_split():
    with pytest.raises(ValueError):
        AnomalyThresholds(0, 30)
    with pytest.raises(ValueError):
        AnomalyThresholds(50, -1)
    ride = recording(frame(0, floor=10), frame(.5, floor=9))
    assert len([ev for ev in detect_anomalies(ride) if ev.category == "low_clearance"]) == 2


def test_examine_interval_clips_to_capture_boundaries():
    region = PlaybackRange(duration=10)
    region.select_event(.3, .5)
    assert (region.start, region.end, region.mode) == pytest.approx((0, 1.5, "loop"), rel=1e-7)
    region.select_event(9.8, 9.9)
    assert region.start == pytest.approx(8.8)
    assert region.end == pytest.approx(10)
    assert region.mode == "loop"
    region.select_event(5, 5)
    assert region.start == pytest.approx(4)
    assert region.end == pytest.approx(6)


def test_real_blackbird_cue_switch_changes_step_anomalies():
    candidates = [
        Path('/mnt/data/Blackbird_repeat01_20261026_110843.csv'),
        ROOT.parent / 'captures/nl2_ride_study/Blackbird_repeat01_20261026_110843.csv',
    ]
    source = next((item for item in candidates if item.is_file()), None)
    if source is None:
        pytest.skip("Reference Blackbird capture absent; synthetic cue tests still run")
    euler = load_recording(source, cue_mode=EULER)
    sine = load_recording(source, cue_mode=SINE)
    grav = load_recording(source, cue_mode=GRAVITY)
    assert euler.times == sine.times == grav.times
    assert len([e for e in detect_anomalies(euler) if e.category == 'muscle_step']) > 0
    assert not [e for e in detect_anomalies(sine) if e.category == 'muscle_step']
    assert not [e for e in detect_anomalies(grav) if e.category == 'muscle_step']
    assert euler.frames[0].source_axes == sine.frames[0].source_axes
