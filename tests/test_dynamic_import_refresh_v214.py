#test_dynamic_import_refresh_v214.py ver 1
"""Regression coverage for Pi-safe dynamic import and live catalogue refresh."""
from __future__ import annotations

from pathlib import Path

import pytest

import core.dynamic_import as dynamic_import
from core.dynamic_import import default_import_directory, import_candidate
from sims.nolimits2.sim import Sim
from sims.nolimits2.variant_store import VariantStore
from tools.nl2_dynamic_profiles.package import prepare_bundle
from tests.test_dynamic_runtime_v201 import _fixture


def test_default_import_directory_matches_analyzer_exports(tmp_path):
    assert default_import_directory(tmp_path) == (
        tmp_path.resolve() / 'exports' / 'dynamic_candidates')


def test_import_stages_on_runtime_filesystem(tmp_path, monkeypatch):
    root, source, catalog, _ = _fixture(tmp_path, 'chair_v3', 'blackbird')
    package, _, _ = prepare_bundle(
        source, 'Blackbird [Pi import]', export_dir=tmp_path / 'exports',
        token='aabbccddeeff')

    real_temporary_directory = dynamic_import.tempfile.TemporaryDirectory
    observed = {}

    def tracked_temporary_directory(*args, **kwargs):
        observed['dir'] = Path(kwargs['dir']).resolve()
        return real_temporary_directory(*args, **kwargs)

    monkeypatch.setattr(dynamic_import.tempfile, 'TemporaryDirectory',
                        tracked_temporary_directory)
    import_candidate(package, platform_key='chair_v3', root=root,
                     catalog_dir=catalog)
    assert observed['dir'] == root.resolve()


def test_nl2_append_only_variant_refresh_preserves_existing_indices():
    original = VariantStore.for_capture('chair_v3')
    old_entries = list(original.entries())
    added = dict(old_entries[0])
    added['label'] = added['label'] + ' [Imported]'
    refreshed = VariantStore('chair_v3', {
        'schema_version': 1,
        'platform_key': 'chair_v3',
        'variants': old_entries + [added],
    }, operator_filter=False)

    sim = Sim(client=object(), platform_key='chair_v3', variant_store=original,
              capture_mode=True)
    before = tuple(sim._rides)
    sim.reload_variant_store(refreshed)

    assert tuple(sim._rides[:len(before)]) == before
    assert sim._experiences[-1] == added['label']


def test_nl2_refresh_rejects_changes_to_existing_definitions():
    original = VariantStore.for_capture('chair_v3')
    old_entries = list(original.entries())
    changed = [dict(entry) for entry in old_entries]
    changed[0]['label'] = changed[0]['label'] + ' changed'
    refreshed = VariantStore('chair_v3', {
        'schema_version': 1,
        'platform_key': 'chair_v3',
        'variants': changed,
    }, operator_filter=False)

    sim = Sim(client=object(), platform_key='chair_v3', variant_store=original,
              capture_mode=True)
    with pytest.raises(RuntimeError, match='changed outside this session'):
        sim.reload_variant_store(refreshed)
