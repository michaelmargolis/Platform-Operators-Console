# test_dynamic_export_inventory_v224.py ver 8
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

from tools.sim_player.dynamic_export_inventory import (
    candidate_identity,
    compare_same_name_exports,
    discover_exported_candidates,
    short_candidate_label,
)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _package(path: Path, *, platform='chair_v3', park='blackbird',
             label='Blackbird [Dynamic A]', target='strict', recordings=3,
             content_tag='same'):
    token = path.stem.replace('-', '_')
    deployment_path = f'sims/nolimits2/ride_profiles/{platform}/{park}/{token}/deployment.json'
    deployment = {
        'schema': 'test',
        'platform': platform,
        'park': park,
        'file_names': ['profile.json', 'reference.npz', 'qualification.json'],
        'sha256': {
            'profile.json': _digest(content_tag + ':profile'),
            'reference.npz': _digest(content_tag + ':reference'),
            'qualification.json': _digest(content_tag + ':qualification'),
        },
        'profile_identity': {
            'platform': platform,
            'park': park,
            'content_tag': content_tag,
        },
    }
    payload = {
        'schema': 'poc.nl2.dynamic-candidate-package/1',
        'candidate': {'label': label, 'park': park, 'status': 'candidate'},
        'platform': platform,
        'park': park,
        'deployment_path': deployment_path,
        'qualification_summary': {
            'platform': platform,
            'park': park,
            'target': target,
            'source_recordings': recordings,
        },
        'operational_status': 'uncommissioned_candidate',
    }
    with ZipFile(path, 'w') as archive:
        archive.writestr('dynamic_candidate.json', json.dumps(payload))
        archive.writestr(deployment_path, json.dumps(deployment))


def test_discovers_platform_ride_label_and_qualification_target(tmp_path):
    folder = tmp_path / 'exports' / 'dynamic_candidates'
    folder.mkdir(parents=True)
    _package(folder / 'blackbird.zip')
    _package(
        folder / 'flying.zip', platform='platform_v2', park='flying_beast',
        label='Flying Beast [Dynamic B]', target='relaxed5', recordings=5)

    rows, invalid = discover_exported_candidates(folder)

    assert invalid == 0
    assert len(rows) == 2
    by_label = {row.label: row for row in rows}
    assert by_label['Blackbird [Dynamic A]'].target == 'strict'
    assert dict(by_label['Blackbird [Dynamic A]'].content_hashes)['profile.json']
    flying = by_label['Flying Beast [Dynamic B]']
    assert flying.platform == 'platform_v2'
    assert flying.park == 'flying_beast'
    assert flying.target == 'relaxed5'
    assert flying.source_recordings == 5
    assert flying.operational_status == 'uncommissioned_candidate'


def test_same_ride_dynamic_a_and_b_are_both_discovered(tmp_path):
    folder = tmp_path / 'exports' / 'dynamic_candidates'
    folder.mkdir(parents=True)
    _package(folder / 'flying_a.zip', park='flying_beast',
             label='Flying Beast [Dynamic A]', target='strict')
    _package(folder / 'flying_b.zip', park='flying_beast',
             label='Flying Beast [Dynamic B]', target='relaxed5')

    rows, invalid = discover_exported_candidates(folder)

    assert invalid == 0
    assert [(row.park, row.label) for row in rows] == [
        ('flying_beast', 'Flying Beast [Dynamic A]'),
        ('flying_beast', 'Flying Beast [Dynamic B]'),
    ]


def test_same_name_duplicate_exports_report_identical_bound_content(tmp_path):
    folder = tmp_path / 'exports'
    folder.mkdir()
    _package(folder / 'first.zip', content_tag='candidate-a')
    _package(folder / 'second.zip', content_tag='candidate-a')
    rows, invalid = discover_exported_candidates(folder)

    state, summary, details = compare_same_name_exports(rows)

    assert invalid == 0
    assert state == 'identical'
    assert 'identical bound content' in summary
    assert 'first.zip' in details and 'second.zip' in details
    assert candidate_identity(rows[0]) == candidate_identity(rows[1])


def test_same_name_duplicate_exports_report_hash_and_metadata_differences(tmp_path):
    folder = tmp_path / 'exports'
    folder.mkdir()
    _package(folder / 'first.zip', content_tag='candidate-a', target='strict', recordings=3)
    _package(folder / 'second.zip', content_tag='candidate-b', target='relaxed5', recordings=5)
    rows, _invalid = discover_exported_candidates(folder)

    state, summary, details = compare_same_name_exports(rows)

    assert state == 'different'
    assert 'DIFFERENT' in summary
    assert 'profile.json differs' in details
    assert 'reference.npz differs' in details
    assert 'qualification.json differs' in details
    assert 'qualification target differs' in details
    assert 'source recording count differs' in details



