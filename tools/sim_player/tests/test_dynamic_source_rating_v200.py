# test_dynamic_source_rating_v200.py ver 1
from __future__ import annotations

import csv
import json
from pathlib import Path

from tools.sim_player.dynamic_source_rating import suitability_hint


def _write_csv(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0])
    with path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _run_row(name, *, repeat=1, eligible=True, status='complete', gaps=0,
             backwards=0, equal=0, duplicates=0, skipped=0, issues='', error=''):
    return {
        'trial': rf'C:\captures\session_x\ride\{name}_trial.json',
        'ride': 'Ride',
        'park': 'ride',
        'repeat': str(repeat),
        'status': status,
        'motion_to_return_s': '100.123',
        'path_distance_m': '800.25',
        'max_unique_gap_ms': '70.0',
        'gaps_above_threshold': str(gaps),
        'gap_threshold_s': '0.25',
        'duplicate_replies': str(duplicates),
        'equal_timestamp_observations': str(equal),
        'backwards_timestamp_observations': str(backwards),
        'skipped_poll_ticks': str(skipped),
        'issues': issues,
        'error': error,
        'eligible': str(bool(eligible)),
    }


def _session(tmp_path: Path):
    session = tmp_path / 'captures' / 'dynamic_source' / 'session_20261114T120000_deadbeef'
    ride = session / 'ride'
    ride.mkdir(parents=True)
    return session, ride


def test_clean_representative_is_suggested(tmp_path):
    session, ride = _session(tmp_path)
    base = 'Ride_repeat02_timing_20261114T120100_1234abcd'
    path = ride / f'{base}.csv'
    path.write_text('dummy', encoding='utf-8')
    _write_csv(session / 'report' / 'runs.csv', [_run_row(base, repeat=2)])
    _write_csv(session / 'report' / 'groups.csv', [{
        'reference': base, 'complete_measured_runs': '5', 'note': ''
    }])
    hint = suitability_hint(path)
    assert hint.rating == 'Suggested'
    assert 'representative source' in hint.summary
    assert 'Preferred' not in hint.tooltip()


def test_clean_nonrepresentative_is_good(tmp_path):
    session, ride = _session(tmp_path)
    base = 'Ride_repeat01_timing_20261114T120000_1234abcd'
    path = ride / f'{base}.csv'
    path.write_text('dummy', encoding='utf-8')
    _write_csv(session / 'report' / 'runs.csv', [_run_row(base)])
    _write_csv(session / 'report' / 'groups.csv', [{
        'reference': 'Other_repeat02_timing_20261114T120100_abcdef12',
        'complete_measured_runs': '5', 'note': ''
    }])
    assert suitability_hint(path).rating == 'Good'


def test_soft_timing_issue_is_caution_even_if_reference(tmp_path):
    session, ride = _session(tmp_path)
    base = 'Ride_repeat03_timing_20261114T120200_1234abcd'
    path = ride / f'{base}.csv'
    path.write_text('dummy', encoding='utf-8')
    _write_csv(session / 'report' / 'runs.csv', [_run_row(base, repeat=3, gaps=1)])
    _write_csv(session / 'report' / 'groups.csv', [{
        'reference': base, 'complete_measured_runs': '5', 'note': ''
    }])
    hint = suitability_hint(path)
    assert hint.rating == 'Caution'
    assert 'gap' in hint.tooltip().lower()


def test_hard_fault_is_avoid(tmp_path):
    session, ride = _session(tmp_path)
    base = 'Ride_repeat04_timing_20261114T120300_1234abcd'
    path = ride / f'{base}.csv'
    path.write_text('dummy', encoding='utf-8')
    _write_csv(session / 'report' / 'runs.csv', [
        _run_row(base, repeat=4, eligible=False, backwards=1)
    ])
    _write_csv(session / 'report' / 'groups.csv', [{
        'reference': base, 'complete_measured_runs': '5', 'note': ''
    }])
    hint = suitability_hint(path)
    assert hint.rating == 'Avoid'
    assert 'backwards timestamp' in hint.tooltip().lower()


def test_singleton_reference_is_good_not_suggested(tmp_path):
    session, ride = _session(tmp_path)
    base = 'Ride_repeat01_timing_20261114T120000_1234abcd'
    path = ride / f'{base}.csv'
    path.write_text('dummy', encoding='utf-8')
    _write_csv(session / 'report' / 'runs.csv', [_run_row(base)])
    _write_csv(session / 'report' / 'groups.csv', [{
        'reference': base, 'complete_measured_runs': '1',
        'note': 'only one comparable run; repeatability unknown'
    }])
    hint = suitability_hint(path)
    assert hint.rating == 'Good'
    assert 'repeatability unknown' in hint.tooltip()


def test_trial_metadata_fallback(tmp_path):
    session, ride = _session(tmp_path)
    base = 'Ride_repeat05_timing_20261114T120400_1234abcd'
    path = ride / f'{base}.csv'
    path.write_text('dummy', encoding='utf-8')
    trial = path.with_name(path.stem + '_trial.json')
    trial.write_text(json.dumps({
        'status': 'complete', 'repeat': 5, 'quality_issues': [],
        'error': '', 'transport_overflow': 0,
    }), encoding='utf-8')
    hint = suitability_hint(path)
    assert hint.rating == 'Good'
    assert 'trial metadata only' in hint.tooltip()


def test_missing_metadata_is_unrated(tmp_path):
    session, ride = _session(tmp_path)
    path = ride / 'Ride_repeat01_timing_20261114T120000_1234abcd.csv'
    path.write_text('dummy', encoding='utf-8')
    assert suitability_hint(path).rating == 'Unrated'
