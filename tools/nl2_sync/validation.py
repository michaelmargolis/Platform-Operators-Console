"""Offline causal replay and checkpoint consistency (not independent ground truth)."""
from __future__ import annotations
from collections import Counter
from dataclasses import asdict
import time
import numpy as np
from .matcher import Matcher, Reference, Settings


def evaluate(reference_trace,target_trace,hits=(),settings=None):
    ref=Reference.from_trace(reference_trace)
    matcher=Matcher(ref,settings)
    times=target_trace.time-target_trace.meta['marks']['first_motion']
    outputs=[];cost=[];events=[];previous=None
    for t,p,q,v,r in zip(times,target_trace.xyz,target_trace.quat,target_trace.speed,target_trace.rows):
        start=time.perf_counter_ns()
        m=matcher.update(p,q,float(t),int(r['frame']),float(v))
        cost.append((time.perf_counter_ns()-start)/1e6)
        outputs.append(m)
        if m.status!=previous:
            events.append(dict(park=target_trace.meta['park_key'],reference=reference_trace.name,
                               target=target_trace.name,live_phase_s=float(t),**asdict(m)))
        previous=m.status
    count=Counter(m.status for m in outputs)
    errors=[];profile_errors=[];checks=[]
    for h in hits:
        tp=h['target_time_s']-target_trace.meta['marks']['first_motion']
        rp=h['reference_time_s']-reference_trace.meta['marks']['first_motion']
        j=int(np.searchsorted(times,tp,side='right')-1)
        if j<0 or j>=len(times)-1:continue
        a,b=outputs[j:j+2];dt=times[j+1]-times[j]
        if dt<=0 or dt>.25 or a.confidence!='matched' or b.confidence!='matched':continue
        f=(tp-times[j])/dt
        raw=a.phase_s+f*(b.phase_s-a.phase_s)-rp
        applied=a.profile_phase_s+f*(b.profile_phase_s-a.profile_phase_s)-rp
        errors.append(raw);profile_errors.append(applied)
        checks.append(dict(park=target_trace.meta['park_key'],reference=reference_trace.name,
                           target=target_trace.name,live_phase_s=float(tp),reference_phase_s=float(rp),
                           matcher_error_s=float(raw),slewed_profile_error_s=float(applied),
                           observed_interval_s=float(dt)))
    def mx(x):return float(np.max(np.abs(x))) if len(x) else None
    def pc(x):return float(np.percentile(np.abs(x),95)) if len(x) else None
    # A correct trajectory match does not guarantee the *applied profile clock*
    # has caught up. Report this error across ALL observations, including
    # low-information stationary intervals (which checkpoint checks exclude).
    profile_lag=[abs(m.phase_s-m.profile_phase_s) for m in outputs]
    stationary_profile_lag=[abs(m.phase_s-m.profile_phase_s) for m in outputs
                            if m.status=='stationary_time']
    lost=[i for i,m in enumerate(outputs) if m.confidence=='lost']
    durations=[]
    if lost:
        for segment in np.split(np.array(lost),np.flatnonzero(np.diff(lost)>1)+1):
            end=min(int(segment[-1])+1,len(times)-1)
            durations.append(float(times[end]-times[segment[0]]))
    row=dict(park=target_trace.meta['park_key'],reference=reference_trace.name,target=target_trace.name,
             machine=target_trace.machine,samples=len(times),matched_samples=sum(m.confidence=='matched' for m in outputs),
             lost_samples=len(lost),longest_lost_s=max(durations,default=0.),
             stationary_time_samples=sum(m.confidence=='low' for m in outputs),
             max_abs_profile_lag_s=max(profile_lag,default=0.),
             max_abs_stationary_profile_lag_s=max(stationary_profile_lag,default=0.),
             profile_lag_over_250ms_samples=sum(e>.25 for e in profile_lag),
             status_counts=str(dict(count)),reference_bytes=ref.bytes,
             checkpoint_count=len(errors),available_checkpoints=len(hits),
             matcher_checkpoint_max_abs_s=mx(errors),matcher_checkpoint_p95_abs_s=pc(errors),
             slewed_profile_checkpoint_max_abs_s=mx(profile_errors),
             observed_xyz_residual_p95_m=pc([m.position_residual_m for m in outputs if m.position_residual_m is not None]),
             matcher_host_p99_ms=float(np.percentile(cost,99)) if cost else None,
             note='Causal sample-by-sample replay. XYZ-derived checkpoint consistency is not external ground truth or a Pi timing measurement.')
    return row,checks,events
