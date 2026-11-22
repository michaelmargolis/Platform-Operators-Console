# test_dynamic_session_rides_v211.py ver 2
"""Session-driven dynamic ride selection and generic generation/package scope."""
from pathlib import Path
from types import SimpleNamespace
import json

from tools.sim_player import dynamic_ride_studies as studies


def _primary(folder, ride_name, repeat, stamp):
    path = folder / f'{ride_name}_repeat{repeat:02d}_timing_{stamp}_abcd1234.csv'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('sample', encoding='utf-8')
    return path


def test_available_rides_come_from_selected_session_data(tmp_path, monkeypatch):
    session = tmp_path / 'session_20261117T000000_abcd1234'
    session.mkdir()
    (session / 'session.json').write_text(json.dumps({
        'selected_parks': ['fenrir', 'tiltcoaster', 'blackbird']
    }), encoding='utf-8')
    _primary(session / 'fenrir', 'Fenrir', 1, '20261117T000001')
    _primary(session / 'fenrir', 'Fenrir', 2, '20261117T000101')
    _primary(session / 'tiltcoaster', 'Tilt_Coaster', 1, '20261117T000201')

    monkeypatch.setattr(
        studies, 'suitability_hint',
        lambda _: SimpleNamespace(rating='Good', tooltip=lambda: 'Good')
    )
    summary = studies.inspect_study(session)
    assert studies.available_rides(summary) == ('fenrir', 'tiltcoaster')
    assert 'blackbird' not in summary.rides
    assert len(studies.rated_recordings(summary, 'fenrir')) == 2
    assert len(studies.rated_recordings(summary, 'tiltcoaster')) == 1


def test_recording_ride_recognises_all_current_park_names():
    examples = {
        'Fenrir_repeat01_timing_20261117T000000_abcd1234.csv': 'fenrir',
        'Flying_Beast_repeat01_timing_20261117T000000_abcd1234.csv': 'flyingbeast',
        'Forest_Mountain_Park_repeat01_timing_20261117T000000_abcd1234.csv': 'forestmountain',
        'Hybris_repeat01_timing_20261117T000000_abcd1234.csv': 'hybris',
        'Tilt_Coaster_repeat01_timing_20261117T000000_abcd1234.csv': 'tiltcoaster',
        'Wilderness_Park_repeat01_timing_20261117T000000_abcd1234.csv': 'wilderness',
        'Blackbird_repeat01_timing_20261117T000000_abcd1234.csv': 'blackbird',
        'Mdx_Logo_Coaster_repeat01_timing_20261117T000000_abcd1234.csv': 'mdxlogo',
    }
    for filename, expected in examples.items():
        assert studies.recording_ride(filename) == expected


def test_workspace_source_contract_is_session_driven_and_buttons_are_distinct():
    source = (Path(__file__).parents[1] / 'dynamic_ride_workspace.py').read_text(encoding='utf-8')
    assert 'self.populate_rides(self._preferred_ride)' in source
    assert 'rides = available_rides(session)' in source
    assert 'self.ride.clear()' in source
    assert 'RIDE_LABELS =' not in source
    assert 'background:#2f6f9d' in source
    assert 'QPushButton#primaryActionButton' in source
    assert 'background:#147fc8' in source
    assert 'background:#182838' in source  # combo-box field, visually distinct from buttons


def test_generator_and_package_are_not_limited_to_two_named_rides():
    generator = (Path(__file__).parents[2] / 'nl2_dynamic_profiles' / '__main__.py').read_text(encoding='utf-8')
    package = (Path(__file__).parents[2] / 'nl2_dynamic_profiles' / 'package.py').read_text(encoding='utf-8')
    assert "issubset({'mdxlogo','blackbird'})" not in generator
    assert "park not in ('blackbird', 'mdxlogo')" not in package
    assert 'ride_config.PARKS' in generator
    assert 'ride_config.PARKS' in package
