# test_dynamic_source_capture_v0211.py ver 4
"""Headless contracts for Ride Analyzer dynamic-source capture integration."""
from __future__ import annotations

import json
from pathlib import Path

from tools.sim_player import PRODUCT_NAME, PRODUCT_SHORT_NAME, WORKSPACE_VERSION
from tools.sim_player.dynamic_source import (
    DynamicSourcePlan, annotate_session_scope, available_rides, capture_start_event,
    default_test_conditions, estimate_plan_duration, operator_available_ride_keys,
    report_path_from_output, session_path_from_output,
    source_recordings, translated_capture_output,
)


def test_product_name_changes_without_internal_package_rename():
    assert PRODUCT_NAME == "MDX Ride Analyzer"
    assert PRODUCT_SHORT_NAME == "Ride Analyzer"
    assert WORKSPACE_VERSION == "0.2.14"


def test_current_ride_plan_launches_existing_capture_module(tmp_path):
    plan = DynamicSourcePlan(
        scope="current", rides=("blackbird",), repeats=3, warmups=1,
        conditions="Rift active", extend_uncertain=True,
        output_root=tmp_path / "dynamic_source",
    )
    args = plan.command_arguments()
    assert args[:3] == ["-m", "tools.nl2_timing_study", "capture"]
    assert args[args.index("--rides") + 1] == "blackbird"
    assert args[args.index("--repeats") + 1] == "3"
    assert args[args.index("--warmups") + 1] == "1"
    assert args[args.index("--conditions") + 1] == "Rift active"
    assert "--monitors" not in args
    assert "--extend-uncertain" in args
    assert args[args.index("--output") + 1].endswith("dynamic_source")


def test_quick_keeps_short_capture_preset(tmp_path):
    plan = DynamicSourcePlan(scope="quick", repeats=3, output_root=tmp_path)
    args = plan.command_arguments()
    assert args[args.index("--preset") + 1] == "quick"
    assert "--rides" not in args


def test_standard_and_extended_use_current_operator_visible_rides(tmp_path):
    expected = ",".join(operator_available_ride_keys())
    assert expected
    for scope, repeats in (("standard", 3), ("extended", 5)):
        plan = DynamicSourcePlan(scope=scope, repeats=repeats, output_root=tmp_path)
        args = plan.command_arguments()
        assert args[args.index("--preset") + 1] == scope
        assert args[args.index("--rides") + 1] == expected
        assert args[args.index("--repeats") + 1] == str(repeats)


def test_explicit_ride_list_follows_operator_visibility():
    rides = {ride.key: ride for ride in available_rides()}
    assert "blackbird" in rides
    assert "grandnational" not in rides
    assert "scenicrun" not in rides
    assert tuple(rides) == operator_available_ride_keys()


def test_capture_output_is_presented_as_dynamic_study():
    line = "Timing study: HOST; NL2 127.0.0.1:15151; output C:/repo/captures/dynamic_source/session_1"
    shown = translated_capture_output(line)
    assert shown.startswith("Dynamic study:")
    assert "Timing study:" not in shown
    assert session_path_from_output(line) == Path("C:/repo/captures/dynamic_source/session_1")
    assert translated_capture_output("MEASURED Blackbird 2") == "DYNAMIC SOURCE Blackbird 2"
    assert "dynamic source 2" in translated_capture_output("COMPLETE: Blackbird repeat 2: 100 player samples")
    assert report_path_from_output("Report: C:/repo/report/report.html") == Path("C:/repo/report/report.html")


def test_completed_non_warmup_recordings_are_discovered(tmp_path):
    ride = tmp_path / "blackbird"
    ride.mkdir()
    good_csv = ride / "good.csv"; good_csv.write_text("x\n")
    warm_csv = ride / "warm.csv"; warm_csv.write_text("x\n")
    incomplete_csv = ride / "bad.csv"; incomplete_csv.write_text("x\n")
    (ride / "good_trial.json").write_text(json.dumps({
        "ride": "Blackbird", "repeat": 2, "warmup": False, "status": "complete",
        "files": {"capture": good_csv.name},
    }))
    (ride / "warm_trial.json").write_text(json.dumps({
        "ride": "Blackbird", "repeat": 1, "warmup": True, "status": "complete",
        "files": {"capture": warm_csv.name},
    }))
    (ride / "bad_trial.json").write_text(json.dumps({
        "ride": "Blackbird", "repeat": 3, "warmup": False, "status": "incomplete",
        "files": {"capture": incomplete_csv.name},
    }))
    assert source_recordings(tmp_path) == (good_csv,)


