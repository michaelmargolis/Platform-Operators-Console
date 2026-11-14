# dynamic_review.py ver 1
"""Profile-aware, offline review of Phase 1 output. No Qt or hardware I/O.

Saved-study mode consumes *post-washout* diagnostics, not telemetry. It never
filters or matches these values a second time. Kinematics is recalculated with
the frozen profile settings and checked against the exported lengths. Raw mode
uses the separate production replay adapter, with its explicitly null outputs.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from copy import deepcopy
import csv
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Callable

import numpy as np

from tools.nl2_sync.profile import AXES, DynamicProfile, load_reference
from tools.analyze_nl2_csv import make_backend, lengths_for_real
from .recording import PlaybackFrame, RideRecording, RideEvent
from .platform_selection import platform_metadata

ROOT = Path(__file__).resolve().parents[2]
MODES = ('dynamic', 'unity', 'fallback')
MODE_LABELS = {'dynamic': 'Dynamic profile', 'unity': 'Static 100% (same cue/midpoint)',
               'fallback': 'Static fallback (same cue/midpoint)'}
STATUSES = {'xyz','orientation','stationary_time','stale','waiting','lost','reacquiring',
            'time_fallback','lost_hold','lost_time','reacquired_xyz','reacquired_orientation',
            'reacquired_stationary_time','no_fresh_telemetry','disabled'}
REVIEW_SCHEMA = 'sim_player.dynamic-review/1'


class ReviewCancelled(Exception):
    pass


def check_cancel(cancel):
    if cancel is not None and cancel():
        raise ReviewCancelled('Profile review cancelled')


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _basename(name):
    return (isinstance(name, str) and bool(name) and name not in ('.', '..')
            and '/' not in name and '\\' not in name and ':' not in name)


@dataclass(frozen=True)
class ProfileChoice:
    path: Path
    park: str
    platform: str
    name: str
    target: str
    cue: str
    neutral_mm: float
    qualification: str
    captures: tuple[str, ...]
    content_hash: str
    run: str
    reference_trial: str

    @property
    def description(self):
        tag = 'Strict' if self.target == 'strict' else '5% allowance' if self.target == 'relaxed5' else self.target
        return f'{tag} / {self.name}'


def profile_choice(path):
    path = Path(path).resolve()
    profile = DynamicProfile.load(path)
    load_reference(path, profile)  # bound geometry-only trajectory checksum
    metadata = profile.metadata
    context = metadata['processing']
    base = context['base_variant']
    if context['platform'] != profile.identity['platform'] or base['park'] != profile.identity['park']:
        raise ValueError('Profile processing identity disagrees with its park/platform')
    if context.get('tick_s') != .05 or context.get('intensity_percent') != 100.:
        raise ValueError('This Ride Analyzer supports the Phase 1 20 Hz / 100% frozen processing format')
    captures = []
    for row in metadata.get('source_captures', []):
        name = row.get('trial')
        digest = row.get('timing_sha256', '')
        if not _basename(name) or len(digest) != 64:
            raise ValueError('Invalid source-capture identity in profile')
        captures.append(name)
    if not captures or len(captures) != len(set(captures)):
        raise ValueError('Profile has no unique source recordings')
    run = path.parent
    for parent in path.parents:
        if (parent / 'index.csv').is_file():
            run = parent
            break
    return ProfileChoice(path, profile.identity['park'], profile.identity['platform'],
        str(metadata.get('name', path.parent.name)), str(metadata.get('target', 'research')),
        str(base['angular_cue']), float(base['neutral_height_delta_mm']),
        str(metadata.get('qualification', 'unqualified')), tuple(captures),
        json.loads(path.read_text(encoding='utf-8'))['content_sha256'], run.name, str(metadata.get('reference_trial','')))


def discover_profiles(directory):
    """All candidates, not just one from each category. Do not follow symlink trees."""
    directory = Path(directory).expanduser().resolve()
    if not directory.is_dir():
        raise ValueError(f'Profile results directory not found: {directory}')
    choices, errors = [], []
    for folder, dirs, names in os.walk(directory, followlinks=False):
        dirs[:] = [d for d in sorted(dirs) if not (Path(folder)/d).is_symlink()
                   and d not in ('.git', '__pycache__', '.venv', 'venv')]
        if 'profile.json' not in names:
            continue
        path = Path(folder)/'profile.json'
        if path.is_symlink():
            errors.append(f'{path}: symbolic profile link ignored')
            continue
        try:
            choices.append(profile_choice(path))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append(f'{path}: {exc}')
        if len(choices) + len(errors) > 2000:
            raise ValueError('More than 2000 profiles; choose a narrower results directory')
    choices.sort(key=lambda c: (c.platform, c.park, c.target != 'strict', c.name, str(c.path)))
    choices.sort(key=lambda c: c.run, reverse=True)
    return choices, errors


def processing_mismatches(profile, root=ROOT):
    """Compare relevant source only; Windows CRLF alone is not a model change."""
    mismatches = []
    for name, expected in profile.metadata['processing']['source_hashes'].items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts or '\\' in name or ':' in name:
            raise ValueError('Invalid processing source path')
        path = root / relative
        if not path.is_file():
            mismatches.append(name + ' (missing)')
            continue
        contents = path.read_bytes()
        exact = hashlib.sha256(contents).hexdigest()
        lf = hashlib.sha256(contents.replace(b'\r\n', b'\n')).hexdigest()
        if expected not in (exact, lf):
            mismatches.append(name)
    return mismatches


def require_processing(profile, root=ROOT):
    """Strict source identity is mandatory for raw, causal reprocessing."""
    differences = processing_mismatches(profile, root)
    if differences:
        raise ValueError('Profile processing code/geometry differs from this checkout. '
                         'Raw replay cannot reuse the archived qualification; regenerate the profile '
                         'with the installed code, or review its saved diagnostics instead.\n'
                         + '\n'.join(differences))


@dataclass
class TickData:
    time: np.ndarray
    phase: np.ndarray
    status: tuple[str, ...]
    valid: np.ndarray
    post: np.ndarray
    gains: np.ndarray
    expected_requested: np.ndarray
    expected_output: np.ndarray
    expected_limited: np.ndarray
    expected_clipped: np.ndarray
    match_phase: np.ndarray | None = None
    residual: np.ndarray | None = None
    age: np.ndarray | None = None
    source: str = 'saved study'
    provenance: dict = field(default_factory=dict)
    video_path: Path | None = None
    video_offset_s: float = 0.
    events: tuple = ()


def read_diagnostics(path, profile):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 100_000_000:
        raise ValueError('Missing, linked or oversized diagnostic CSV: ' + str(path))
    required = {'ride_elapsed_s','profile_phase_s','match_status','applied','workspace_limited','final_clip'}
    for a in AXES:
        required.update(('multiplier_'+a, 'post_washout_'+a, 'requested_normalized_'+a))
    for j in range(1,7):
        required.update((f'requested_muscle_{j}_mm',f'output_muscle_{j}_mm'))
    rows = []
    with path.open(newline='', encoding='utf-8-sig') as stream:
        reader = csv.DictReader(stream)
        if not required.issubset(reader.fieldnames or []):
            raise ValueError('Not a Phase 1 dynamic diagnostic CSV (do not choose ordinary telemetry)')
        for i, row in enumerate(reader):
            if i > 250_000:
                raise ValueError('Too many diagnostic ticks')
            try:
                numeric = {k: float(row[k]) for k in required - {'match_status'}}
                if not all(math.isfinite(v) for v in numeric.values()):
                    raise ValueError('nonfinite values')
                if row['match_status'] not in STATUSES:
                    raise ValueError('unknown synchronisation status')
                if any(numeric[k] not in (0.,1.) for k in ('applied','workspace_limited','final_clip')):
                    raise ValueError('invalid flags')
                rows.append((numeric, row['match_status']))
            except (ValueError, TypeError, KeyError) as exc:
                raise ValueError(f'Invalid diagnostic row {i+2}: {exc}') from exc
    if len(rows) < 2:
        raise ValueError('Diagnostic CSV has fewer than two ticks')
    col = lambda key: np.array([r[0][key] for r in rows], dtype=float)
    matrix = lambda pattern: np.column_stack([col(pattern.format(a)) for a in AXES])
    times = col('ride_elapsed_s'); phase = col('profile_phase_s')
    if np.any(np.diff(times) <= 0) or not np.allclose(np.diff(times), .05, atol=1e-7, rtol=0):
        raise ValueError('Diagnostic ticks must increase at the recorded 20 Hz rate')
    post = matrix('post_washout_{}'); gains = matrix('multiplier_{}')
    if (gains < 0).any() or (gains > 2).any():
        raise ValueError('Diagnostic multipliers exceed 0..2')
    if not np.allclose(post*gains, matrix('requested_normalized_{}'), atol=1e-9, rtol=1e-9):
        raise ValueError('Diagnostic request does not equal post-washout value times multiplier')
    # A fallback transition is a convex combination. The blend/packet age are
    # not saved in Phase 1 CSVs, so never invent an exact blend or match residual.
    curve = profile.lookup_many(phase)
    lower = np.minimum(curve, profile.fallback)-1e-7
    upper = np.maximum(curve, profile.fallback)+1e-7
    # In loss, Playback holds its last good gain before crossfading. Check the
    # strict convex envelope only on usable/quiet fresh states, not held loss.
    good = np.array([r[1] in ('xyz','orientation','stationary_time') for r in rows])
    if np.any(((gains < lower)|(gains > upper))[good]):
        raise ValueError('Diagnostic multipliers disagree with profile lookup/fallback')
    return TickData(times, phase, tuple(r[1] for r in rows), col('applied').astype(bool),post,gains,
        np.column_stack([col(f'requested_muscle_{i}_mm') for i in range(1,7)]),
        np.column_stack([col(f'output_muscle_{i}_mm') for i in range(1,7)]),
        col('workspace_limited').astype(bool),col('final_clip').astype(bool),
        provenance={'diagnostics_path':str(path.resolve()),'diagnostics_sha256':sha256(path)})


@dataclass(frozen=True)
class ReviewSpec:
    profile_path: Path
    capture: str
    mode: str = 'dynamic'
    trial_path: Path | None = None

    def __post_init__(self):
        if self.mode not in MODES or not _basename(self.capture):
            raise ValueError('Invalid review mode or recording name')


@dataclass
class ProfileReviewSession:
    specs: tuple[ReviewSpec, ...]
    recordings: tuple[RideRecording, ...]
    active: int = 0
    source: str = 'saved study'

    @property
    def recording(self):
        return self.recordings[self.active]


def _calculate_ticks(profile, data, mode, cancel=None):
    """Exactly production rounding/limits, NOT the older player's double clamp."""
    backend = make_backend(profile.identity['platform'])
    backend.set_ride_neutral_height_delta_mm(profile.metadata['processing']['base_variant']['neutral_height_delta_mm'])
    cfg = backend.cfg
    lo, hi = float(cfg.MUSCLE_MIN_LENGTH), float(cfg.MUSCLE_MAX_LENGTH)
    floor_required = (backend._workspace_min_floor_clearance + backend._workspace_hull_margin
                      if backend._workspace_hull_xy else None)
    gains = data.gains if mode == 'dynamic' else np.ones_like(data.gains) if mode == 'unity' else np.tile(profile.fallback,(len(data.time),1))
    zero = min(0., float(data.time[0]))
    frames = []; max_req_error = max_out_error = 0.
    for i,t in enumerate(data.time):
        if i % 100 == 0: check_cancel(cancel)
        norm = data.post[i]*gains[i]
        real = backend.real_with_ride_neutral_offset(backend.k.norm_to_real(tuple(norm)))
        requested = np.array(lengths_for_real(backend, real))
        request_floor = backend._floor_clearance_for_real_transform(real)
        applied_real, act = backend._limit_workspace(real)
        workspace = (np.array(lengths_for_real(backend,applied_real)) if act is None else
                     np.array(act)-cfg.FIXED_HARDWARE_LENGTH)
        # This is the same conversion as Phase 1 and the production backend.
        output = np.array(backend.k.muscle_lengths_from_lengths(workspace+cfg.FIXED_HARDWARE_LENGTH), dtype=float)
        floor = backend._floor_clearance_for_real_transform(applied_real)
        clipped_mask = tuple(bool(x < lo-1e-7 or x > hi+1e-7) for x in workspace)
        clipping = any(clipped_mask)
        if mode == 'dynamic' and data.valid[i]:
            max_req_error = max(max_req_error,float(np.max(np.abs(requested-data.expected_requested[i]))))
            max_out_error = max(max_out_error,float(np.max(np.abs(output-data.expected_output[i]))))
            if (max_req_error > 1e-6 or max_out_error > 1e-6 or
                    bool(backend.workspace_limited) != bool(data.expected_limited[i]) or
                    clipping != bool(data.expected_clipped[i])):
                raise ValueError(f'Diagnostic/production kinematics mismatch at ride {t:.3f} s. '
                                 'Check profile, CSV and processing-source versions; no silent approximation is allowed.')
        excess = max(0.,float(np.max(lo-requested)),float(np.max(requested-hi)))
        gain_curve = np.asarray(profile.lookup(float(data.phase[i])))
        fallback_active = bool(mode == 'dynamic' and not np.allclose(gains[i],gain_curve,atol=1e-7,rtol=0))
        dynamic = {'mode':mode,'source':data.source,'ride_time_s':float(t),'phase_s':float(data.phase[i]),
            'status':data.status[i],'multipliers':tuple(map(float,gains[i])),
            'profile_multipliers':tuple(map(float,gain_curve)), 'fallback_active':fallback_active,
            'matched_phase_s':None if data.match_phase is None or not math.isfinite(data.match_phase[i]) else float(data.match_phase[i]),
            'position_residual_m':None if data.residual is None or not math.isfinite(data.residual[i]) else float(data.residual[i]),
            'observation_age_s':None if data.age is None else float(data.age[i])}
        dynamic['profile_lag_s'] = (None if dynamic['matched_phase_s'] is None else dynamic['matched_phase_s']-dynamic['phase_s'])
        applied = bool(data.valid[i])
        if not applied and frames:
            # No new hardware command was calculated on this tick. Show the
            # preceding held command rather than a hypothetical zero transform.
            output = np.array(frames[-1].muscle_lengths_mm)
        info = dict(time=float(t-zero),norm_input=tuple(data.post[i]),norm=tuple(norm),
            requested_real=tuple(map(float,real)),applied_real=tuple(map(float,applied_real)),
            requested=tuple(requested),workspace_lengths=tuple(workspace),permitted=tuple(output),
            limited=bool(backend.workspace_limited or clipping or excess>1e-7),
            workspace_limited=bool(backend.workspace_limited),scale=float(backend.workspace_scale),
            reason=backend.workspace_limit_reason,raw_out=excess>1e-7,raw_exceed_mm=excess,
            output_clipped=clipping, output_exceed_mm=max((max(lo-x,x-hi,0.) for x in workspace),default=0.),
            final_clip_mask=clipped_mask, output_quantized=True,
            requested_floor=request_floor,applied_floor=floor,floor=floor,
            below_floor=floor_required is not None and request_floor < floor_required-1e-7,
            intervention=bool(backend.workspace_limited or clipping),sample_dt_s=.05,
            dynamic=dynamic,valid=applied)
        if not applied and frames:
            previous=frames[-1]
            held_info=dict(previous.analysis)
            held_info.update(time=float(t-zero),dynamic=dynamic,valid=False)
            frames.append(replace(previous,seconds=float(t-zero),analysis=held_info))
        else:
            frames.append(PlaybackFrame(float(t-zero),tuple(data.post[i]),tuple(data.post[i]),tuple(norm),
                tuple(output),bool(backend.workspace_limited),floor,info))
    return frames,backend,zero,{'max_requested_difference_mm':max_req_error,'max_output_difference_mm':max_out_error}


