# canonical_media.py ver 1
"""Player-owned, portable ride reference video library.

Each import is immutable; only the per-ride reference.json pointer/calibration
is replaced atomically. Existing videos/captures are copied, never moved/deleted.
No POC catalogue, motion code or optimiser bundle is edited.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from copy import deepcopy
import json
import math
import os
import shutil
import uuid
import numpy as np
from .media_trajectory import (MediaError, Trajectory, check_cancel, file_sha256,
                                load_json, read_trajectory, safe_key)

SCHEMA = 'sim_player.canonical-media/1'
ROOT = Path(__file__).resolve().parent / 'canonical_rides'
VIDEO_SUFFIXES = {'.mp4','.mkv','.mov','.avi','.webm'}


def companion_telemetry(video_path):
    """Return the required same-basename telemetry CSV for a reference video.

    Canonical media registration deliberately uses a deterministic file pairing:
    ``ride.mp4`` must have ``ride.csv`` in the same directory.  This keeps the
    setup dialog from asking the operator to manually pair two unrelated files.
    """
    video_path = Path(video_path)
    if not video_path.is_file() or video_path.suffix.lower() not in VIDEO_SUFFIXES:
        raise MediaError('Select an existing MP4, MKV, MOV, AVI or WebM video')
    csv_path = video_path.with_suffix('.csv')
    if not csv_path.is_file():
        raise MediaError(
            f'No matching telemetry CSV was found for {video_path.name}. '
            f'Expected {csv_path.name} in the same folder.'
        )
    return csv_path.resolve()


def atomic_json(path, data):
    path = Path(path)
    temp = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temp.open('x', encoding='utf-8') as f:
            json.dump(data, f, indent=2, allow_nan=False)
            f.write('\n')
        os.replace(temp, path)
    finally:
        if temp.exists(): temp.unlink()


def checked_anchors(anchors):
    try:
        a = np.asarray(anchors, dtype=float)
    except (ValueError, TypeError) as exc:
        raise MediaError('Calibration anchors must be numeric [telemetry seconds, video seconds]') from exc
    if a.ndim != 2 or a.shape[1] != 2 or not 1 <= len(a) <= 100 or not np.isfinite(a).all():
        raise MediaError('Use 1 to 100 finite telemetry/video timing anchors')
    if len(a) > 1 and (np.any(np.diff(a[:,0])<=0) or np.any(np.diff(a[:,1])<=0)):
        raise MediaError('Telemetry and video anchors must both increase; no reversed or repeated time')
    if len(a) > 1:
        rate = np.diff(a[:,1])/np.diff(a[:,0])
        if np.any((rate < .25)|(rate > 4.)):
            raise MediaError('Anchor speed ratio outside 0.25..4; check the chosen times')
    return a


def calibrated_time(capture_seconds, anchors):
    """Piecewise linear original-capture clock to video PTS; one anchor = offset.

    Beyond first/last anchor continue at unit rate, not an extrapolated extreme
    warp. Availability is separately restricted to recorded telemetry coverage.
    """
    a = checked_anchors(anchors)
    value = np.asarray(capture_seconds, dtype=float)
    result = np.interp(value, a[:,0], a[:,1])
    result = np.where(value<a[0,0], value+a[0,1]-a[0,0], result)
    result = np.where(value>a[-1,0], value+a[-1,1]-a[-1,0], result)
    return float(result) if result.ndim == 0 else result


def media_path(base, relative):
    if not isinstance(relative, str) or '\\' in relative or ':' in relative:
        raise MediaError('Invalid canonical asset path')
    p = Path(relative)
    if p.is_absolute() or '..' in p.parts:
        raise MediaError('Canonical asset path must remain inside its ride folder')
    base = Path(base).resolve()
    result = (base/p).resolve()
    if base not in result.parents or not result.is_file():
        raise MediaError('Missing canonical asset: '+str(base/p))
    return result


@dataclass
class CanonicalMedia:
    manifest_path: Path
    data: dict
    trajectory: Trajectory
    video_path: Path

    @property
    def key(self): return self.data['park']
    @property
    def token(self):
        return (str(self.manifest_path), json.dumps(self.data, sort_keys=True))
    @property
    def anchors(self): return self.data['calibration']['anchors']
    def video_time(self, phase_s):
        return calibrated_time(np.asarray(phase_s)+self.trajectory.first_motion,self.anchors)


def load_media(park_key, root=ROOT):
    key = safe_key(park_key)
    path = Path(root)/key/'reference.json'
    if not path.is_file(): return None
    d = load_json(path)
    if d.get('schema') != SCHEMA or d.get('park') != key:
        raise MediaError('Invalid canonical reference metadata: '+str(path))
    checked_anchors(d.get('calibration',{}).get('anchors'))
    video = media_path(path.parent, d['video']['path'])
    if video.stat().st_size != d['video']['size_bytes']:
        raise MediaError('Canonical video size has changed; reimport the video/telemetry pair')
    trajectory_path = media_path(path.parent,d['trajectory']['path'])
    if file_sha256(trajectory_path) != d['trajectory']['sha256']:
        raise MediaError('Canonical trajectory checksum differs; reimport the original pair')
    trace = Trajectory.load(trajectory_path,d['identity'],d.get('events',()),
                            d.get('warnings',()),d.get('source_csv_sha256',''))
    return CanonicalMedia(path,d,trace,video)


def initial_calibration(csv_path):
    from .recording import read_saved_calibration, _read_sync, _video_offset
    path=Path(csv_path)
    calibrated=read_saved_calibration(path)
    if calibrated is not None:
        return [[0., calibrated]], True, 'Existing saved Ride Analyzer calibration'
    manifest,_=_read_sync(path.with_name(path.stem+'_video_sync.json'))
    offset, reason = _video_offset(manifest)
    return [[0.,offset]], False, reason


def _copy_hash(source, destination, cancel, progress):
    import hashlib
    digest=hashlib.sha256()
    size=Path(source).stat().st_size
    written=0
    with Path(source).open('rb') as src, Path(destination).open('xb') as dst:
        for block in iter(lambda: src.read(1024*1024),b''):
            check_cancel(cancel)
            dst.write(block);digest.update(block);written+=len(block)
            if progress and written % (32*1024*1024)==0:
                progress(f'Copying reference video: {written//(1024*1024)} / {size//(1024*1024)} MB')
    if written!=size or Path(source).stat().st_size!=size:
        raise MediaError('Source file changed during import')
    return digest.hexdigest(), written


def register_media(park_key, csv_path, video_path, *, root=ROOT, anchors=None,
                   calibrated=False, confirmed_pair=False, cancel=None, progress=None):
    """Import one known corresponding telemetry/video pair (worker-thread safe)."""
    key=safe_key(park_key)
    csv_path=Path(csv_path).resolve(); video_path=Path(video_path).resolve()
    if not confirmed_pair:
        raise MediaError('Confirm that the telemetry was recorded with this video and the intended train/seat')
    if not video_path.is_file() or video_path.suffix.lower() not in VIDEO_SUFFIXES:
        raise MediaError('Select an existing MP4, MKV, MOV, AVI or WebM video')
    if video_path.stat().st_size==0: raise MediaError('Video file is empty')
    if progress: progress('Reading the video\'s original telemetry and event clocks...')
    trace=read_trajectory(csv_path,park_key=key,cancel=cancel)
    if anchors is None:
        anchors, calibrated, _=initial_calibration(csv_path)
    anchors=checked_anchors(anchors).tolist()
    parent=Path(root)/key
    parent.mkdir(parents=True,exist_ok=True)
    revision='media_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'_'+uuid.uuid4().hex[:8]
    dest=parent/revision
    dest.mkdir()
    try:
        source=dest/'source';source.mkdir()
        for suffix in ('.csv','_state_events.csv','_video_sync.json','_sim_player_sync.json','_trial.json'):
            original=csv_path if suffix=='.csv' else csv_path.with_name(csv_path.stem+suffix)
            if original.is_file():
                check_cancel(cancel);shutil.copy2(original, source/original.name)
        if file_sha256(source/csv_path.name,cancel) != trace.source_hash:
            raise MediaError('Telemetry changed during import; retry after capture has finished')
        video_name='video'+video_path.suffix.lower()
        digest,size=_copy_hash(video_path,dest/video_name,cancel,progress)
        check_cancel(cancel)
        trace.save(dest/'trajectory.npz')
        data={
            'schema':SCHEMA, 'park':key, 'ride':trace.identity.get('ride',key),
            'created_utc':datetime.now(timezone.utc).isoformat(),
            'identity':trace.identity,'source_csv_sha256':trace.source_hash,
            'source_csv':revision+'/source/'+csv_path.name,
            'video':{'path':revision+'/'+video_name,'size_bytes':size,'sha256':digest,
                     'original_name':video_path.name},
            'trajectory':{'path':revision+'/trajectory.npz','sha256':file_sha256(dest/'trajectory.npz')},
            'calibration':{'anchors':anchors,'verified_by_user':bool(calibrated),
                           'note':'Video PTS versus original telemetry elapsed seconds'},
            'events':list(trace.events),'warnings':list(trace.warnings),
            'confirmed_video_telemetry_pair':True,
        }
        atomic_json(dest/'import_manifest.json',data)
        check_cancel(cancel)
        atomic_json(parent/'reference.json',data)
    except Exception:
        # Only this operation's unfinished staging import is removed.
        # Existing reference manifests and their assets remain untouched.
        shutil.rmtree(dest,ignore_errors=True)
        raise
    return load_media(key,root)


def update_calibration(media, anchors, *, calibrated=True):
    data=deepcopy(media.data)
    data['calibration']={'anchors':checked_anchors(anchors).tolist(),
                         'verified_by_user':bool(calibrated),
                         'note':'Video PTS versus original telemetry elapsed seconds'}
    current=load_json(media.manifest_path)
    if current != media.data:
        raise MediaError('Reference was changed in another window; reopen setup before saving')
    atomic_json(media.manifest_path,data)
    return load_media(media.key,media.manifest_path.parent.parent)
