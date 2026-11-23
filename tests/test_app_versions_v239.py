#test_app_versions_v239.py ver 10
from pathlib import Path

import pytest

import app_versions


ROOT = Path(__file__).resolve().parents[1]


def _write_metadata(tmp_path: Path, global_release: int, dependency_lines: list[str]) -> None:
    (tmp_path / "file_versions.txt").write_text(
        f"#file_versions.txt ver 1\n# Global release: v{global_release}\n",
        encoding="utf-8",
    )
    (tmp_path / "runtime_dependencies.txt").write_text(
        "#runtime_dependencies.txt ver 1\n"
        "# Format: relative path|apps|last_runtime_release\n"
        + "\n".join(dependency_lines)
        + "\n",
        encoding="utf-8",
    )


def test_app_version_is_latest_relevant_global_release(tmp_path):
    _write_metadata(
        tmp_path,
        250,
        [
            "poc_only.py|poc|241",
            "analyzer_only.py|analyzer|249",
            "shared.py|poc,analyzer|245",
        ],
    )
    assert app_versions.get_app_version("poc", tmp_path) == 245
    assert app_versions.get_app_version("analyzer", tmp_path) == 249


def test_value_only_configuration_edit_does_not_change_app_version(tmp_path):
    _write_metadata(
        tmp_path,
        250,
        [
            "platform_config.py|poc|241",
            "analyzer.py|analyzer|249",
        ],
    )
    config = tmp_path / "platform_config.py"
    config.write_text("GAIN = 0.8\n", encoding="utf-8")
    before = app_versions.get_app_version("poc", tmp_path)
    config.write_text("GAIN = 0.9\n", encoding="utf-8")
    after = app_versions.get_app_version("poc", tmp_path)
    assert before == after == 241


def test_shared_runtime_dependency_advances_both_apps(tmp_path):
    _write_metadata(
        tmp_path,
        251,
        [
            "poc_only.py|poc|241",
            "analyzer_only.py|analyzer|249",
            "shared.py|poc,analyzer|251",
        ],
    )
    assert app_versions.get_app_versions(tmp_path) == {"analyzer": 251, "poc": 251}


def test_runtime_version_cannot_exceed_global_release(tmp_path):
    _write_metadata(tmp_path, 251, ["shared.py|poc,analyzer|252"])
    with pytest.raises(ValueError, match="newer than global release"):
        app_versions.get_app_version("poc", tmp_path)


def test_current_manifest_versions_and_membership_are_consistent():
    assert app_versions.read_global_release(ROOT) == 248
    assert app_versions.get_app_versions(ROOT) == {"analyzer": 247, "poc": 248}

    tracked = set()
    for raw in (ROOT / "file_versions.txt").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            tracked.add(line.split("|", 1)[0].strip())
    dependencies = app_versions.load_runtime_dependencies(ROOT)
    dependency_paths = {item.path for item in dependencies}
    assert dependency_paths <= tracked
    assert any(item.apps == frozenset({"poc", "analyzer"}) for item in dependencies)

    analyzer_paths = {item.path for item in dependencies if "analyzer" in item.apps}
    assert {
        "core/digital_twin.py",
        "core/telemetry_config.py",
        "tools/sim_player/canonical_sync.py",
        "tools/sim_player/canonical_workflow.py",
        "tools/sim_player/dynamic_panel.py",
        "tools/sim_player/dynamic_replay.py",
        "tools/sim_player/media_trajectory.py",
        "tools/sim_player/panel_state.py",
        "tools/sim_player/task_workspace.py",
    } <= analyzer_paths
    poc_paths = {item.path for item in dependencies if "poc" in item.apps}
    assert "core/dynamic_import.py" in poc_paths
    assert "core/dynamic_import.py" not in analyzer_paths

    dependency_releases = {item.path: item.last_runtime_release for item in dependencies}
    assert dependency_releases["core/poc_core.py"] == 248
    assert dependency_releases["core/config_store.py"] == 248
    assert dependency_releases["controls/physical_controls.py"] == 248
    assert dependency_releases["sims/nolimits2/sim.py"] == 246
    assert dependency_releases["kinematics/cfg_SuspendedChairV3.py"] == 247
    assert dependency_releases["sim_host_service/server.py"] == 243
    assert dependency_releases["sim_host_service/sims/spacecoaster.bat"] == 243
    assert dependency_releases["sims/spacecoaster/config.py"] == 243
    assert dependency_releases["sims/spacecoaster/sim.py"] == 243


def test_title_bars_use_the_derived_runtime_version():
    poc_source = (ROOT / "ui" / "main_window.py").read_text(encoding="utf-8")
    analyzer_source = (ROOT / "tools" / "sim_player" / "app.py").read_text(encoding="utf-8")
    analyzer_init = (ROOT / "tools" / "sim_player" / "__init__.py").read_text(encoding="utf-8")

    assert "_poc_runtime_version_label()" in poc_source
    assert 'APP_VERSION = app_version_label("analyzer")' in analyzer_init
    assert 'f"{PRODUCT_NAME} {APP_VERSION} — No recording loaded"' in analyzer_source
    assert 'f"{PRODUCT_NAME} {APP_VERSION} — {recording.ride} — {recording.csv_path.name}"' in analyzer_source
