# test_dynamic_session_selector_v212.py ver 5
"""Session-folder selection, ride population and v212 terminology guards."""
from pathlib import Path
from types import SimpleNamespace
import json

from tools.sim_player import dynamic_ride_studies as sessions


def _recording(root, ride_key, display, repeat):
    path = root / ride_key / f'{display}_repeat{repeat:02d}_timing_20261117T120000_abcd1234.csv'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('sample', encoding='utf-8')
    return path


def _good(_path):
    return SimpleNamespace(rating='Good', tooltip=lambda: 'Good recording')


def test_find_sessions_searches_only_direct_session_children(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, 'suitability_hint', _good)
    root = tmp_path / 'captures' / 'dynamic_source'
    first = root / 'session_20261117T120000_aaaaaaaa'
    second = root / 'session_20261117T121000_bbbbbbbb'
    nested = root / 'archive' / 'session_20261117T122000_cccccccc'
    _recording(first, 'blackbird', 'Blackbird', 1)
    _recording(second, 'fenrir', 'Fenrir', 1)
    _recording(nested, 'tiltcoaster', 'Tilt_Coaster', 1)

    found = sessions.find_sessions(root)
    assert {row.path for row in found} == {first.resolve(), second.resolve()}
    assert nested.resolve() not in {row.path for row in found}


def test_session_tooltip_lists_only_rides_present(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, 'suitability_hint', _good)
    session = tmp_path / 'session_20261117T120000_aaaaaaaa'
    session.mkdir()
    (session / 'session.json').write_text(json.dumps({
        'selected_parks': ['blackbird', 'fenrir', 'mdxlogo']
    }), encoding='utf-8')
    _recording(session, 'blackbird', 'Blackbird', 1)
    _recording(session, 'fenrir', 'Fenrir', 1)

    summary = sessions.inspect_session(session)
    assert summary.rides == ('blackbird', 'fenrir')
    assert 'Blackbird' in summary.tooltip
    assert 'Fenrir' in summary.tooltip
    assert 'Mdx Logo' not in summary.tooltip


def test_workspace_always_starts_at_documented_default_and_does_not_persist_browse_root():
    source = (Path(__file__).parents[1] / 'dynamic_ride_workspace.py').read_text(encoding='utf-8')
    assert 'self._source_root = DEFAULT_SOURCE_ROOT.resolve()' in source
    assert "dynamic_ride_workflow_studies_root" not in source
    assert "Choose folder containing dynamic-source sessions" not in source
    assert "QPushButton('Choose folder...')" not in source
    assert "def browse_sessions_folder" not in source
    assert "description, tooltip = session_status_display(row, generated_root)" in source
    assert "self.session.setItemData(index, tooltip, Qt.ItemDataRole.ToolTipRole)" in source
    assert "Capture new session..." in source
    assert "Rides are populated from primary recordings in the selected source study" not in source


def test_normal_capture_ui_uses_fixed_dynamic_source_root_without_browse_controls():
    workspace = (Path(__file__).parents[1] / 'dynamic_ride_workspace.py').read_text(encoding='utf-8')
    capture = (Path(__file__).parents[1] / 'dynamic_source_dialog.py').read_text(encoding='utf-8')

    assert 'DynamicSourceCaptureDialog(initial_ride, self)' in workspace
    assert 'output_root=self._source_root' not in workspace
    assert 'def _browse_output' not in capture
    assert 'QPushButton("Browse...")' not in capture
    assert 'str(DEFAULT_OUTPUT_ROOT)' in capture
    assert 'output_root or DEFAULT_OUTPUT_ROOT' not in capture


def test_dynamic_workspace_primary_action_is_visually_distinct_from_secondary_buttons():
    source = (Path(__file__).parents[1] / 'dynamic_ride_workspace.py').read_text(encoding='utf-8')
    assert 'background:#263746' in source
    assert 'QPushButton#primaryActionButton' in source
    assert 'background:#147fc8' in source