def test_dialog_source_uses_qprocess_and_has_no_hardware_output_dependency():
    source = (Path(__file__).resolve().parents[1] / "dynamic_source_dialog.py").read_text()
    assert "QProcess" in source
    assert "tools.nl2_timing_study" not in source  # command building is isolated in the Qt-free helper
    assert "Festo" not in source
    assert "sim_host_service" not in source
    assert "Capture Dynamic Source Recordings" in source
    assert "selected Ride Analyzer candidate is not used" in source
    assert 'addItem("Custom rides", "selected")' in source
    assert 'QPushButton("Choose rides...")' in source
    assert '"Test conditions (optional)"' in source
    assert '"Monitors"' not in source
    assert "Key_Return" in source and "Key_Enter" in source and "Key_Space" in source


def test_duration_estimate_uses_selected_ride_count(monkeypatch, tmp_path):
    from tools.sim_player import dynamic_source
    durations = {"blackbird": (90.0, "canonical"), "fenrir": (50.0, "canonical")}
    monkeypatch.setattr(dynamic_source, "typical_ride_duration_s", lambda key: durations.get(key, (None, None)))
    plan = DynamicSourcePlan(scope="selected", rides=("blackbird", "fenrir"), repeats=3, warmups=0, output_root=tmp_path)
    estimate = estimate_plan_duration(plan)
    assert estimate.seconds == 420.0
    assert estimate.missing == ()


def test_capture_start_event_recognises_measured_and_warmup():
    assert capture_start_event("MEASURED Blackbird 2") == ("blackbird", 2, False)
    assert capture_start_event("WARMUP Tilt Coaster 1") == ("tiltcoaster", 1, True)
    assert capture_start_event("COMPLETE: Blackbird repeat 2: 100 player samples") is None


def test_scenic_run_hidden_from_operator_and_analyzer():
    from sims.nolimits2 import ride_config
    assert "scenicrun" in ride_config.ANALYZER_HIDDEN_PARKS
    assert "scenicrun" in ride_config.POC_HIDDEN_PARKS
    assert "scenicrun" not in ride_config.OPERATOR_HIDDEN_PARKS


def test_scenic_run_not_offered_by_analyzer_browsers():
    base = Path(__file__).resolve().parents[1]
    for name in ("candidate_browser.py", "canonical_dialog.py", "dynamic_browser.py"):
        source = (base / name).read_text()
        assert "ANALYZER_HIDDEN_PARKS" in source

    from sims.nolimits2.variant_store import VariantStore
    store = VariantStore.load_readonly("chair_v3")
    assert not any(label.startswith("Scenic Run") for label in store.player_labels())
    assert any(label.startswith("GrandNational") for label in store.player_labels())


def test_dialog_source_contains_estimate_and_live_start_update():
    source = (Path(__file__).resolve().parents[1] / "dynamic_source_dialog.py").read_text()
    assert '"Estimated capture time"' in source
    assert "capture_start_event" in source
    assert "min remaining" in source


def test_default_test_conditions_reuses_host_probe_and_filters_virtual_gpu(monkeypatch):
    from tools.sim_player import dynamic_source
    monkeypatch.setattr(dynamic_source, "_windows_cpu_model", lambda: "Intel Core Test CPU")
    monkeypatch.setattr(dynamic_source, "_host_details", lambda: {
        "processor": "generic processor",
        "display_controllers": [
            {"Name": "Meta Virtual Monitor"},
            {"Name": "NVIDIA GeForce GTX 1080 Ti"},
        ],
    })
    text = default_test_conditions()
    assert text == (
        "Normal deployed configuration; CPU: Intel Core Test CPU; "
        "GPU: NVIDIA GeForce GTX 1080 Ti"
    )


def test_default_test_conditions_falls_back_without_hardware(monkeypatch):
    from tools.sim_player import dynamic_source
    monkeypatch.setattr(dynamic_source, "_windows_cpu_model", lambda: "")
    monkeypatch.setattr(dynamic_source, "_host_details", lambda: {})
    assert default_test_conditions() == "Normal deployed configuration"


def test_session_scope_provenance_is_added_without_rewriting_low_level_preset(tmp_path):
    (tmp_path / "session.json").write_text(json.dumps({"preset": "quick", "trials": []}))
    assert annotate_session_scope(tmp_path, "selected")
    data = json.loads((tmp_path / "session.json").read_text())
    assert data["preset"] == "quick"
    assert data["study_scope"] == "custom"
    assert annotate_session_scope(tmp_path, "extended")
    data = json.loads((tmp_path / "session.json").read_text())
    assert data["study_scope"] == "extended"


def test_dialog_prefills_hardware_conditions_and_records_scope():
    source = (Path(__file__).resolve().parents[1] / "dynamic_source_dialog.py").read_text()
    assert "setText(default_test_conditions())" in source
    assert "annotate_session_scope" in source

