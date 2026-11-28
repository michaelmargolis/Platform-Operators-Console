# replay.py ver 2
"""Replay real production code at 20Hz with the benchmark's null interfaces.

Search arrays are measured at the actual post-washout boundary. Final verification
re-runs raw packets through production including kinematics and pressure mapping.
No reimplementation of the telemetry conversion, filters or IK is used.
"""
from __future__ import annotations
from dataclasses import dataclass
from copy import deepcopy
from pathlib import Path
import hashlib
import inspect
import numpy as np
from core.motion_convention import MOTION_CONVENTION
from core.platform_geometry import geometry_id_for_platform
from tools.nl2_sync.matcher import Reference, Settings
from tools.nl2_sync.profile import AXES, ProfilePlayback, digest_json
from tools.nl2_sync_benchmark.pipeline import Pipeline
from tools.nl2_sync_benchmark.runner import packets
from tools.analyze_nl2_csv import lengths_for_real

# These define motion and profile identity, not unrelated UI source.
SOURCE_FILES = ('config/platforms.py','core/motion_convention.py','sims/nolimits2/config.py',
    'core/platform_controller.py','core/telemetry_config.py','core/platform_geometry.py',
    'core/ride_neutral.py','kinematics/kinematics_V3.py','kinematics/cfg_SuspendedChairV3.py',
    'kinematics/cfg_SuspendedChair.py','kinematics/cfg_SuspendedPlatformMdx.py',
    'sims/nolimits2/sim.py','sims/nolimits2/nl2_protocol.py','sims/nolimits2/angular_cues.py',
    'sims/nolimits2/ride_config.py','tools/nl2_sync/matcher.py','tools/nl2_sync/profile.py')


@dataclass
class Series:
    trace: object
    time: np.ndarray
    phase: np.ndarray
    status: list
    age: np.ndarray
    norm: np.ndarray
    valid: np.ndarray
    snapshot: dict
    globals: dict
    matcher_settings: dict
    baseline_lengths: np.ndarray
    @property
    def rows(self):
        # Adapter for the established static search's capture-length interface.
        return self.norm


def configure(pipe, cue, neutral=None, globals_snapshot=None, base_gains=None, snapshot=None):
    """Freeze the variant's existing heave and washout; change only explicit study settings."""
    if snapshot is not None:
        pipe.snapshot=deepcopy(snapshot)
        pipe.sim._heave_mode=snapshot['heave_mode']
        pipe.sim._normalized_height_amplitude=snapshot['normalized_height_amplitude']
        pipe.telem.set_angular_washout_overrides(snapshot.get('roll_washout'),snapshot.get('pitch_washout'),reset=True)
        pipe.telem.set_temporary_classical_z(snapshot['heave_mode']=='vertical_g')
    pipe.sim._angular_cue_mode=cue
    pipe.sim._reset_transform_tracking()
    pipe.snapshot=deepcopy(pipe.snapshot);pipe.snapshot['angular_cue']=cue
    if globals_snapshot is not None:pipe.telem.apply_values(globals_snapshot)
    if base_gains is not None:
        pipe.telem.set_variant_gain_scales(base_gains)
        pipe.snapshot['gain_scale_pct']=dict(zip(AXES,base_gains))
    if neutral is not None:
        pipe.backend.set_ride_neutral_height_delta_mm(neutral)
        pipe.snapshot['neutral_height_delta_mm']=float(neutral)


def timeline(trace):
    encoded=packets(trace)
    start=min(0.,encoded[0][0]);end=trace.meta['marks']['view_train_returned']-trace.meta['marks']['first_motion']
    # Integer tick counters avoid numerical drift; same starting phase as benchmark.
    ticks=start+np.arange(int(np.floor((end-start)/.05+1e-8))+1)*.05
    return encoded,ticks


def prepare(root, trace, reference, platform, cue, *, variant=None):
    from dataclasses import asdict
    ref=Reference.from_trace(reference);settings=Settings()
    pipe=Pipeline(root,platform,trace.meta['park_key'],trace,ref,settings,sync=True,twin=False,variant=variant)
    configure(pipe,cue)
    post=[];phases=[];states=[];ages=[];valid=[];lengths=[]
    original=pipe.telem.process;current=[np.zeros(6)]
    def record(*args,**kwargs):
        value=original(*args,**kwargs);current[0]=np.asarray(value,dtype=float);return value
    pipe.telem.process=record
    encoded,ticks=timeline(trace);cursor=0
    try:
        for t in ticks:
            old=cursor
            while cursor<len(encoded) and encoded[cursor][0]<=t+1e-9:cursor+=1
            pipe.feed([p for _,p in encoded[old:cursor]])
            row=pipe.tick(float(t))
            post.append(current[0].copy() if row['valid'] else np.zeros(6))
            phases.append(row['profile_phase_s'] if row['profile_phase_s'] is not None else 0.)
            states.append(row['match_status']);valid.append(row['applied'])
            ages.append(max(0.,t-encoded[cursor-1][0]) if cursor else 1e6)
            lengths.append(list(pipe.backend.muscle_lengths))
        return Series(trace,ticks,np.array(phases),states,np.array(ages),np.array(post),
                      np.array(valid,dtype=bool),deepcopy(pipe.snapshot),pipe.telem.snapshot(),
                      asdict(settings),np.array(lengths))
    finally:pipe.close()


