# test_dynamic_import_filter_v215.py ver 1
"""Platform-aware POC dynamic-package chooser regressions."""
from pathlib import Path

from core.dynamic_import import discover_candidate_packages, import_candidate
from tools.nl2_dynamic_profiles.package import prepare_bundle
from tests.test_dynamic_runtime_v201 import _fixture


def test_discovery_shows_only_current_platform_and_hides_imported(tmp_path):
    chair_root, chair_source, chair_catalog, _ = _fixture(
        tmp_path / 'chair', 'chair_v3', 'blackbird')
    _ground_root, ground_source, _ground_catalog, _ = _fixture(
        tmp_path / 'ground', 'platform_v2', 'blackbird')
    exports = tmp_path / 'exports'
    chair_package, _, _ = prepare_bundle(
        chair_source, 'Blackbird [Chair import]', export_dir=exports,
        token='aabbccddeeff')
    prepare_bundle(
        ground_source, 'Blackbird [Ground import]', export_dir=exports,
        token='112233445566')

    packages, counts = discover_candidate_packages(
        exports, platform_key='chair_v3', catalog_dir=chair_catalog)
    assert [row.path for row in packages] == [chair_package]
    assert counts['available'] == 1
    assert counts['other_platform'] == 1
    assert counts['already_imported'] == 0

    import_candidate(
        chair_package, platform_key='chair_v3', root=chair_root,
        catalog_dir=chair_catalog)
    packages, counts = discover_candidate_packages(
        exports, platform_key='chair_v3', catalog_dir=chair_catalog)
    assert packages == ()
    assert counts['already_imported'] == 1
    assert counts['other_platform'] == 1


def test_discovery_hides_invalid_zip_without_blocking_valid_packages(tmp_path):
    _root, source, catalog, _ = _fixture(
        tmp_path / 'chair', 'chair_v3', 'blackbird')
    exports = tmp_path / 'exports'
    package, _, _ = prepare_bundle(
        source, 'Blackbird [Valid import]', export_dir=exports,
        token='aabbccddeeff')
    (exports / 'not_a_candidate.zip').write_text('not a zip', encoding='utf-8')

    packages, counts = discover_candidate_packages(
        exports, platform_key='chair_v3', catalog_dir=catalog)
    assert [row.path for row in packages] == [package]
    assert counts['invalid'] == 1
