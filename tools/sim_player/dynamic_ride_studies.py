#dynamic_ride_studies.py ver 1
"""Qt-free Dynamic Ride Workspace discovery and source ranking.

A STUDY is selected for profile generation. Individual CSVs are available for
replay only; the optimizer chooses reference and independent holdout itself.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from .dynamic_source_rating import suitability_hint
from .video_sync import is_dynamic_source_recording_csv

RATING_PRIORITY = {'Suggested': 0, 'Good': 1, 'Caution': 2, 'Unrated': 3, 'Avoid': 4}
SUPPORTED_RIDES = ('blackbird', 'mdxlogo')


def study_recordings(session):
    session = Path(session)
    return sorted((p for p in session.rglob('*.csv')
                   if is_dynamic_source_recording_csv(p.name) and p.is_file()),
                  key=lambda p: p.name.casefold())


def recording_ride(path):
    name = Path(path).name.casefold().replace('-', '').replace('_', '')
    if name.startswith('blackbird'):
        return 'blackbird'
    if name.startswith(('mdxlogocoaster', 'mdxlogo')):
        return 'mdxlogo'
    return None


@dataclass(frozen=True)
class StudySummary:
    path: Path
    counts: dict
    recordings: tuple[Path, ...]

    @property
    def quality_sort_key(self):
        # Clean + well-supported studies first; newest is a tie-breaker.
        return (self.counts['Avoid'], self.counts['Caution'],
                -self.counts['Suggested'], -self.counts['Good'],
                -len(self.recordings), self.path.name.casefold())

    @property
    def description(self):
        c = self.counts
        return (f'{self.path.name}  |  {c["Suggested"]} Suggested, {c["Good"]} Good, '
                f'{c["Caution"]} Caution, {c["Avoid"]} Avoid')


def inspect_study(folder):
    folder = Path(folder).resolve()
    recordings = tuple(study_recordings(folder))
    if not recordings:
        return None
    counts = {name: 0 for name in RATING_PRIORITY}
    for recording in recordings:
        counts[suitability_hint(recording).rating] += 1
    return StudySummary(folder, counts, recordings)


def find_studies(directory):
    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        return ()
    folders = [root] if root.name.startswith('session_') else [p for p in root.iterdir()
                                                                if p.is_dir() and p.name.startswith('session_')]
    found = [inspect_study(folder) for folder in folders]
    return tuple(sorted((s for s in found if s), key=lambda s: s.quality_sort_key))


def rated_recordings(study, ride=None):
    rows = []
    for p in study.recordings:
        if ride and recording_ride(p) != ride:
            continue
        hint = suitability_hint(p)
        rows.append((RATING_PRIORITY.get(hint.rating, 3), p.name.casefold(), p, hint))
    return tuple((p,hint) for _,_,p,hint in sorted(rows))
