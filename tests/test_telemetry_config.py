#test_telemetry_config.py  ver 5
from pathlib import Path

import pytest

from core.telemetry_config import TelemetryConfig, _ClassicalFilter


def test_load_save_and_scale(tmp_path: Path):
    path = tmp_path / "telemetry_config.ini"
    cfg = TelemetryConfig(path, source="local")
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
    assert not path.exists()
    assert cfg.local_path.read_text(encoding="utf-8").startswith("#telemetry_config.ini ver 2\n")


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
    cfg = TelemetryConfig(path, source="local")
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


def test_global_local_round_trip(tmp_path):
    path = tmp_path / "telemetry_config.ini"
    original = "[Gains]\nmaster = 80\n[Washout]\ntype = disabled\n"
    path.write_text(original, encoding="utf-8")
    cfg = TelemetryConfig(path)
    assert cfg.source == "global"
    cfg.save()
    assert not cfg.local_path.exists()
    cfg.select_source("local")
    values = cfg.snapshot()
    values["master_gain"] = 60
    cfg.apply_values(values)
    cfg.load()
    assert cfg.source == "global"
    assert cfg.snapshot()["master_gain"] == 80
    cfg.select_source("local")
    cfg.apply_values(values)
    cfg.save()
    assert path.read_text(encoding="utf-8") == original
    cfg = TelemetryConfig(path)
    assert cfg.source == "local"
    assert cfg.snapshot() == values
    cfg.select_source("global")
    cfg.load()
    assert cfg.source == "local"
    cfg.select_source("global")
    cfg.save()
    path.write_text(original.replace("80", "90"), encoding="utf-8")
    cfg = TelemetryConfig(path)
    assert cfg.source == "global"
    assert cfg.snapshot()["master_gain"] == 90
    cfg.select_source("local")
    assert cfg.snapshot() == values
    assert TelemetryConfig(path, source="global").snapshot()["master_gain"] == 90


def test_incomplete_local_rejected(tmp_path):
    cfg = TelemetryConfig(tmp_path / "telemetry_config.ini")
    cfg.local_path.write_text("[Gains]\nmaster = 50\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Incomplete local"):
        TelemetryConfig(cfg.path)
    assert TelemetryConfig(cfg.path, source="global").source == "global"


def test_sources_are_independent(tmp_path):
    first = TelemetryConfig(tmp_path / "nl2" / "telemetry_config.ini")
    second = TelemetryConfig(tmp_path / "sc" / "telemetry_config.ini")
    first.select_source("local")
    first.save()
    assert TelemetryConfig(first.path).source == "local"
    assert TelemetryConfig(second.path).source == "global"
