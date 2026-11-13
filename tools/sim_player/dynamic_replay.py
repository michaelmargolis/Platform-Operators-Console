"""Raw timing-session replay for dynamic-profile review.

Uses the installed benchmark's actual production parser/service/filter/kinematics
path, with injected null Festo/Twin transports. No UI, no live client, no output.
The whole capture is processed once in order; seeking subsequently reads cached
frames and cannot reset washout or advance the matcher out of order.
"""
from __future__ import annotations
from pathlib import Path
import math
import numpy as np

from .dynamic_review import TickData, check_cancel, sha256
from tools.nl2_sync.profile import AXES, ProfilePlayback
from tools.nl2_sync.matcher import Settings
from tools.nl2_sync_benchmark.pipeline import Pipeline
from tools.nl2_dynamic_profiles.replay import configure, timeline
from tools.nl2_timing_study.analysis import read_trial, safe_companion
from tools.analyze_nl2_csv import lengths_for_real


def _verified_trace(spec, profile):
    path=Path(spec.trial_path).resolve()
    trace=read_trial(path)
    if trace.issues:
        raise ValueError('Selected timing trial is not qualified: '+'; '.join(trace.issues))
    if trace.name!=spec.capture:
        raise ValueError('Raw trial name does not match the selected study recording')
    expected=next(x['timing_sha256'] for x in profile.metadata['source_captures'] if x['trial']==spec.capture)
    timing=safe_companion(path,trace.meta['files']['timing'])
    if sha256(timing)!=expected:
        raise ValueError('Raw timing hash differs from the capture bound to this profile')
    identity=profile.identity
    checks={'park':trace.meta['park_key'],'park_sha256':trace.meta.get('park_sha256',''),
        'nl2_version':str(trace.meta.get('nl2_version','unknown')),
        'view_identity':trace.meta.get('view_identity'),'departure_station':trace.meta.get('departure_station'),
        'observed_vr':trace.summary.get('vr_observed','unknown')}
    for key,value in checks.items():
        if identity.get(key)!=value:raise ValueError('Raw capture/profile mismatch: '+key)
    return trace


def _media(trace, zero, duration):
    """Use the same capture's video only. Never borrow video from another repeat."""
    from .recording import (_read_sync,_load_events,_find_video,_video_offset,read_saved_calibration,RideEvent)
    meta=trace.meta;files=meta['files'];folder=trace.path.parent
    original=files.get('telemetry') or files.get('capture') or files.get('csv')
    csv_path=folder/original if original and Path(original).name==original else folder/(trace.name+'.csv')
    manifest,_=_read_sync(folder/(trace.name+'_video_sync.json'))
    video,_=_find_video(csv_path,manifest)
    # Ordinary capture elapsed zero is defined by its monotonic anchor; our
    # zero is the first 20 Hz pre-roll tick relative to first sustained movement.
    epoch = float(meta['epoch_monotonic_s'])
    player_zero = epoch + float(meta['marks']['first_motion']) + zero
    events=manifest.get('events',{})
    try:
        capture_zero=float(events['capture_elapsed_zero']['monotonic_s'])
        delta=player_zero-capture_zero
        if not math.isfinite(delta):raise ValueError()
    except (ValueError,TypeError,KeyError):
        video=None;delta=0.
    offset,_=_video_offset(manifest)
    saved=read_saved_calibration(csv_path)
    if saved is not None:offset=saved
    # Reuse the established event loader with a temporary in-memory anchor for
    # this player's pre-roll zero. The actual video-sync JSON is never edited.
    from copy import deepcopy
    adjusted=deepcopy(manifest)
    adjusted.setdefault('events',{})['capture_elapsed_zero']={'monotonic_s':player_zero}
    event_path=folder/files.get('events',trace.name+'_state_events.csv')
    timeline,_=_load_events(event_path,adjusted,csv_path,duration)
    return video,offset+delta,timeline


def replay_profile(root,spec,profile,reference,*,cancel=None,progress=None):
    trace=_verified_trace(spec,profile)
    context=profile.metadata['processing'];base=context['base_variant']
    pipe=Pipeline(root,profile.identity['platform'],profile.identity['park'],trace,reference,
                  Settings(**context['matcher']),sync=True,twin=False)
    try:
        configure(pipe,base['angular_cue'],base['neutral_height_delta_mm'],context['telemetry'],
                  [base['gain_scale_pct'][a] for a in AXES],snapshot=base)
        player=ProfilePlayback(profile,profile.metadata.get('fallback_transition_s',.25),
                               profile.metadata.get('max_observation_age_s',.25))
        encoded,ticks=timeline(trace);cursor=0;now=[0.];age=[1e6];gains=[np.array(profile.fallback)]
        class Hook:
            def lookup(self,phase):
                gains[0]=np.asarray(player.update(phase,pipe.last_match.status,now[0],age[0]))
                return tuple(gains[0])
        pipe.profile=Hook()
        original=pipe.telem.process;post=[np.zeros(6)]
        def process(*args,**kwargs):
            v=original(*args,**kwargs);post[0]=np.asarray(v,dtype=float)
            if now[0]<0:
                player.update(0.,'waiting',now[0],age[0]);gains[0]=np.array(profile.fallback)
                return tuple(post[0]*profile.fallback)
            return v
        pipe.telem.process=process
        result={k:[] for k in ('phase','status','valid','post','gains','requested','output','limited','clipped','match_phase','residual','age')}
        for i,t in enumerate(ticks):
            if i%100==0:
                check_cancel(cancel)
                if progress:progress(f'Replaying raw telemetry: {i}/{len(ticks)} ticks')
            old=cursor
            while cursor<len(encoded) and encoded[cursor][0]<=t+1e-9:cursor+=1
            now[0]=float(t);age[0]=max(0.,t-encoded[cursor-1][0]) if cursor else 1e6
            pipe.feed([p for _,p in encoded[old:cursor]])
            row=pipe.tick(float(t))
            request=post[0]*gains[0] if row['valid'] else np.zeros(6)
            real=pipe.backend.real_with_ride_neutral_offset(pipe.backend.k.norm_to_real(tuple(request)))
            raw=lengths_for_real(pipe.backend,real)
            values=dict(phase=row['profile_phase_s'] if row['profile_phase_s'] is not None else 0.,
                status=row['match_status'],valid=row['applied'],post=post[0].copy(),gains=gains[0].copy(),
                requested=raw,output=list(pipe.backend.muscle_lengths),limited=row['workspace_limited'],
                clipped=row['final_length_clip'],match_phase=row['match_phase_s'],
                residual=row['match_position_residual_m'],age=age[0])
            for key,value in values.items():result[key].append(value)
        f=lambda key:np.asarray(result[key],dtype=float)
        video,offset,events=_media(trace,min(0.,float(ticks[0])),float(ticks[-1]-ticks[0]))
        return TickData(ticks,f('phase'),tuple(result['status']),np.asarray(result['valid'],bool),
            f('post'),f('gains'),f('requested'),f('output'),np.asarray(result['limited'],bool),
            np.asarray(result['clipped'],bool),f('match_phase'),f('residual'),f('age'),source='raw causal replay',
            provenance={'trial_path':str(trace.path),'trial_sha256':sha256(trace.path),
                        'timing_sha256':trace.meta['file_sha256']['timing']},
            video_path=video,video_offset_s=offset,events=events)
    finally:
        pipe.close()