def test_current_ui_terminology_uses_session_recording_and_capture_consistently():
    workspace = (Path(__file__).parents[1] / 'dynamic_ride_workspace.py').read_text(encoding='utf-8')
    capture = (Path(__file__).parents[1] / 'dynamic_source_dialog.py').read_text(encoding='utf-8')
    help_text = (Path(__file__).parents[1] / 'workspace_help_content.py').read_text(encoding='utf-8')
    player_ui = (Path(__file__).parents[1] / 'player_ui.py').read_text(encoding='utf-8')

    for text in (workspace, capture):
        assert 'Capture new study' not in text
        assert 'dynamic-source study' not in text.casefold()
    assert 'Study scope' not in capture
    assert 'Capture scope' in capture
    assert 'Open session report' in capture
    assert 'Return to dynamic study' not in player_ui
    assert 'Return to dynamic preparation' in player_ui
    assert 'dynamic-source session' in help_text


def test_session_processing_status_reports_partial_generated_reviewed_exported(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, 'suitability_hint', _good)
    session_dir = tmp_path / 'captures' / 'dynamic_source' / 'session_20261117T120000_aaaaaaaa'
    _recording(session_dir, 'blackbird', 'Blackbird', 1)
    summary = sessions.inspect_session(session_dir)
    generated_root = tmp_path / 'studies' / 'nl2' / 'dynamic_profiles'
    run = generated_root / 'run_001'
    run.mkdir(parents=True)
    (run / 'run_settings.json').write_text(json.dumps({
        'inputs': [str(session_dir)], 'rides': ['blackbird'], 'platforms': 'both'
    }), encoding='utf-8')

    status = sessions.session_processing_status(summary, generated_root)
    assert status['blackbird']['chair_v3'] == 'partial'
    assert status['blackbird']['platform_v2'] == 'partial'

    (run / 'report.json').write_text('{}', encoding='utf-8')
    chair = run / 'blackbird' / 'chair_v3' / 'strict' / 'profile.json'
    ground = run / 'blackbird' / 'platform_v2' / 'strict' / 'profile.json'
    for path, platform in ((chair, 'chair_v3'), (ground, 'platform_v2')):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'identity': {
            'park': 'blackbird', 'platform': platform
        }}), encoding='utf-8')
    package = tmp_path / 'exports' / 'chair.zip'
    package.parent.mkdir()
    package.write_bytes(b'zip-placeholder')
    chair_rel = chair.relative_to(run).as_posix()
    (run / 'analyzer_dynamic_workflow_state.json').write_text(json.dumps({
        'reviewed': [chair_rel], 'prepared': {chair_rel: str(package)}
    }), encoding='utf-8')

    status = sessions.session_processing_status(summary, generated_root)
    assert status['blackbird']['chair_v3'] == 'exported'
    assert status['blackbird']['platform_v2'] == 'generated'
    display, tooltip = sessions.session_status_display(summary, generated_root)
    assert '1 generated' in display
    assert '1 exported' in display
    assert 'Blackbird - Chair: Exported; Ground: Generated' in tooltip


def test_source_processing_details_make_previous_work_visible(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, 'suitability_hint', _good)
    session_dir = tmp_path / 'captures' / 'dynamic_source' / 'session_20261117T120000_aaaaaaaa'
    _recording(session_dir, 'blackbird', 'Blackbird', 1)
    summary = sessions.inspect_session(session_dir)
    generated_root = tmp_path / 'studies' / 'nl2' / 'dynamic_profiles'
    run = generated_root / 'profiles_20261117T180000_abcd1234'
    run.mkdir(parents=True)
    (run / 'run_settings.json').write_text(json.dumps({
        'inputs': [str(session_dir)], 'rides': ['blackbird'], 'platforms': 'chair_v3'
    }), encoding='utf-8')
    (run / 'report.json').write_text('{}', encoding='utf-8')
    profile = run / 'chair_v3' / 'blackbird' / 'strict' / 'profile.json'
    profile.parent.mkdir(parents=True)
    profile.write_text(json.dumps({'identity': {
        'park': 'blackbird', 'platform': 'chair_v3'
    }}), encoding='utf-8')

    details = sessions.session_processing_details(summary, generated_root)
    chair = details['blackbird']['chair_v3']
    assert chair['status'] == 'generated'
    assert chair['profile_set'] == run.name
    assert chair['target'] == 'strict'

    ride_text, ride_tip = sessions.ride_processing_display('blackbird', details['blackbird'])
    assert 'Blackbird' in ride_text
    assert 'Chair: Generated' in ride_text
    assert run.name in ride_tip

    platform_text, platform_tip = sessions.platform_processing_display(chair)
    assert platform_text.startswith('Generated')
    assert run.name in platform_text
    assert str(run) in platform_tip
