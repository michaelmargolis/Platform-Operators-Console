# test_nl2_obs_capture_v173.py  ver 3
"""OBS integration tests with no Windows GUI, OBS instance or NL2 required."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import capture_nl2_rides as capture
from tools.nl2_obs_recorder import OBSRideRecorder, _matches_ride_window


class MockOBS:
    def __init__(self, directory, kind="window_capture"):
        self.directory = Path(directory)
        self.kind = kind
        self.active = False
        self.calls = []
        self.source = {"window": "Old:Old:old.exe"}
        self.run = 0
        self.fail_start = False
        self.fail_stop = False

    def get_record_status(self):
        return SimpleNamespace(output_active=self.active)

    def get_input_settings(self, name):
        if name != "NL2 Game Capture":
            raise RuntimeError("No source")
        return SimpleNamespace(input_kind=self.kind, input_settings=dict(self.source))

    def set_input_settings(self, name, settings, overlay):
        assert overlay is True
        self.source.update(settings)
        self.calls.append(("window", name, dict(settings)))

    def get_record_directory(self):
        return SimpleNamespace(record_directory=str(self.directory))

    def set_record_directory(self, directory):
        assert not self.active
        self.directory = Path(directory)
        self.calls.append(("directory", str(directory)))

    def start_record(self):
        self.calls.append(("start",))
        if self.fail_start:
            raise RuntimeError("start failed")
        assert not self.active
        self.active = True

    def stop_record(self):
        self.calls.append(("stop",))
        if self.fail_stop:
            raise RuntimeError("stop failed")
        assert self.active
        self.active = False
        self.run += 1
        video = self.directory / f"OBS_{self.run}.mp4"
        video.parent.mkdir(parents=True, exist_ok=True)
        video.write_bytes(b"FAKEVIDEO")
        return SimpleNamespace(output_path=str(video))


def recorder(tmp_path, kind="window_capture"):
    original = tmp_path / "Videos"
    original.mkdir()
    obs = MockOBS(original, kind=kind)
    out = tmp_path / "captures"
    rec = OBSRideRecorder(out, client=obs, source="NL2 Game Capture", tail_s=0,
                          window_finder=lambda *a: ("Blackbird.nl2park - NoLimits 2",
                                                    "NL3D_main_guid", "NoLimits2.exe"))
    return rec, obs, out, original


@pytest.mark.parametrize("kind", ["game_capture", "window_capture"])
def test_window_source_configuration_and_named_video(tmp_path, kind):
    rec, obs, output, original = recorder(tmp_path, kind)
    rec.begin("Blackbird", "Blackbird_repeat01_20261026_110000")
    assert obs.active
    assert obs.source["window"] == "Blackbird.nl2park - NoLimits 2:NL3D_main_guid:NoLimits2.exe"
    if kind == "game_capture":
        assert obs.source["capture_mode"] == "window"
    else:
        assert "capture_mode" not in obs.source
    ack_clock = rec._active["events"]["obs_start_ack"]["monotonic_s"]
    rec.mark("state_event_elapsed_zero", ack_clock - 0.5)
    rec.mark("capture_elapsed_zero", ack_clock + 0.2)
    video = Path(rec.finish(status="completed", frames=200))
    assert video.name == "Blackbird_repeat01_20261026_110000.mp4"
    assert video.read_bytes() == b"FAKEVIDEO"
    assert not (output / "OBS_1.mp4").exists()
    manifest = json.loads((output / "Blackbird_repeat01_20261026_110000_video_sync.json").read_text())
    assert manifest["status"] == "completed"
    assert manifest["frames"] == 200
    assert manifest["sync_calibrated"] is False
    assert manifest["video_to_telemetry_offset_ms"] is None
    assert manifest["approx_csv_zero_after_obs_ack_s"] == pytest.approx(0.2)
    assert "state_event_elapsed_zero" in manifest["events"]
    rec.close()
    assert obs.directory == original


def test_preexisting_recording_is_not_stopped(tmp_path):
    rec, obs, *_ = recorder(tmp_path)
    obs.active = True
    with pytest.raises(RuntimeError, match="already recording"):
        rec.begin("Blackbird", "test")
    assert ("stop",) not in obs.calls


def test_source_type_rejected_before_recording(tmp_path):
    rec, obs, *_ = recorder(tmp_path, kind="image_source")
    with pytest.raises(RuntimeError, match="must be Game Capture or Window Capture"):
        rec.begin("Blackbird", "test")
    assert ("start",) not in obs.calls


def test_missing_window_rejected_before_recording(tmp_path):
    rec, obs, *_ = recorder(tmp_path)
    rec.window_finder = lambda *a: (_ for _ in ()).throw(RuntimeError("No window"))
    with pytest.raises(RuntimeError, match="No window"):
        rec.begin("Blackbird", "test")
    assert not obs.active


def test_second_ride_new_video_and_correct_file(tmp_path):
    rec, obs, out, *_ = recorder(tmp_path)
    for stem in ("Blackbird_repeat01", "Blackbird_repeat02"):
        rec.begin("Blackbird", stem)
        rec.finish(status="completed")
    assert (out / "Blackbird_repeat01.mp4").is_file()
    assert (out / "Blackbird_repeat02.mp4").is_file()
    rec.close()
    assert [c[0] for c in obs.calls].count("stop") == 2


def test_existing_video_is_not_overwritten(tmp_path):
    rec, obs, out, *_ = recorder(tmp_path)
    collision = out / "Blackbird_repeat01.mp4"
    collision.write_bytes(b"PRESERVE")
    rec.begin("Blackbird", "Blackbird_repeat01")
    with pytest.raises(RuntimeError, match="overwrite existing ride video"):
        rec.finish(status="completed")
    assert collision.read_bytes() == b"PRESERVE"
    manifest = json.loads((out / "Blackbird_repeat01_video_sync.json").read_text())
    assert "recording_error" in manifest
    rec.close()


def test_stop_failure_creates_diagnostic_manifest(tmp_path):
    rec, obs, out, *_ = recorder(tmp_path)
    rec.begin("Blackbird", "Blackbird_repeat01")
    obs.fail_stop = True
    with pytest.raises(RuntimeError, match="finalization failed"):
        rec.finish(status="incomplete", error="NL2 timeout")
    manifest = json.loads((out / "Blackbird_repeat01_video_sync.json").read_text())
    assert manifest["error"] == "NL2 timeout"
    assert "stop failed" in manifest["recording_error"]
    # Fake OBS remains active after the injected stop failure; clean it here.
    obs.fail_stop = False
    obs.stop_record()
    rec.close()


def test_begin_error_does_not_run_train(monkeypatch):
    order = []
    sim = SimpleNamespace(_selected=0, prepare_for_activation=lambda: order.append("prepare"),
                          run=lambda: order.append("dispatch"))
    monkeypatch.setattr(capture, "select_ride_for_capture", lambda *a: None)
    monkeypatch.setattr(capture, "await_condition", lambda *a: None)
    def failed_video():
        order.append("obs")
        raise RuntimeError("OBS failed")
    with pytest.raises(RuntimeError, match="OBS failed"):
        capture.start_ride(sim, 0, 10, 0.05, before_dispatch=failed_video)
    assert order == ["prepare", "obs"]


def test_start_ride_dispatch_after_video_is_armed(monkeypatch):
    order = []
    sim = SimpleNamespace(_selected=0, prepare_for_activation=lambda: order.append("prepare"),
                          run=lambda: order.append("dispatch"))
    monkeypatch.setattr(capture, "select_ride_for_capture", lambda *a: None)
    monkeypatch.setattr(capture, "await_condition", lambda *a: None)
    capture.start_ride(sim, 0, 10, 0.05, before_dispatch=lambda: order.append("obs"))
    assert order == ["prepare", "obs", "dispatch"]


class FakeRide:
    def __init__(self):
        self.state = capture.ExperienceState.READY
        self._view_train_departed = True
        self._capture_state_monitor = None

    def _station_observation(self):
        return SimpleNamespace(identity=None, status=None)

    def is_ready_to_start(self):
        return True


def test_capture_one_video_and_csv_timeline(monkeypatch, tmp_path):
    rec, obs, out, *_ = recorder(tmp_path)
    args = SimpleNamespace(host="localhost", hz=20., prepare_timeout=1.,
                           max_duration=2., output_dir=out, state_monitor=True,
                           _obs_recorder=rec)
    sim = FakeRide()
    def fake_start(sim, *a, before_dispatch=None):
        assert before_dispatch is not None
        before_dispatch()
        assert obs.active
    def fake_pump(sim, last_frame=None):
        # Ready after departure; capture utility should terminate this run.
        tm = SimpleNamespace(frame=1, received_at=123.0)
        sample = SimpleNamespace(telemetry_valid=True, transform=(0,)*6)
        return sample, tm
    monkeypatch.setattr(capture, "start_ride", fake_start)
    monkeypatch.setattr(capture, "pump", fake_pump)
    path = capture.capture_one(sim, 0, 1, args, (("Blackbird", "file"),))
    assert path.name.endswith("_state_events.csv")
    video = out / (path.stem.replace("_state_events", "") + ".mp4")
    assert video.exists()
    metadata = json.loads((out / (video.stem + "_video_sync.json")).read_text())
    assert metadata["status"] == "completed"
    assert set(("dispatch_request", "dispatch_confirmed", "capture_elapsed_zero",
                "state_event_elapsed_zero", "first_telemetry_received")) <= set(metadata["events"])
    assert sim._capture_state_monitor is None
    rec.close()


def test_capture_one_exception_stops_video(monkeypatch, tmp_path):
    rec, obs, out, *_ = recorder(tmp_path)
    args = SimpleNamespace(host="localhost", hz=20., prepare_timeout=1.,
                           max_duration=2., output_dir=out, state_monitor=True,
                           _obs_recorder=rec)
    sim = FakeRide()
    monkeypatch.setattr(capture, "start_ride",
                        lambda sim, *a, before_dispatch=None: before_dispatch())
    monkeypatch.setattr(capture, "pump", lambda *a: (_ for _ in ()).throw(TimeoutError("NL2 failed")))
    with pytest.raises(TimeoutError, match="NL2 failed"):
        capture.capture_one(sim, 0, 1, args, (("Blackbird", "file"),))
    assert not obs.active
    manifest_file = next(out.glob("*_video_sync.json"))
    manifest = json.loads(manifest_file.read_text())
    assert manifest["status"] == "incomplete"
    assert "NL2 failed" in manifest["error"]
    assert sim._capture_state_monitor is None
    rec.close()


def test_temporary_windows_video_lock_is_retried(tmp_path, monkeypatch):
    rec, obs, out, *_ = recorder(tmp_path)
    rec.begin("Blackbird", "Blackbird_repeat01")
    original_rename = Path.rename
    calls = [0]
    def locked_twice(path, target):
        calls[0] += 1
        if calls[0] <= 2:
            raise PermissionError(32, "file in use")
        return original_rename(path, target)
    monkeypatch.setattr(Path, "rename", locked_twice)
    monkeypatch.setattr("tools.nl2_obs_recorder.time.sleep", lambda _: None)
    video = Path(rec.finish(status="completed"))
    assert calls[0] == 3
    assert video.name == "Blackbird_repeat01.mp4"
    assert video.is_file()
    rec.close()


def test_persistent_windows_lock_preserves_original_video(tmp_path, monkeypatch):
    rec, obs, out, *_ = recorder(tmp_path)
    rec.begin("Blackbird", "Blackbird_repeat01")
    monkeypatch.setattr(Path, "rename", lambda *a: (_ for _ in ()).throw(PermissionError(32, "in use")))
    import tools.nl2_obs_recorder as mod
    start = mod.time.monotonic()
    ticks = [start]
    def clock():
        ticks[0] += 1.0
        return ticks[0]
    monkeypatch.setattr(mod.time, "monotonic", clock)
    monkeypatch.setattr(mod.time, "sleep", lambda _: None)
    video = Path(rec.finish(status="completed"))
    assert video.name == "OBS_1.mp4"
    assert video.is_file()
    manifest = json.loads((out / "Blackbird_repeat01_video_sync.json").read_text())
    assert manifest["status"] == "completed"
    assert manifest["video_filename"] == "OBS_1.mp4"
    assert "video_rename_warning" in manifest
    assert "recording_error" not in manifest
    rec.close()


def test_obs_busy_directory_restore_retries(tmp_path, monkeypatch):
    rec, obs, out, original = recorder(tmp_path)
    rec.begin("Blackbird", "Blackbird_repeat01")
    rec.finish(status="completed")
    original_set = obs.set_record_directory
    attempts = [0]
    def busy_restore(directory):
        if Path(directory) == original:
            attempts[0] += 1
            if attempts[0] < 3:
                raise RuntimeError("OBS request code 500")
        return original_set(directory)
    obs.set_record_directory = busy_restore
    monkeypatch.setattr("tools.nl2_obs_recorder.time.sleep", lambda _: None)
    rec.close()
    assert attempts[0] == 3
    assert obs.directory == original


@pytest.mark.parametrize("ride, title", [
    ("Mdx Logo Coaster", "MdxLogoCoaster.nl2park - NoLimits 2"),
    ("Blackbird", "Blackbird.nl2park - NoLimits 2"),
    ("Forest Mountain Park", "ForestMountainPark.nl2park - NoLimits 2"),
])
def test_obs_nl2_window_name_normalization(ride, title):
    assert _matches_ride_window(ride, title, "NL3D_MAIN_{some-guid}", "nolimits2app.exe")


def test_obs_window_name_match_rejects_other_park_and_non_render_windows():
    title = "MdxLogoCoaster.nl2park - NoLimits 2"
    assert not _matches_ride_window("Blackbird", title, "NL3D_MAIN_{guid}", "nolimits2app.exe")
    assert not _matches_ride_window("Mdx Logo Coaster", title, "NL3D_NATIVECONSOLE_{guid}", "nolimits2app.exe")
    assert not _matches_ride_window("Mdx Logo Coaster", title, "NL3D_MAIN_{guid}", "")
