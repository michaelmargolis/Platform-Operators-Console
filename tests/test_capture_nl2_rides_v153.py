# test_capture_nl2_rides_v153.py  ver 4
"""Offline checks for the separate NL2 ride study utility."""
import importlib.util
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1] / "tools" / "capture_nl2_rides.py"
spec = importlib.util.spec_from_file_location("capture_nl2_rides", PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_ride_selection():
    rides = (("Mdx Logo Coaster", "p1"), ("Fenrir", "p2"), ("Blackbird", "p3"))
    assert module.selected_indices("1,3,1", rides) == [0, 2]
    assert module.selected_indices("fenrir", rides) == [1]
    assert module.selected_indices("all", rides) == [0, 1, 2]
    with pytest.raises(ValueError):
        module.selected_indices("4", rides)


def test_columns_cover_raw_and_all_six_normalized_axes():
    for axis in ("surge", "sway", "heave", "roll", "pitch", "yaw"):
        assert f"norm_{axis}" in module.COLUMNS
    for field in ("pos_y", "g_force_y", "quat_w", "frame", "speed"):
        assert field in module.RAW_FIELDS
    assert "legacy_height_heave" in module.COLUMNS
    assert "vertical_g_heave" in module.COLUMNS


class FakeClient:
    def __init__(self, outcome):
        self.outcome = outcome

    def request_result(self, request_id):
        assert request_id == 37
        return self.outcome


class FakeSim:
    def __init__(self, outcome):
        self.client = FakeClient(outcome)
        self._ride_load_pending = False
        self.state = module.ExperienceState.READY


def test_confirmed_load_success(monkeypatch, capsys):
    monkeypatch.setattr(module, "pump", lambda sim: (None, None))
    module.wait_for_park_load(FakeSim(("ok", "")), 37, "Blackbird", "C:/Blackbird.nl2park", 0.2, 0)
    assert "acknowledged OK" in capsys.readouterr().out


def test_load_error_prevents_dispatch(monkeypatch):
    monkeypatch.setattr(module, "pump", lambda sim: (None, None))
    with pytest.raises(RuntimeError, match="NL2 rejected LOAD_PARK.*bad park"):
        module.wait_for_park_load(FakeSim(("error", "bad park")), 37, "Blackbird", "C:/x", 0.2, 0)


def test_pending_or_unexpected_outcome(monkeypatch):
    monkeypatch.setattr(module, "pump", lambda sim: (None, None))
    with pytest.raises(RuntimeError, match="Unexpected LOAD_PARK"):
        module.wait_for_park_load(FakeSim(("unknown", "")), 37, "Blackbird", "C:/x", 0.2, 0)
    with pytest.raises(RuntimeError, match="No LOAD_PARK request ID"):
        module.wait_for_park_load(FakeSim(None), None, "Blackbird", "C:/x", 0.2, 0)


def test_busy_error_is_distinguished_from_invalid_path(monkeypatch):
    monkeypatch.setattr(module, "pump", lambda sim: (None, None))
    with pytest.raises(module.ParkLoadBusyError, match="processing another operation"):
        module.wait_for_park_load(
            FakeSim(("error", "Application is processing another operation")),
            37, "Blackbird", "C:/Blackbird.nl2park", 0.2, 0,
        )


class RideSelectingSim:
    def __init__(self):
        self._selected = 0
        self._rides = (("Mdx Logo Coaster", "default"), ("Blackbird", "blackbird"))
        self._ride_load_request_id = None
        self.select_calls = []
        self.reload_calls = []

    def select_experience(self, target):
        self.select_calls.append(target)
        self._selected = target
        self._ride_load_request_id = 1

    def _begin_ride_load(self, target, startup=False):
        self.reload_calls.append((target, startup))
        self._selected = target
        self._ride_load_request_id = 2


def test_transient_busy_retries_same_ride_without_changing_default(monkeypatch):
    sim = RideSelectingSim()
    attempts = []
    wait_s = []

    def response(_sim, request_id, *args):
        attempts.append(request_id)
        if request_id == 1:
            raise module.ParkLoadBusyError("Application is processing another operation")

    monkeypatch.setattr(module, "wait_for_park_load", response)
    monkeypatch.setattr(module, "wait_between_load_attempts",
                        lambda *args: wait_s.append(args[1]))
    module.select_ride_for_capture(sim, 1, 1, 0.05)
    assert sim.select_calls == [1]
    assert sim.reload_calls == [(1, False)]
    assert attempts == [1, 2]
    assert wait_s == [module.BUSY_RETRY_DELAY_S]
    assert sim._selected == 1


def test_non_busy_error_does_not_retry(monkeypatch):
    sim = RideSelectingSim()
    def reject(*args):
        raise RuntimeError("NL2 rejected LOAD_PARK: missing park")
    monkeypatch.setattr(module, "wait_for_park_load", reject)
    with pytest.raises(RuntimeError, match="missing park"):
        module.select_ride_for_capture(sim, 1, 1, 0.05)
    assert sim.select_calls == [1]
    assert sim.reload_calls == []


def test_busy_retry_is_bounded(monkeypatch):
    sim = RideSelectingSim()
    monkeypatch.setattr(module, "wait_for_park_load",
                        lambda *args: (_ for _ in ()).throw(module.ParkLoadBusyError("busy")))
    monkeypatch.setattr(module, "wait_between_load_attempts", lambda *args: None)
    with pytest.raises(RuntimeError, match="remained busy"):
        module.select_ride_for_capture(sim, 1, 1, 0.05)
    assert len(sim.reload_calls) == module.BUSY_RETRIES


def test_repeat_same_ride_prepares_station_and_dispatches_without_reload(monkeypatch):
    sim = RideSelectingSim()
    sim._selected = 1
    sim.prepare_calls = 0
    sim.run_calls = 0

    def prepare():
        sim.prepare_calls += 1

    def run():
        sim.run_calls += 1
        sim.state = module.ExperienceState.RUNNING
        sim._dispatch_pending = False

    sim.prepare_for_activation = prepare
    sim.run = run
    monkeypatch.setattr(module, "await_condition", lambda *args: None)
    module.start_ride(sim, 1, 1, 0.05)
    module.start_ride(sim, 1, 1, 0.05)
    assert sim.select_calls == []
    assert sim.reload_calls == []
    assert sim.prepare_calls == sim.run_calls == 2


def test_main_reuses_one_connection_for_all_rides_and_repeats(monkeypatch, tmp_path):
    sessions = []
    recorded = []

    class RecordingSession:
        def __init__(self, address, startup_ride_index=None):
            self.address = address
            self.startup_ride_index = startup_ride_index
            self.disconnected = 0
            sessions.append(self)

        def disconnect(self):
            self.disconnected += 1

    monkeypatch.setattr(module, "Sim", RecordingSession)
    monkeypatch.setattr(module, "connect_recorder", lambda *args: None)
    monkeypatch.setattr(module, "capture_one",
                        lambda sim, index, repeat, *args: recorded.append((sim, index, repeat)))
    assert module.main(["--rides", "1,2", "--repeats", "2", "--output-dir", str(tmp_path)]) == 0
    assert len(sessions) == 1
    assert sessions[0].startup_ride_index == 0
    assert sessions[0].disconnected == 1
    assert [(index, repeat) for _, index, repeat in recorded] == [
        (0, 1), (0, 2), (1, 1), (1, 2),
    ]
    assert all(sim is sessions[0] for sim, _, _ in recorded)


def test_main_chooses_blackbird_as_initial_park_without_changing_normal_default(monkeypatch, tmp_path):
    created = []
    class Session:
        def __init__(self, address, startup_ride_index=None):
            created.append(startup_ride_index)
        def disconnect(self):
            pass
    monkeypatch.setattr(module, "Sim", Session)
    monkeypatch.setattr(module, "connect_recorder", lambda *args: None)
    monkeypatch.setattr(module, "capture_one", lambda *args: None)
    assert module.main(["--rides", "2,3", "--repeats", "1", "--output-dir", str(tmp_path)]) == 0
    assert created == [1]
