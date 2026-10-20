#test_telemetry_config.py  ver 4
from pathlib import Path

from core.telemetry_config import TelemetryConfig, _ClassicalFilter


def test_load_save_and_scale(tmp_path: Path):
    path = tmp_path / "telemetry_config.ini"
    cfg = TelemetryConfig(path)
    values = cfg.snapshot()
    values["master_gain"] = 50
    values["gains"] = [200, 100, 50, 100, 100, 100]
    values["washout_type"] = "disabled"
    cfg.apply_values(values)
    assert cfg.scale_without_washout((1, 1, 1, 0, 0, 0), 100)[:3] == (1.0, 0.5, 0.25)

    cfg.save()
    loaded = TelemetryConfig(path).snapshot()
    assert loaded["master_gain"] == 50
    assert loaded["gains"][:3] == [200, 100, 50]
    assert loaded["washout_type"] == "disabled"
    assert path.read_text(encoding="utf-8").startswith("#telemetry_config.ini ver 2\n")


def test_disabled_washout_is_pass_through(tmp_path: Path):
    cfg = TelemetryConfig(tmp_path / "none.ini")
    values = cfg.snapshot()
    values["washout_type"] = "disabled"
    cfg.apply_values(values)
    transform = (0.2, -0.3, 0.4, 0.5, -0.6, 0.7)
    assert cfg.process(transform, 100, 0.05) == transform


def test_legacy_decay_tracks_rising_input_then_decays_to_two_percent(tmp_path: Path):
    cfg = TelemetryConfig(tmp_path / "legacy.ini")
    values = cfg.snapshot()
    values["washout_type"] = "legacy"
    values["enabled"] = [True, False, False, False, False, False]
    values["legacy_duration"] = [12.0] * 6
    cfg.apply_values(values)

    assert cfg.process((0.8, 0, 0, 0, 0, 0), 100, 0.05)[0] == 0.8
    output = 0.8
    for _ in range(240):
        output = cfg.process((0, 0, 0, 0, 0, 0), 100, 0.05)[0]
    assert abs(output - 0.8 * 0.02) < 1.0e-9


def test_temporary_classical_z_does_not_change_saved_settings(tmp_path: Path):
    path = tmp_path / "telemetry_config.ini"
    cfg = TelemetryConfig(path)
    values = cfg.snapshot()
    values["washout_type"] = "classical"
    values["enabled"] = [False] * 6
    cfg.apply_values(values)
    saved_snapshot = cfg.snapshot()

    cfg.set_temporary_classical_z(True)
    first = cfg.process((0, 0, 0.5, 0, 0, 0), 100, 0.05)[2]
    second = cfg.process((0, 0, 0.0, 0, 0, 0), 100, 0.05)[2]

    assert 0.0 < first < 0.5
    assert second < 0.0
    assert cfg.snapshot() == saved_snapshot

    cfg.save()
    assert TelemetryConfig(path).snapshot() == saved_snapshot


def test_classical_gain_scales_output_without_entering_filter_feedback():
    unity = _ClassicalFilter(tau=1.0, gain=1.0)
    doubled = _ClassicalFilter(tau=1.0, gain=2.0)

    for value in (0.5, 0.5, 0.0, -0.25, 0.0):
        unity_output = unity.update(value, 0.05)
        doubled_output = doubled.update(value, 0.05)
        assert abs(doubled_output - 2.0 * unity_output) < 1.0e-12