def build_recording(spec, *, root=ROOT, cancel=None, progress=None):
    check_cancel(cancel)
    profile = DynamicProfile.load(spec.profile_path)
    reference = load_reference(spec.profile_path, profile)
    differences = processing_mismatches(profile, root)
    # Saved studies carry the original post-washout request and resulting six
    # lengths for every tick. A source-file hash can change without changing
    # its actual results (comments, unrelated legacy settings, etc.). Do not
    # invalidate an offline *review* until the installed numerical calculation
    # demonstrably differs. Raw causal replay still requires exact source identity.
    if spec.trial_path is not None:
        require_processing(profile,root)
    elif any(name.endswith(' (missing)') for name in differences):
        raise ValueError('Saved profile review requires the installed processing/geometry files. '
                         'This checkout is missing:\n' + '\n'.join(differences))
    source = next((x for x in profile.metadata['source_captures'] if x['trial'] == spec.capture),None)
    if source is None:
        raise ValueError('Selected recording is not one of this profile\'s bound source captures')
    if spec.trial_path is not None:
        from .dynamic_replay import replay_profile
        data = replay_profile(root, spec, profile, reference, cancel=cancel, progress=progress)
    else:
        path=Path(spec.profile_path).parent/(spec.capture+'_dynamic_diagnostics.csv')
        data = read_diagnostics(path,profile)
    if progress: progress('Checking displayed kinematics: '+profile.metadata['name'])
    # Static unity/fallback comparisons have no archived length trace of their
    # own. Before calculating either against a changed checkout, independently
    # prove that the *dynamic* trace matches the original recorded outputs.
    # The dynamic-mode parity check covers every applied tick and flags as well
    # as all six requested/output muscle lengths (tolerance: 1e-6 mm).
    try:
        if differences and spec.trial_path is None and spec.mode != 'dynamic':
            if progress: progress('Verifying archived dynamic outputs against installed geometry')
            _calculate_ticks(profile,data,'dynamic',cancel)
        frames,backend,zero,parity = _calculate_ticks(profile,data,spec.mode,cancel)
    except ValueError as exc:
        if differences and spec.trial_path is None:
            raise ValueError('Source files differ AND the installed kinematics cannot reproduce '
                             'the archived dynamic outputs. Review is blocked; do not override '
                             'the profile qualification. Changed files:\n'
                             + '\n'.join(differences) + '\nDetails: ' + str(exc)) from exc
        raise
    base = deepcopy(profile.metadata['processing']['base_variant'])
    metadata = platform_metadata(profile.identity['platform'])
    csv_path = Path(spec.profile_path).parent/(spec.capture+'_dynamic_diagnostics.csv')
    event_list = list(data.events)
    event_list.append(RideEvent(-zero,'First sustained movement','#84cbb5',
                     'Ride elapsed zero; pre-roll remains on the Ride Analyzer timeline'))
    note = ('Saved study: recorded post-washout values and B+C phase; local kinematics checked. '
            'No second washout or matching pass. Match residual/lookup lag unavailable in Phase 1 diagnostics.'
            if data.source=='saved study' else
            'Raw replay: full 20 Hz production processing and causal B+C matching; null hardware output.')
    if spec.mode!='dynamic':
        note += ' Comparison uses the selected profile\'s fixed cue/midpoint/base settings, not a preferred live variant.'
    if differences:
        note += (' WARNING: processing source hashes differ (' + ', '.join(differences) + '). '
                 'Recorded dynamic requests/output matched this checkout numerically; '
                 'offline viewing only. Original source qualification does NOT transfer '
                 'to the changed checkout, static alternatives or physical operation.')
    details = {'schema':'sim_player.dynamic-recording/1','profile_path':str(Path(spec.profile_path).resolve()),
        'profile_sha256':sha256(spec.profile_path),'capture':spec.capture,'capture_timing_sha256':source['timing_sha256'],
        'mode':spec.mode,'label':profile.metadata['name']+' / '+MODE_LABELS[spec.mode],
        'source':data.source,'time_origin_ride_s':zero,'profile':profile.to_dict(),
        'provenance':data.provenance,'parity':parity,
        'source_qualification':profile.metadata.get('qualification','unqualified'),
        'processing_source_mismatches':list(differences),
        'numerical_replay_parity_verified':bool(spec.trial_path is None and differences),
        'qualification_scope':('Archived research qualification does not transfer to this changed checkout'
                               if differences else 'Archived nominal assessment, not a live safety approval'),
        'readonly':True}
    return RideRecording(csv_path=csv_path.resolve(),ride=base['park_info']['name'],frames=tuple(frames),
        times=tuple(f.seconds for f in frames),events=tuple(sorted(event_list,key=lambda e:e.seconds)),
        video_path=data.video_path,sync_path=None,video_offset_s=data.video_offset_s,
        sync_source=('No video in saved timing study' if data.video_path is None else 'Timing-study monotonic alignment'),
        min_length_mm=float(backend.cfg.MUSCLE_MIN_LENGTH),max_length_mm=float(backend.cfg.MUSCLE_MAX_LENGTH),
        analysis_settings=f'{profile.identity["platform"]} | {MODE_LABELS[spec.mode]} | {base["angular_cue"]} | neutral {base["neutral_height_delta_mm"]:+g} mm',
        diagnostics=note,platform_key=profile.identity['platform'],cue_mode=base['angular_cue'],
        gain_percent=(100.,)*6,variant_label=base['label'],variant_snapshot=base,
        platform_config_module=metadata['platform_config_module'],platform_config_sha256=metadata['platform_config_sha256'],
        platform_neutral_height_mm=float(backend.k.PLATFORM_MID_HEIGHT),
        global_settings_snapshot=deepcopy(profile.metadata['processing']['telemetry']),dynamic_profile=details)


