#test_dynamic_study_workspace_v202.py ver 1
"""Study selection is automatic; the user is not required to pick a reference CSV."""
from pathlib import Path
from types import SimpleNamespace

from tools.sim_player import dynamic_ride_studies as studies


def test_study_quality_order_and_recording_sort(tmp_path,monkeypatch):
    root=tmp_path/'captures'
    for name in ('session_20261101T000000_abcd1234','session_20261102T000000_abcd1234'):
        (root/name).mkdir(parents=True)
    old=root/'session_20261101T000000_abcd1234'
    new=root/'session_20261102T000000_abcd1234'
    for f in ('Blackbird_repeat01_timing_20261101T000000_abcd1234.csv',
              'Blackbird_repeat02_timing_20261101T000100_abcd1234.csv'):
        (old/f).write_text('sample')
    for f in ('Blackbird_repeat01_timing_20261102T000000_abcd1234.csv',
              'Blackbird_repeat02_timing_20261102T000100_abcd1234.csv'):
        (new/f).write_text('sample')
    def rating(file):
        kind='Caution' if '20261102' in str(file) else ('Good' if 'repeat02' in file.name else 'Suggested')
        return SimpleNamespace(rating=kind,tooltip=lambda:kind)
    monkeypatch.setattr(studies,'suitability_hint',rating)
    found=studies.find_studies(root)
    assert found[0].path==old
    assert found[0].counts['Suggested']==1
    assert [hint.rating for _,hint in studies.rated_recordings(found[0],'blackbird')]==['Suggested','Good']
    assert studies.recording_ride('Mdx_Logo_Coaster_repeat01_timing_20261115T000000_abcd1234.csv')=='mdxlogo'
