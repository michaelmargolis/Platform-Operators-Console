# test_recording.py ver 1
"""Headless tests for standalone sim_player; no Qt or hardware required."""
from __future__ import annotations

import csv
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from sim_player.recording import (load_recording, read_saved_calibration,
                                  write_saved_calibration)


def make_capture(folder, *, with_events=True, with_manifest=True, video=True):
    folder.mkdir(parents=True, exist_ok=True)
    stem = "Blackbird_repeat01_20261026_123456"
    path = folder / (stem + ".csv")
    columns = ["ride", "elapsed_s", "wall_time", "pos_y"] + [
        "norm_" + x for x in ("surge", "sway", "heave", "roll", "pitch", "yaw")]
    with path.open("w", newline="") as handle:
        wr = csv.DictWriter(handle, fieldnames=columns)
        wr.writeheader()
        for t in (0.0, 0.052, 0.111, 0.167, 0.235):
            wr.writerow(dict(ride="Blackbird", elapsed_s=t,
                             wall_time=f"2026-10-26T12:34:{56+t:06.3f}",
                             pos_y=7.5, norm_surge=.1, norm_sway=-.2,
                             norm_heave=0, norm_roll=.1, norm_pitch=-.1, norm_yaw=0))
    if video:
        (folder / (stem + ".mp4")).write_bytes(b"test placeholder video path only")
    if with_manifest:
        manifest = {"schema": 1, "approx_csv_zero_after_obs_ack_s": 1.21,
                    "video_filename": stem + ".mp4",
                    "events": {
                        "state_event_elapsed_zero": {"monotonic_s": 120.0},
                        "capture_elapsed_zero": {"monotonic_s": 130.0},
                    }}
        (folder / (stem + "_video_sync.json")).write_text(json.dumps(manifest))
    if with_events:
        with (folder / (stem + "_state_events.csv")).open("w", newline="") as handle:
            wr = csv.DictWriter(handle, fieldnames=["wall_time", "elapsed_s", "event", "state", "details"])
            wr.writeheader()
            wr.writerow(dict(wall_time="2026-10-26T12:34:56.010", elapsed_s=10.01,
                             event="view_train_dispatched", state="RUNNING"))
            wr.writerow(dict(wall_time="2026-10-26T12:34:56.130", elapsed_s=10.13,
                             event="view_train_departed", state="RUNNING"))
            wr.writerow(dict(wall_time="2026-10-26T12:34:56.200", elapsed_s=10.2,
                             event="view_train_returned", state="READY"))
    return path


def test_poc_calculation_and_nonuniform_timeline(tmp_path):
    ride = load_recording(make_capture(tmp_path))
    assert ride.ride == "Blackbird"
    assert len(ride.frames) == 5
    assert ride.times == (0, .052, .111, .167, .235)
    assert ride.nearest_index(.070) == 1
    assert ride.nearest_index(.095) == 2
    assert all(len(frame.muscle_lengths_mm) == 6 for frame in ride.frames)
    assert all(ride.min_length_mm <= v <= ride.max_length_mm
               for frame in ride.frames for v in frame.muscle_lengths_mm)
    assert "applied_real" in ride.frames[0].analysis  # later tension solver seam
    assert ride.video_path and ride.video_path.suffix == ".mp4"
    assert ride.video_offset_s == pytest.approx(1.21)
    assert [e.name for e in ride.events] == ["Dispatched", "Departed", "Returned"]
    assert [e.seconds for e in ride.events] == pytest.approx([.01, .13, .2])


def test_calibration_is_separate_and_reloaded(tmp_path):
    csv_path = make_capture(tmp_path)
    before = csv_path.read_bytes()
    saved = write_saved_calibration(csv_path, 1.39)
    assert saved.name.endswith("_sim_player_sync.json")
    assert read_saved_calibration(csv_path) == pytest.approx(1.39)
    ride = load_recording(csv_path)
    assert ride.video_offset_s == pytest.approx(1.39)
    assert "calibrated" in ride.sync_source
    assert csv_path.read_bytes() == before


def test_no_video_or_manifest_still_loads(tmp_path):
    ride = load_recording(make_capture(tmp_path, video=False, with_manifest=False,
                                       with_events=False))
    assert ride.video_path is None
    assert ride.video_offset_s == 0
    assert ride.events == ()
    assert len(ride.frames) == 5


def test_invalid_timing_rejected(tmp_path):
    csv_path = make_capture(tmp_path)
    text = csv_path.read_text().replace("0.111", "0.052", 1)
    csv_path.write_text(text)
    with pytest.raises(ValueError, match="strictly"):
        load_recording(csv_path)


def test_wrong_file_rejected(tmp_path):
    path = tmp_path / "Blackbird_state_events.csv"
    path.write_text("event,elapsed_s\n", encoding="utf-8")
    with pytest.raises(ValueError, match="telemetry CSV"):
        load_recording(path)