def build_session(specs, *, root=ROOT, cancel=None, progress=None):
    specs = tuple(specs)
    if not 1 <= len(specs) <= 2:
        raise ValueError('Select one or two profile review slots')
    profiles=[DynamicProfile.load(s.profile_path) for s in specs]
    first=profiles[0]
    for s,p in zip(specs,profiles):
        if s.capture != specs[0].capture:
            raise ValueError('A and B must use the same source recording')
        for key in ('park','platform','park_sha256','nl2_version','view_identity','departure_station','observed_vr'):
            if p.identity.get(key)!=first.identity.get(key):
                raise ValueError('A/B compatibility mismatch: '+key)
    hashes = [next((x['timing_sha256'] for x in p.metadata['source_captures'] if x['trial']==s.capture),None)
              for s,p in zip(specs,profiles)]
    if None in hashes or len(set(hashes))!=1:
        raise ValueError('A and B do not share the same recorded telemetry hash')
    if len({s.trial_path is None for s in specs})!=1:
        raise ValueError('A and B must use the same replay source mode')
    records=tuple(build_recording(s,root=root,cancel=cancel,progress=progress) for s in specs)
    if len(records)==2 and records[0].times!=records[1].times:
        raise ValueError('A/B capture tick timelines differ; cannot compare at the same playback time')
    return ProfileReviewSession(specs,records,source=records[0].dynamic_profile['source'])