def test_same_bound_content_but_different_metadata_is_reported_different(tmp_path):
    folder = tmp_path / 'exports_metadata'
    folder.mkdir()
    _package(folder / 'first.zip', content_tag='same-runtime', target='strict', recordings=3)
    _package(folder / 'second.zip', content_tag='same-runtime', target='relaxed5', recordings=3)
    rows, _invalid = discover_exported_candidates(folder)

    state, _summary, details = compare_same_name_exports(rows)

    assert state == 'different'
    assert 'qualification target differs' in details
    assert 'profile.json differs' not in details

def test_invalid_or_non_candidate_zip_is_counted_not_listed(tmp_path):
    folder = tmp_path / 'exports'
    folder.mkdir()
    (folder / 'broken.zip').write_bytes(b'not-a-zip')
    with ZipFile(folder / 'not_candidate.zip', 'w') as archive:
        archive.writestr('other.json', '{}')
    _package(folder / 'good.zip')

    rows, invalid = discover_exported_candidates(folder)

    assert [row.label for row in rows] == ['Blackbird [Dynamic A]']
    assert invalid == 2


def test_wrong_platform_is_not_listed_but_unknown_target_is_inventory_visible(tmp_path):
    folder = tmp_path / 'exports'
    folder.mkdir()
    _package(folder / 'wrong_platform.zip', platform='other_platform')
    _package(folder / 'unknown_target.zip', target='future_target')

    rows, invalid = discover_exported_candidates(folder)

    assert [row.label for row in rows] == ['Blackbird [Dynamic A]']
    assert rows[0].target == 'future_target'
    assert invalid == 1


def test_missing_folder_is_empty_without_error(tmp_path):
    rows, invalid = discover_exported_candidates(tmp_path / 'missing')
    assert rows == ()
    assert invalid == 0


def test_short_candidate_label_keeps_only_dynamic_suffix_when_possible():
    assert short_candidate_label('Flying Beast [Dynamic A]', 'Flying Beast') == 'Dynamic A'
    assert short_candidate_label('Blackbird engineering candidate', 'Blackbird') == 'engineering candidate'
    assert short_candidate_label('Custom label', 'Blackbird') == 'Custom label'


def test_workspace_contract_has_persistent_export_inventory_and_duplicate_tooltip():
    source = (Path(__file__).parents[1] / 'dynamic_ride_workspace.py').read_text(encoding='utf-8')
    assert "QGroupBox('Exported POC candidates')" in source
    assert "self.content_row.addWidget(export_group)" in source
    assert "self.export_tree.itemDoubleClicked.connect(self._export_item_double_clicked)" in source
    assert "self.export_refresh_btn.clicked.connect(self.refresh_exported_candidates)" in source
    assert "QFileSystemWatcher" in source
    assert "self.export_watcher.directoryChanged.connect(self._export_folder_changed)" in source
    assert "for row in platform_rows:" in source
    assert "markers = {'chair_v3': '●', 'platform_v2': '◆'}" in source
    assert "group.setFirstColumnSpanned(True)" in source
    assert "setFirstItemColumnSpanned" not in source
    assert "setAlternatingRowColors(True)" not in source
    assert "QPushButton('Compare duplicates...')" not in source
    assert 'def _compare_selected_export_duplicates' not in source
    assert "compare_same_name_exports(group)" in source
    assert "'different': 'DIFFERENT'" in source
    assert r"Same-name export comparison:\n' + details" in source
    assert "tooltip += f'\\nQualification target: {target}'" in source
    assert "else:\n                    target = TARGET_LABELS.get(" in source
    assert "TARGET_ROW_LABELS = {'strict': 'Strict', 'relaxed5': 'Relaxed'}" in source
    assert "candidate_name = f'{candidate_name}  {target_text}'" in source
    assert 'export_group.setMinimumWidth(440)' in source
    assert 'export_group.setMaximumWidth(540)' in source
    export_block = source.split('def _export_profile_for_platform', 1)[1].split(
        'def _open_platform_export_folder', 1)[0]
    assert 'self.refresh_exported_candidates()' in export_block
    # Clicking/selecting an exported package is informational only; it must not
    # be wired to source session/ride selection.
    selection_block = source.split('def _export_selection_changed', 1)[1].split('def _export_item_double_clicked', 1)[0]
    assert 'setCurrentIndex' not in selection_block
    assert '_source_context_changed' not in selection_block
