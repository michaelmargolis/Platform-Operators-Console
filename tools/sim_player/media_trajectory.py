"""Read ride media trajectories without invoking filters, kinematics or hardware.

All positions are NL2 metres and quaternions x,y,z,w. The source CSV clock is
preserved. Phase zero is first sustained movement, never load/Start time.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import csv
import hashlib
import json
import math
import re
import numpy as np


class MediaError(ValueError):
    pass


class MediaCancelled(Exception):
    pass


def check_cancel(cancel):
    if cancel and cancel():
        raise MediaCancelled('Reference video operation cancelled')


def file_sha256(path, cancel=None):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            check_cancel(cancel)
            digest.update(block)
    return digest.hexdigest()


def load_json(path):
    path = Path(path)
    if path.stat().st_size > 10_000_000:
        raise MediaError('Oversized media metadata: ' + str(path))
    value = json.loads(path.read_text(encoding='utf-8-sig'),
                      parse_constant=lambda x: (_ for _ in ()).throw(MediaError('Nonfinite JSON value')))
    if not isinstance(value, dict):
        raise MediaError('Metadata must be a JSON object: ' + str(path))
    return value


def safe_key(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,79}', value):
        raise MediaError('Invalid ride key; use the configured key, for example blackbird or mdxlogo')
    return value


def key_for_name(name):
    from sims.nolimits2 import ride_config
    normal = lambda v: re.sub(r'[^a-z0-9]', '', str(v).casefold())
    wanted = normal(name)
    for key, info in ride_config.PARKS.items():
        if wanted in (normal(key), normal(info['name'])):
            return key
    raise MediaError('Unknown ride name: ' + str(name))


def recording_key(recording):
    dynamic = getattr(recording, 'dynamic_profile', None)
    if dynamic:
        return safe_key(dynamic['profile']['identity']['park'])
    variant = getattr(recording, 'variant_snapshot', None) or {}
    return safe_key(variant['park']) if variant.get('park') else key_for_name(recording.ride)


@dataclass
class Trajectory:
    time: np.ndarray
    xyz: np.ndarray
    quat: np.ndarray
    speed: np.ndarray
    frame: np.ndarray
    first_motion: float
    identity: dict = field(default_factory=dict)
    events: tuple = ()
    warnings: tuple = ()
    source_hash: str = ''

    @property
    def phase(self):
        return self.time - self.first_motion

    def reference(self):
        from tools.nl2_sync.matcher import Reference
        return Reference(self.phase, self.xyz, self.quat, self.speed)

    def save(self, path):
        np.savez_compressed(path, time=self.time, xyz=self.xyz, quat=self.quat,
                            speed=self.speed, frame=self.frame,
                            first_motion=np.array([self.first_motion]))

    @classmethod
    def load(cls, path, identity, events=(), warnings=(), source_hash=''):
        if Path(path).stat().st_size > 100_000_000:
            raise MediaError('Oversized trajectory')
        with np.load(path, allow_pickle=False) as d:
            result = cls(d['time'], d['xyz'], d['quat'], d['speed'], d['frame'],
                         float(d['first_motion'][0]), identity, tuple(events),
                         tuple(warnings), source_hash)
        result.reference()  # validates shape, finiteness, clock and quaternion
        if result.frame.shape != result.time.shape or not math.isfinite(result.first_motion):
            raise MediaError('Invalid trajectory frame or origin')
        return result


def detect_first_motion(time, speed):
    start = None
    count = 0
    for i, (t, v) in enumerate(zip(time, speed)):
        if abs(v) >= .1:
            if start is None or (i and t-time[i-1] > .5):
                start, count = i, 1
            else:
                count += 1
            if count >= 3:
                return float(time[start])
        else:
            start, count = None, 0
    raise MediaError('No first sustained movement found. Use the full video-matched ride capture, not a stationary fragment.')


def read_trajectory(csv_path, *, park_key=None, cancel=None):
    """Read ordinary capture CSVs (including static study exports).

    Timing-study unsuffixed CSVs are supported. Select the original CSV that
    was recorded with the video, not its _timing or _dynamic_diagnostics file.
    """
    path = Path(csv_path).resolve()
    if not path.is_file() or path.stat().st_size > 200_000_000:
        raise MediaError('Missing or oversized telemetry CSV: ' + str(path))
    if path.name.endswith(('_timing.csv', '_state_events.csv', '_dynamic_diagnostics.csv')):
        raise MediaError('Select the unsuffixed ride telemetry CSV recorded with the video')
    required = {'elapsed_s', 'speed', 'pos_x', 'pos_y', 'pos_z',
                'quat_x', 'quat_y', 'quat_z', 'quat_w'}
    rows = []
    names = set()
    view_values = set()
    skipped = 0
    last_clock = None
    last_frame = None
    with path.open(newline='', encoding='utf-8-sig') as stream:
        reader = csv.DictReader(stream)
        if not required.issubset(reader.fieldnames or []):
            raise MediaError('Raw XYZ/quaternion telemetry is required. Missing: ' +
                             ', '.join(sorted(required-set(reader.fieldnames or []))))
        for row_index, row in enumerate(reader):
            if row_index % 100 == 0:
                check_cancel(cancel)
            if row_index >= 250_000:
                raise MediaError('Telemetry exceeds 250,000 samples')
            if row.get('ride'):
                names.add(row['ride'])
            try:
                t = float(row['elapsed_s'])
                vector = [float(row[k]) for k in ('pos_x','pos_y','pos_z','quat_x','quat_y','quat_z','quat_w','speed')]
                frame = int(row.get('nl2_frame') or row.get('frame') or row_index)
                if not math.isfinite(t) or not all(map(math.isfinite, vector)):
                    raise ValueError('nonfinite sample')
            except (ValueError, TypeError) as exc:
                raise MediaError(f'Invalid telemetry at line {row_index+2}: {exc}') from exc
            if last_clock is not None and t < last_clock:
                raise MediaError('Telemetry clock runs backwards; cannot build video mapping')
            if last_frame is not None and frame < last_frame:
                raise MediaError('NL2 frame counter resets inside capture; use a single ride run')
            equal = last_clock is not None and t == last_clock
            repeated = last_frame is not None and frame == last_frame
            last_clock, last_frame = t, frame
            if row.get('valid', '1').strip().lower() in ('0','false') or equal or repeated:
                skipped += 1
                continue
            if row.get('state'):
                state = int(row['state'], 0) if str(row['state']).lower().startswith('0x') else int(row['state'])
                if state & 4:
                    raise MediaError('Capture contains an NL2 pause; use an uninterrupted reference run')
            if all(row.get(k) not in (None, '') for k in ('coaster_index','train','car','seat','view_mode')):
                view_values.add(tuple(int(row[k]) for k in ('coaster_index','train','car','seat','view_mode')))
            rows.append([t, *vector, frame])
    if len(rows) < 4:
        raise MediaError('Too few valid telemetry samples')
    if len(names) > 1 or len(view_values) > 1:
        raise MediaError('Capture changes ride or viewed train/seat inside the recording')
    ride = next(iter(names), None)
    actual_key = key_for_name(ride) if ride else None
    if park_key and actual_key and actual_key != park_key:
        raise MediaError('Selected ride does not match the telemetry ride')
    identity = {'park': safe_key(park_key or actual_key or ''), 'ride': ride or park_key}
    trial_path = path.with_name(path.stem + '_trial.json')
    warnings = []
    trial = {}
    if trial_path.is_file():
        trial = load_json(trial_path)
        if trial.get('status') != 'complete':
            raise MediaError('Timing-study run is not complete; choose a completed run')
        expected = (trial.get('file_sha256') or {}).get('capture')
        if expected and expected != file_sha256(path, cancel):
            raise MediaError('Capture no longer matches its trial checksum')
        if trial.get('park_key') and trial['park_key'] != identity['park']:
            raise MediaError('Trial metadata identifies another park')
        for name in ('park_sha256','view_identity','departure_station','nl2_version'):
            if trial.get(name) is not None:
                identity[name] = trial[name]
    elif view_values:
        identity['view_identity'] = list(next(iter(view_values)))
    else:
        warnings.append('Legacy capture: park hash/train-seat metadata unavailable; route matching and your reference selection are required.')
    if skipped:
        warnings.append(f'{skipped} repeated-time/frame or invalid samples omitted from media trajectory only.')
    a = np.asarray(rows, dtype=float)
    first_motion = detect_first_motion(a[:,0], a[:,8])
    # The timing-study movement mark uses the same absolute elapsed clock as
    # the unsuffixed capture. Prefer the recorded distinct-frame detection.
    mark = (trial.get('marks') or {}).get('first_motion')
    if mark is not None and a[0,0]-.5 <= float(mark) <= a[-1,0] and abs(float(mark)-first_motion)<1.:
        first_motion = float(mark)
    gaps = np.diff(a[:,0])
    if np.any(gaps > .25):
        warnings.append(f'{int(np.sum(gaps>.25))} telemetry gaps over 250 ms; media is unavailable across those gaps.')
    # Reuse player event clock conversion: event timestamps are NOT assumed
    # to have the same zero as the capture CSV.
    from .recording import _load_events, _read_sync
    sync_path = path.with_name(path.stem+'_video_sync.json')
    sync, sync_warning = _read_sync(sync_path)
    events, event_warning = _load_events(path.with_name(path.stem+'_state_events.csv'),
                                        sync, path, float(a[-1,0]))
    event_data = tuple({'seconds':e.seconds,'name':e.name,'details':e.details} for e in events)
    if sync_warning: warnings.append(sync_warning)
    if event_warning: warnings.append(event_warning)
    result = Trajectory(a[:,0], a[:,1:4], a[:,4:8], a[:,8], a[:,9].astype(np.int64),
                        first_motion, identity, event_data, tuple(warnings), file_sha256(path,cancel))
    result.reference()
    return result


def compare_identity(canonical, target):
    """Media may be desktop footage for a VR run. VR and platform are not gates."""
    if canonical.get('park') != target.get('park'):
        raise MediaError('Reference belongs to a different ride')
    for key in ('park_sha256', 'view_identity', 'departure_station'):
        a, b = canonical.get(key), target.get(key)
        if a is not None and b is not None and a != b:
            raise MediaError('New video capture required: reference and selected recording differ in '+key)
    return (() if canonical.get('nl2_version') == target.get('nl2_version') else
            ('NL2 version differs or is unknown; alignment is checked from the recorded route.',))