def save_review(path, session, seconds=0., loop=None):
    """Save only a research view, never a live variant or edited source profile."""
    path = Path(path).resolve()
    if path.exists():
        raise FileExistsError('Choose a new review filename; existing studies are not overwritten')
    def relative(source):
        try:return os.path.relpath(source,path.parent)
        except ValueError:return str(Path(source).resolve())  # Different Windows volume.
    slots=[]
    for spec,rec in zip(session.specs,session.recordings):
        slots.append({'profile':relative(spec.profile_path),
            'profile_sha256':sha256(spec.profile_path),'capture':spec.capture,'mode':spec.mode,
            'trial':relative(spec.trial_path) if spec.trial_path else None,
            'trial_sha256':sha256(spec.trial_path) if spec.trial_path else None,
            'diagnostics_sha256':rec.dynamic_profile['provenance'].get('diagnostics_sha256')})
    value={'schema':REVIEW_SCHEMA,'slots':slots,'active':session.active,'seconds':float(seconds),'loop':loop}
    with path.open('x',encoding='utf-8') as stream:
        json.dump(value,stream,indent=2,allow_nan=False);stream.write('\n')


def read_review(path):
    path=Path(path).resolve();d=json.loads(path.read_text(encoding='utf-8'))
    if d.get('schema')!=REVIEW_SCHEMA or not 1<=len(d.get('slots',[]))<=2:
        raise ValueError('Not a dynamic-profile review session')
    specs=[]
    for s in d['slots']:
        p=(path.parent / s['profile']).resolve()
        if sha256(p)!=s['profile_sha256']:raise ValueError('Saved review profile has changed')
        spec=ReviewSpec(p,s['capture'],s['mode'],(path.parent/s['trial']).resolve() if s.get('trial') else None)
        if spec.trial_path is not None and s.get('trial_sha256') and sha256(spec.trial_path)!=s['trial_sha256']:
            raise ValueError('Saved review raw trial metadata has changed')
        if s.get('diagnostics_sha256') and sha256(p.parent/(spec.capture+'_dynamic_diagnostics.csv'))!=s['diagnostics_sha256']:
            raise ValueError('Saved review diagnostic trace has changed')
        specs.append(spec)
    seconds=float(d.get('seconds',0.))
    if not math.isfinite(seconds) or seconds<0:raise ValueError('Invalid saved playback time')
    active=int(d.get('active',0))
    if not 0<=active<len(specs):raise ValueError('Invalid saved review slot')
    loop=d.get('loop')
    if loop is not None:
        if (not isinstance(loop,dict) or loop.get('mode') not in ('full','range','loop') or
            any(not math.isfinite(float(loop.get(k,0.))) for k in ('start','end','loop_delay_s'))):
            raise ValueError('Invalid saved loop settings')
    return tuple(specs),active,seconds,d.get('loop')
