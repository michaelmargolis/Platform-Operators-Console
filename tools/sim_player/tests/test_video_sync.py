# test_video_sync.py ver 1
"""Headless regression tests for sim_player video seek and file-dialog policy."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from sim_player.video_sync import VideoSeekGuard, is_ride_telemetry_csv


def test_open_dialog_filename_policy():
    assert is_ride_telemetry_csv("Blackbird_repeat01.csv")
    assert is_ride_telemetry_csv("Mdx_Logo_Coaster_repeat01.CSV")
    assert not is_ride_telemetry_csv("Blackbird_repeat01_state_events.csv")
    assert not is_ride_telemetry_csv("Blackbird_repeat01_STATE_EVENTS.CSV")
    assert not is_ride_telemetry_csv("Blackbird_repeat01_video_sync.json")
    assert not is_ride_telemetry_csv("Blackbird_repeat01.mp4")


def test_resume_does_not_seek_for_normal_decoder_jitter():
    sync = VideoSeekGuard()
    assert not sync.should_seek_on_resume(0, 10)
    assert not sync.should_seek_on_resume(175, 10)
    assert not sync.should_seek_on_resume(-400, 10)
    assert sync.should_seek_on_resume(1200, 10)
    assert sync.should_seek_on_resume(-1100, 10)
    sync.user_seek(10.0)
    assert not sync.should_seek_on_resume(2000, 10.1)
    assert sync.should_seek_on_resume(2000, 11.0)


def test_transient_drift_during_resume_does_not_trigger_seek():
    sync = VideoSeekGuard()
    sync.resumed(10.0)
    assert not sync.should_correct_drift(10.2, 750)
    assert not sync.should_correct_drift(11.0, 750)
    assert not sync.should_correct_drift(11.3, 500)  # starts sustained drift timer
    assert not sync.should_correct_drift(11.5, 50)   # clears it
    assert not sync.should_correct_drift(11.8, 500)
    assert sync.should_correct_drift(12.7, 500)
    assert not sync.should_correct_drift(12.8, 500)  # cooldown


def test_user_seek_suppresses_automatic_correction():
    sync = VideoSeekGuard()
    sync.user_seek(20)
    assert not sync.should_correct_drift(20.1, 999)
    assert not sync.should_correct_drift(22.3, 999)
    assert not sync.should_correct_drift(22.6, 999)
    assert sync.should_correct_drift(23.5, 999)
    sync.reset()
    assert not sync.should_correct_drift(0.0, 400)
    assert sync.should_correct_drift(.8, 400)


def test_small_drift_always_ignored():
    sync = VideoSeekGuard()
    for t in [0, 1, 2, 3, 5, 20]:
        assert not sync.should_correct_drift(t, -200)
        assert not sync.should_correct_drift(t, 275)
