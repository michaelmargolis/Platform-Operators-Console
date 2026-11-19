#dynamic_ride_studies.py ver 5
"""Qt-free discovery/ranking for dynamic-source sessions and recordings.

Terminology used by Ride Analyzer:
- session: one ``session_*`` directory created by dynamic-source capture;
- recording: one completed ride repeat inside that session;
- generated profile set: optimiser output under ``studies/nl2/dynamic_profiles``.

Individual recordings are available for replay only. Profile generation consumes
all compatible recordings for the selected ride and chooses reference/fitting/
held-out runs automatically.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path

from sims.nolimits2 import ride_config
from .dynamic_source_rating import suitability_hint
from .video_sync import is_dynamic_source_recording_csv

RATING_PRIORITY = {'Suggested': 0, 'Good': 1, 'Caution': 2, 'Unrated': 3, 'Avoid': 4}


def _compact(value):
    return ''.join(ch for ch in str(value).casefold() if ch.isalnum())


def ride_label(park_key):
    park = ride_config.PARKS.get(str(park_key), {})
    return park.get('name', str(park_key))


def _ride_aliases():
    result = {}
    for key, park in ride_config.PARKS.items():
        values = (key, park.get('name', ''), Path(park.get('path', '')).stem)
        for value in values:
            token = _compact(value)
            if token:
                result[token] = key
    return result


_RIDE_ALIASES = _ride_aliases()


def session_recordings(session):
    """Return completed primary recording CSVs from one session directory."""
    session = Path(session)
    return sorted((p for p in session.rglob('*.csv')
                   if is_dynamic_source_recording_csv(p.name) and p.is_file()),
                  key=lambda p: p.name.casefold())


def recording_ride(path, session=None):
    """Return the configured park key for a primary recording, if known.

    Dynamic-source recordings normally live in ``session_<id>/<park>/``. That
    directory is the strongest signal and keeps discovery independent of
    filename punctuation. Filename matching remains as a fallback for older or
    moved recordings.
    """
    path = Path(path)
    if session is not None:
        try:
            relative = path.resolve().relative_to(Path(session).resolve())
            if relative.parts:
                key = relative.parts[0].casefold()
                if key in ride_config.PARKS:
                    return key
        except (OSError, ValueError):
            pass

    for parent in (path.parent, *path.parents):
        key = parent.name.casefold()
        if key in ride_config.PARKS:
            return key
        if parent.name.startswith('session_'):
            break

    name = _compact(path.name)
    for token, key in sorted(_RIDE_ALIASES.items(), key=lambda item: -len(item[0])):
        if name.startswith(token):
            return key
    return None


def _session_declared_rides(session):
    """Read the capture order when available; absence/corruption is non-fatal."""
    path = Path(session) / 'session.json'
    try:
        payload = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError, TypeError):
        return ()
    values = payload.get('selected_parks', ())
    if not isinstance(values, (list, tuple)):
        return ()
    return tuple(str(value).casefold() for value in values
                 if str(value).casefold() in ride_config.PARKS)


def available_rides(session):
    """Configured, Analyzer-visible rides that actually have recordings."""
    if session is None:
        return ()
    present = {recording_ride(path, session.path) for path in session.recordings}
    present.discard(None)
    present.difference_update(ride_config.ANALYZER_HIDDEN_PARKS)

    ordered = []
    for key in _session_declared_rides(session.path):
        if key in present and key not in ordered:
            ordered.append(key)
    for key in ride_config.PARKS:
        if key in present and key not in ordered:
            ordered.append(key)
    return tuple(ordered)


@dataclass(frozen=True)
class SessionSummary:
    path: Path
    counts: dict
    recordings: tuple[Path, ...]

    @property
    def quality_sort_key(self):
        # Preserve the established quality-first list; session name is the
        # deterministic final tie-breaker.
        return (self.counts['Avoid'], self.counts['Caution'],
                -self.counts['Suggested'], -self.counts['Good'],
                -len(self.recordings), self.path.name.casefold())

    @property
    def description(self):
        c = self.counts
        return (f'{self.path.name}  |  {c["Suggested"]} Suggested, {c["Good"]} Good, '
                f'{c["Caution"]} Caution, {c["Avoid"]} Avoid')

    @property
    def rides(self):
        return available_rides(self)

    @property
    def tooltip(self):
        rides = ', '.join(ride_label(key) for key in self.rides) or 'No recognised ride recordings'
        return (f'{self.path.name}\n'
                f'Rides: {rides}\n'
                f'Primary recordings: {len(self.recordings)}\n'
                f'Folder: {self.path}')


def inspect_session(folder):
    folder = Path(folder).resolve()
    recordings = tuple(session_recordings(folder))
    if not recordings:
        return None
    counts = {name: 0 for name in RATING_PRIORITY}
    for recording in recordings:
        counts[suitability_hint(recording).rating] += 1
    return SessionSummary(folder, counts, recordings)


def find_sessions(directory):
    """List direct ``session_*`` children of one source-session folder.

    The supplied directory is deliberately treated as the parent folder, not as
    a recursive search root. This prevents a similarly named session elsewhere
    in the repository from appearing in the selector.
    """
    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        return ()
    folders = [p for p in root.iterdir()
               if p.is_dir() and p.name.startswith('session_')]
    found = [inspect_session(folder) for folder in folders]
    return tuple(sorted((session for session in found if session),
                        key=lambda session: session.quality_sort_key))


def rated_recordings(session, ride=None):
    rows = []
    for path in session.recordings:
        if ride and recording_ride(path, session.path) != ride:
            continue
        hint = suitability_hint(path)
        rows.append((RATING_PRIORITY.get(hint.rating, 3), path.name.casefold(), path, hint))
    return tuple((path, hint) for _, _, path, hint in sorted(rows))


# Compatibility aliases for older tests/tools that imported the pre-v212 names.
StudySummary = SessionSummary
study_recordings = session_recordings
inspect_study = inspect_session
find_studies = find_sessions

# v0.2.28 wizard status helpers -------------------------------------------------
_WORKFLOW_STATUS_RANK = {'partial': 0, 'generated': 1, 'reviewed': 2, 'exported': 3}


def _run_settings_match_session(payload, session_path):
    inputs = payload.get('inputs', ()) if isinstance(payload, dict) else ()
    if isinstance(inputs, str):
        inputs = (inputs,)
    if not isinstance(inputs, (list, tuple)):
        return False
    session_path = Path(session_path).expanduser().resolve()
    for value in inputs:
        candidate = Path(str(value)).expanduser()
        try:
            if candidate.resolve() == session_path:
                return True
        except OSError:
            pass
        if candidate.name == session_path.name:
            return True
    return False


def _setting_tokens(value):
    if isinstance(value, str):
        return tuple(part.strip().casefold() for part in value.split(',') if part.strip())
    if isinstance(value, (list, tuple, set)):
        return tuple(str(part).strip().casefold() for part in value if str(part).strip())
    return ()


def _set_workflow_status(result, ride, platform, status):
    if ride not in ride_config.PARKS or platform not in ('chair_v3', 'platform_v2'):
        return
    current = result.setdefault(ride, {}).get(platform)
    if current is None or _WORKFLOW_STATUS_RANK[status] > _WORKFLOW_STATUS_RANK[current]:
        result[ride][platform] = status


def _workflow_entry_is_better(candidate, current):
    if current is None:
        return True
    candidate_rank = _WORKFLOW_STATUS_RANK.get(candidate.get('status'), -1)
    current_rank = _WORKFLOW_STATUS_RANK.get(current.get('status'), -1)
    if candidate_rank != current_rank:
        return candidate_rank > current_rank
    return float(candidate.get('generated_at', 0.0)) > float(current.get('generated_at', 0.0))


def _set_workflow_detail(result, ride, platform, entry):
    if ride not in ride_config.PARKS or platform not in ('chair_v3', 'platform_v2'):
        return
    current = result.setdefault(ride, {}).get(platform)
    if _workflow_entry_is_better(entry, current):
        result[ride][platform] = entry


def session_processing_details(session, generated_root):
    """Return retained processing detail for one source session.

    The mapping is ``{ride: {platform: detail}}``. ``detail`` includes the
    highest completed workflow state found for that source/ride/platform plus
    the profile-set directory and generation timestamp. A newer result wins
    when two results are at the same workflow stage. This is read-only
    discovery used to make prior work visible on the Source page.
    """
    session_path = Path(session.path if hasattr(session, 'path') else session).resolve()
    root = Path(generated_root).expanduser().resolve()
    result = {}
    if not root.is_dir():
        return result

    for settings_path in root.rglob('run_settings.json'):
        try:
            payload = json.loads(settings_path.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError, TypeError):
            continue
        if not _run_settings_match_session(payload, session_path):
            continue
        run_dir = settings_path.parent
        rides = _setting_tokens(payload.get('rides', ()))
        platforms = _setting_tokens(payload.get('platforms', payload.get('platform', ())))
        if 'both' in platforms:
            platforms = ('chair_v3', 'platform_v2')
        rides = tuple(ride for ride in rides if ride in ride_config.PARKS)
        platforms = tuple(p for p in platforms if p in ('chair_v3', 'platform_v2'))

        report_path = run_dir / 'report.json'
        timestamp_path = report_path if report_path.is_file() else settings_path
        try:
            generated_at = timestamp_path.stat().st_mtime
        except OSError:
            generated_at = 0.0

        if not report_path.is_file():
            for ride in rides:
                for platform in platforms:
                    _set_workflow_detail(result, ride, platform, {
                        'status': 'partial',
                        'generated_at': generated_at,
                        'run_dir': str(run_dir),
                        'profile_set': run_dir.name,
                        'profile_path': '',
                        'target': '',
                    })
            continue

        state_path = run_dir / 'analyzer_dynamic_workflow_state.json'
        try:
            state = json.loads(state_path.read_text(encoding='utf-8'))
        except (OSError, ValueError, TypeError):
            state = {}
        reviewed = {str(value) for value in state.get('reviewed', ())}
        prepared = state.get('prepared', {}) if isinstance(state.get('prepared', {}), dict) else {}

        for profile_path in run_dir.glob('*/*/*/profile.json'):
            try:
                profile_data = json.loads(profile_path.read_text(encoding='utf-8-sig'))
                identity = profile_data.get('identity', {})
                ride = str(identity.get('park', '')).casefold()
                platform = str(identity.get('platform', '')).casefold()
                relative = profile_path.relative_to(run_dir).as_posix()
            except (OSError, ValueError, TypeError):
                continue
            status = 'generated'
            if relative in reviewed:
                status = 'reviewed'
            package = prepared.get(relative)
            if package and Path(str(package)).is_file():
                status = 'exported'
            _set_workflow_detail(result, ride, platform, {
                'status': status,
                'generated_at': generated_at,
                'run_dir': str(run_dir),
                'profile_set': run_dir.name,
                'profile_path': str(profile_path),
                'target': profile_path.parent.name,
            })
    return result


def session_processing_status(session, generated_root):
    """Return ``{ride: {platform: status}}`` for compatibility callers."""
    details = session_processing_details(session, generated_root)
    return {ride: {platform: entry['status'] for platform, entry in rows.items()}
            for ride, rows in details.items()}


def _display_timestamp(value):
    try:
        return datetime.fromtimestamp(float(value)).strftime('%d %b %H:%M')
    except (OSError, OverflowError, TypeError, ValueError):
        return ''


def ride_processing_display(ride, platform_details):
    """Return combo-box text/tooltip showing retained work for one ride."""
    short_platform = {'chair_v3': 'Chair', 'platform_v2': 'Ground'}
    title_status = {
        'partial': 'Partial', 'generated': 'Generated',
        'reviewed': 'Reviewed', 'exported': 'Exported',
    }
    bits = []
    lines = []
    for platform in ('chair_v3', 'platform_v2'):
        entry = (platform_details or {}).get(platform)
        if not entry:
            continue
        title = title_status.get(entry.get('status'), str(entry.get('status', '')).title())
        stamp = _display_timestamp(entry.get('generated_at'))
        bits.append(f'{short_platform[platform]}: {title}')
        detail = f'{short_platform[platform]}: {title}'
        if stamp:
            detail += f' ({stamp})'
        if entry.get('profile_set'):
            detail += f' - {entry["profile_set"]}'
        lines.append(detail)
    label = ride_label(ride)
    text = label + ('  |  ' + '; '.join(bits) if bits else '')
    tooltip = label + ('\nPrevious profile work:\n  ' + '\n  '.join(lines) if lines else '\nNo previous profile generation found.')
    return text, tooltip


def platform_processing_display(entry):
    """Return Source-page status text and tooltip for one platform entry."""
    if not entry:
        return 'Not generated', 'No previous profile generation found for this source session and ride.'
    title_status = {
        'partial': 'Partial generation', 'generated': 'Generated',
        'reviewed': 'Reviewed', 'exported': 'Exported',
    }
    title = title_status.get(entry.get('status'), str(entry.get('status', '')).title())
    stamp = _display_timestamp(entry.get('generated_at'))
    text = title + (f' · {stamp}' if stamp else '')
    if entry.get('profile_set'):
        text += f' · {entry["profile_set"]}'
    tooltip = text
    if entry.get('run_dir'):
        tooltip += f'\nProfile set: {entry["run_dir"]}'
    return text, tooltip


def session_status_display(session, generated_root):
    """Return compact combo text and detailed tooltip for a session."""
    status = session_processing_status(session, generated_root)
    ride_lines = []
    generated = reviewed = exported = partial = 0
    short_platform = {'chair_v3': 'Chair', 'platform_v2': 'Ground'}
    title_status = {
        'partial': 'Partial', 'generated': 'Generated',
        'reviewed': 'Reviewed', 'exported': 'Exported',
    }
    for ride in session.rides:
        rows = []
        for platform in ('chair_v3', 'platform_v2'):
            value = status.get(ride, {}).get(platform)
            if value:
                rows.append(f'{short_platform[platform]}: {title_status[value]}')
                if value == 'partial':
                    partial += 1
                elif value == 'generated':
                    generated += 1
                elif value == 'reviewed':
                    reviewed += 1
                elif value == 'exported':
                    exported += 1
        ride_lines.append(
            f'{ride_label(ride)} - ' + ('; '.join(rows) if rows else 'Not processed'))

    description = f'{session.path.name}  |  {len(session.rides)} rides'
    progress_bits = []
    if partial:
        progress_bits.append(f'{partial} partial')
    if generated:
        progress_bits.append(f'{generated} generated')
    if reviewed:
        progress_bits.append(f'{reviewed} reviewed')
    if exported:
        progress_bits.append(f'{exported} exported')
    if progress_bits:
        description += '  |  ' + ', '.join(progress_bits)

    c = session.counts
    tooltip = (
        session.path.name + '\n' +
        'Rides / profile state:\n  ' + '\n  '.join(ride_lines) + '\n' +
        f'Primary recordings: {len(session.recordings)}\n' +
        f'Quality: {c["Suggested"]} Suggested, {c["Good"]} Good, '
        f'{c["Caution"]} Caution, {c["Avoid"]} Avoid\n' +
        f'Folder: {session.path}'
    )
    return description, tooltip