def processing_identity(root, series, platform, neutral, reference_hash):
    hashes={p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in SOURCE_FILES}
    snapshot=deepcopy(series.snapshot);snapshot['neutral_height_delta_mm']=float(neutral)
    geometry_id=geometry_id_for_platform(platform)
    context={'platform':platform,'base_variant':snapshot,'telemetry':series.globals,
             'transform_convention':MOTION_CONVENTION,'geometry_id':geometry_id,
             'intensity_percent':100.,'tick_s':.05,'matcher':series.matcher_settings,'source_hashes':hashes}
    m=series.trace.meta
    identity={'park':m['park_key'],'platform':platform,'reference_sha256':reference_hash,
              'transform_convention':MOTION_CONVENTION,'geometry_id':geometry_id,
              'processing_sha256':digest_json(context), 'park_sha256':m.get('park_sha256',''),
              'nl2_version':str(m.get('nl2_version','unknown')),
              'view_identity':m.get('view_identity'),'departure_station':m.get('departure_station'),
              'observed_vr':series.trace.summary.get('vr_observed','unknown')}
    return identity,context


def profile_gains(profile, series, phase_offset=0., forced_loss=None):
    player=ProfilePlayback(profile)
    output=[]
    for i,t in enumerate(series.time):
        loss=forced_loss is not None and forced_loss[0]<=t<forced_loss[1]
        status='lost' if loss else series.status[i]
        phase=series.phase[i]+phase_offset
        output.append(player.update(phase,status,float(t),series.age[i]))
    return np.array(output)


def verify_production(root, series, reference, platform, profile):
    """Independent RAW replay, not a replay of optimized/cached length arrays."""
    base=profile.metadata['processing']['base_variant']
    pipe=Pipeline(root,platform,series.trace.meta['park_key'],series.trace,Reference.from_trace(reference),
                  Settings(**series.matcher_settings),sync=True,twin=False)
    configure(pipe,base['angular_cue'],base['neutral_height_delta_mm'],series.globals,
              [base['gain_scale_pct'][a] for a in AXES],snapshot=base)
    player=ProfilePlayback(profile);encoded,ticks=timeline(series.trace);cursor=0;current_time=[0.];age=[1e6]
    class PlaybackHook:
        def lookup(self,phase):
            return player.update(phase,pipe.last_match.status,current_time[0],age[0])
    pipe.profile=PlaybackHook()
    # Pipeline does not call profile.lookup during pre-roll. Apply the same
    # fallback then at this existing boundary; all active normal processing stays unchanged.
    original=pipe.telem.process;processed=[np.zeros(6)]
    def process(*args,**kwargs):
        value=original(*args,**kwargs);processed[0]=np.array(value)
        if current_time[0]<0:
            player.update(0.,'waiting',current_time[0],age[0])
            return tuple(np.array(value)*profile.fallback)
        return value
    pipe.telem.process=process
    out=[];raw=[];floors=[];norms=[];applied_real=[];limited=[];clipped=[];pressures=[];valid=[];ph=[];st=[]
    try:
        for t in ticks:
            old=cursor
            while cursor<len(encoded) and encoded[cursor][0]<=t+1e-9:cursor+=1
            age[0]=max(0.,t-encoded[cursor-1][0]) if cursor else 1e6
            current_time[0]=float(t);pipe.feed([p for _,p in encoded[old:cursor]])
            row=pipe.tick(float(t));valid.append(row['applied'])
            gains=(profile.fallback if t<0 else (1.-player.blend**2*(3.-2.*player.blend))*profile.fallback+
                   (player.blend**2*(3.-2.*player.blend))*player.held)
            req=processed[0]*gains if row['valid'] else np.zeros(6)
            real=pipe.backend.real_with_ride_neutral_offset(pipe.backend.k.norm_to_real(tuple(req)))
            raw.append(lengths_for_real(pipe.backend,real))
            floors.append(pipe.backend._floor_clearance_for_real_transform(real))
            out.append(list(pipe.backend.muscle_lengths));norms.append(req)
            applied_real.append(list(pipe.backend.last_applied_real_transform))
            limited.append(row['workspace_limited']);clipped.append(row['final_length_clip'])
            pressures.append(list(pipe.backend.sent_pressures));ph.append(row['profile_phase_s']);st.append(row['match_status'])
        return {'time':ticks,'valid':np.array(valid),'norm':np.array(norms),'requested':np.array(raw),
                'output':np.array(out),'floor':np.array([np.nan if x is None else x for x in floors]),
                'limited':np.array(limited),'clipped':np.array(clipped),'applied_real':np.array(applied_real),
                'pressures':np.array(pressures),'phase':ph,'status':st}
    finally:pipe.close()
