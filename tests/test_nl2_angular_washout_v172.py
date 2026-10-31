# test_nl2_angular_washout_v172.py ver 2
"""Regression tests for inherited and ride-specific Roll/Pitch filtering."""

from pathlib import Path

import pytest

from core.telemetry_config import TelemetryConfig
from sims.nolimits2 import config as nl2_config


def configured(tmp_path):
    cfg = TelemetryConfig(tmp_path / "telemetry_config.ini")
    values = cfg.snapshot()
    values["washout_type"] = "classical"
    values["enabled"] = [False, False, False, False, False, False]
    values["tau"] = [1.0] * 6
    cfg.apply_values(values)
    return cfg


def test_no_ride_profiles_assigned_initially():
    assert nl2_config.RIDE_ANGULAR_WASHOUT == {}
    assert nl2_config.angular_washout_overrides("Mdx Logo Coaster") == (None, None)
    assert nl2_config.angular_washout_overrides("Blackbird") == (None, None)


def test_ride_mapping_can_override_one_or_both_axes(monkeypatch):
    monkeypatch.setattr(nl2_config, "RIDE_ANGULAR_WASHOUT", {
        "Blackbird": (True, False),
        "Fenrir": (None, True),
    })
    assert nl2_config.angular_washout_overrides("Blackbird") == (True, False)
    assert nl2_config.angular_washout_overrides("Fenrir") == (None, True)
    assert nl2_config.angular_washout_overrides("Tilt Coaster") == (None, None)


@pytest.mark.parametrize("bad", [(True,), (1, False), ("true", None), None])
def test_bad_ride_override_rejected(monkeypatch, bad):
    monkeypatch.setattr(nl2_config, "RIDE_ANGULAR_WASHOUT", {"Blackbird": bad})
    with pytest.raises(ValueError):
        nl2_config.angular_washout_overrides("Blackbird")


def test_override_enables_selected_classical_mode_not_other_axes(tmp_path):
    cfg = configured(tmp_path)
    cfg.set_angular_washout_overrides(True, False)
    effective = cfg.effective_washout()
    assert effective["type"] == "classical"
    assert effective["enabled"] == [False, False, False, True, False, False]
    first = cfg.process((0, 0, 0, 0.5, 0.5, 0), 100, 0.05)
    assert 0 < first[3] < 0.5
    assert first[4] == 0.5
    assert cfg.snapshot()["enabled"] == [False] * 6


def test_inherit_restores_saved_global_flags(tmp_path):
    cfg = configured(tmp_path)
    values = cfg.snapshot()
    values["enabled"][3] = True
    values["enabled"][4] = False
    cfg.apply_values(values)
    cfg.set_angular_washout_overrides(False, True)
    assert cfg.effective_washout()["enabled"][3:5] == [False, True]
    cfg.set_angular_washout_overrides(None, None)
    assert cfg.effective_washout()["enabled"][3:5] == [True, False]


def test_global_disabled_mode_cannot_be_forced_on_by_ride(tmp_path):
    cfg = configured(tmp_path)
    values = cfg.snapshot()
    values["washout_type"] = "disabled"
    cfg.apply_values(values)
    cfg.set_angular_washout_overrides(True, True)
    assert cfg.effective_washout()["enabled"][3:5] == [False, False]
    assert cfg.process((0, 0, 0, 0.4, -0.6, 0), 100, 0.05)[3:5] == (0.4, -0.6)


def test_legacy_uses_shared_duration_and_resets_between_rides(tmp_path):
    cfg = configured(tmp_path)
    values = cfg.snapshot()
    values["washout_type"] = "legacy"
    values["legacy_duration"] = [12.0] * 6
    cfg.apply_values(values)
    cfg.set_angular_washout_overrides(True, True)
    assert cfg.process((0, 0, 0, 0.7, 0.8, 0), 100, 0.05)[3:5] == (0.7, 0.8)
    # New ride with identical policy still starts with clean filter memory.
    cfg.set_angular_washout_overrides(True, True, reset=True)
    assert cfg.process((0, 0, 0, 0, 0, 0), 100, 0.05)[3:5] == (0.0, 0.0)


def test_ride_overrides_never_persist_to_ini(tmp_path):
    cfg = configured(tmp_path)
    original = cfg.snapshot()
    cfg.set_angular_washout_overrides(True, True)
    cfg.save()
    assert cfg.snapshot() == original
    reloaded = TelemetryConfig(tmp_path / "telemetry_config.ini")
    assert reloaded.snapshot() == original
    assert reloaded.effective_washout()["roll_override"] is None


def test_live_core_sync_is_loaded_ride_aware():
    source = (Path(__file__).resolve().parents[1] / "core/poc_core.py").read_text()
    sync = source.split("    def _sync_nl2_angular_washout(self)", 1)[1].split("    def get_telemetry_config(", 1)[0]
    assert 'self.sim.loaded_variant_label' in sync
    assert 'self.sim.loaded_variant_snapshot()' in sync
    assert 'getattr(self.sim, "_ride_load_pending", False)' in sync
    assert 'set_angular_washout_overrides(roll, pitch, reset=True)' in sync
    assert 'self.telemetry_config.save()' not in sync
    assert 'self._sync_nl2_angular_washout()' in source


def test_analysis_uses_same_ride_policy_as_live_poc():
    source = (Path(__file__).resolve().parents[1] / "tools/analyze_nl2_csv.py").read_text()
    assert 'nl2_config.angular_washout_overrides(ride_name)' in source
    assert 'ride_name=capture_ride_name' not in source  # explicit independent CSV field reading
    assert 'ride_name=ride_name' in source


def test_capture_monitor_marks_raw_samples_as_unfiltered():
    source = (Path(__file__).resolve().parents[1] / "tools/capture_nl2_rides.py").read_text()
    assert '"angular_washout_policy"' in source
    assert '"POC policy only; raw capture is unfiltered"' in source


def test_offline_analyzer_replays_explicit_ride_override(monkeypatch):
    """Offline replay must use the same per-ride filter choice as production."""
    from tools.analyze_nl2_csv import analyze

    monkeypatch.setattr(nl2_config, "RIDE_ANGULAR_WASHOUT", {
        "Blackbird": (True, True),
    })
    sample = (0.5, 0.6, 0.0, 0.0, 0.0, 0.0)
    data = [(i * 0.05, 10.0, (0.0, 0.0, 0.0, 0.5, 0.6, 0.0), None)
            for i in range(1, 6)]
    frames, stats = analyze(data, "platform_v2", "Recorded heave", 0.8, 100,
                            True, ride_name="Blackbird")
    assert len(frames) == 5
    assert stats["washout_type"] == "classical"
    assert stats["washout_roll_enabled"]
    assert stats["washout_pitch_enabled"]
    assert 0 < frames[0]["norm"][3] < 0.5
    assert 0 < frames[0]["norm"][4] < 0.6
    # Without a ride profile, both saved-default angular axes bypass the filter.
    unmodified, plain = analyze(data, "platform_v2", "Recorded heave", 0.8,
                                100, True, ride_name="GrandNational")
    assert not plain["washout_roll_enabled"]
    assert unmodified[0]["norm"][3:5] == (0.5, 0.6)
