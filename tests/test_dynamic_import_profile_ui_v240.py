#test_dynamic_import_profile_ui_v240.py ver 1
"""Regression coverage for POC v240 dynamic-candidate import visibility/recovery."""
from pathlib import Path

import pytest

from core.dynamic_import import discover_candidate_packages, import_candidate
from core.dynamic_profiles import DynamicProfileError
from tools.nl2_dynamic_profiles.package import prepare_bundle
from tests.test_dynamic_runtime_v201 import _fixture


def test_overwritten_catalog_reoffers_and_restores_identical_existing_bundle(tmp_path):
    root, source, catalog, _ = _fixture(tmp_path, 'chair_v3', 'blackbird')
    exports = tmp_path / 'exports'
    package, _, _ = prepare_bundle(
        source, 'Blackbird [Dynamic A]', export_dir=exports,
        token='aabbccddeeff')
    catalog_file = catalog / 'chair_v3.json'
    original_catalog = catalog_file.read_bytes()

    label, relative = import_candidate(
        package, platform_key='chair_v3', root=root, catalog_dir=catalog)
    bundle = root / 'sims' / 'nolimits2' / Path(relative).parent
    assert bundle.is_dir()

    # Simulate accidental replacement of ride_variants/chair_v3.json while the
    # immutable runtime bundle from the previous import remains installed.
    catalog_file.write_bytes(original_catalog)

    packages, counts = discover_candidate_packages(
        exports, platform_key='chair_v3', catalog_dir=catalog)
    assert [row.label for row in packages] == [label]
    assert counts['available'] == 1

    restored_label, restored_relative = import_candidate(
        package, platform_key='chair_v3', root=root, catalog_dir=catalog)
    assert (restored_label, restored_relative) == (label, relative)

    packages, counts = discover_candidate_packages(
        exports, platform_key='chair_v3', catalog_dir=catalog)
    assert packages == ()
    assert counts['already_imported'] == 1


def test_recovery_never_overwrites_different_existing_bundle(tmp_path):
    root, source, catalog, _ = _fixture(tmp_path, 'chair_v3', 'blackbird')
    exports = tmp_path / 'exports'
    package, _, _ = prepare_bundle(
        source, 'Blackbird [Dynamic A]', export_dir=exports,
        token='aabbccddeeff')
    catalog_file = catalog / 'chair_v3.json'
    original_catalog = catalog_file.read_bytes()

    _label, relative = import_candidate(
        package, platform_key='chair_v3', root=root, catalog_dir=catalog)
    bundle = root / 'sims' / 'nolimits2' / Path(relative).parent
    catalog_file.write_bytes(original_catalog)
    profile = bundle / 'profile.json'
    profile.write_bytes(profile.read_bytes() + b'\n')

    with pytest.raises(DynamicProfileError, match='differs from the export'):
        import_candidate(
            package, platform_key='chair_v3', root=root, catalog_dir=catalog)


def test_profile_tab_source_exposes_count_zero_state_and_import_all():
    source = (Path(__file__).parents[1] / 'ui' / 'main_window.py').read_text(
        encoding='utf-8')
    assert "No dynamic candidates to import" in source
    assert "dynamic {noun} to import" in source
    assert "Import dynamic candidates..." in source
    assert "'Import all'" in source
    assert 'def packages_to_import(self):' in source
    assert 'self._update_dynamic_import_state(force=force)' in source
