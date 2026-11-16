# evaluate.py ver 1
"""All-tick kinematic qualification, episodes and transparent amplitude metrics."""
from __future__ import annotations
import numpy as np
from tools.nl2_sync.profile import AXES
from tools.analyze_nl2_csv import lengths_for_real
from tools.sim_player.optimization_search import motion_amplitudes
from .replay import profile_gains

EPS=1e-7


def episodes(time,mask):
    result=[];begin=None
    for i,yes in enumerate(np.r_[mask,False]):
        if yes and begin is None:begin=i
        if not yes and begin is not None:
            last=i-1;result.append({'start_s':float(time[begin]),'end_s':float(time[last]+.05),
                                   'duration_s':float((last-begin+1)*.05),'ticks':int(last-begin+1)})
            begin=None
    return result


def calculate(series,geometry,profile=None,*,phase_offset=0.,loss=None,constant=None):
    if constant is not None:gains=np.broadcast_to(np.asarray(constant),(len(series.time),6)).copy()
    elif profile is None:gains=np.ones_like(series.norm)
    else:gains=profile_gains(profile,series,phase_offset,loss)
    norm=series.norm*gains
    requested,floor,real=geometry.requested(norm)
    b=geometry.backend;limited=[];clipped=[];out=[];applied=[];applied_floor=[]
    for pose in real:
        p,act=b._limit_workspace(pose)
        lengths=lengths_for_real(b,p) if act is None else np.asarray(act)-b.cfg.FIXED_HARDWARE_LENGTH
        # Reuse production rounding/clamping exactly, including legacy semantics.
        actuator=(np.asarray(lengths)+b.cfg.FIXED_HARDWARE_LENGTH)
        output=np.asarray(b.k.muscle_lengths_from_lengths(actuator),dtype=float)
        limited.append(bool(b.workspace_limited));clipped.append(bool(np.any(np.asarray(lengths)<geometry.low) or np.any(np.asarray(lengths)>geometry.high)))
        out.append(output);applied.append(p)
        f=b._floor_clearance_for_real_transform(p);applied_floor.append(np.nan if f is None else f)
    return {'time':series.time,'valid':series.valid,'gains':gains,'norm':norm,'real':real,
            'requested':requested,'output':np.array(out),'floor':floor,'applied_floor':np.array(applied_floor),
            'limited':np.array(limited),'clipped':np.array(clipped),'applied_real':np.array(applied)}


def metrics(data,geometry,allowance):
    valid=data['valid'];active=valid & (data['time']>=0)
    if not np.any(active):raise ValueError('No applied motion ticks')
    raw=data['requested'];out=data['output'];norm=data['norm']
    excess=np.maximum(np.maximum(geometry.low-raw,raw-geometry.high),0.).max(axis=1)
    floorbad=np.zeros(len(raw),bool)
    if geometry.floor_required is not None:
        floorbad=(~np.isfinite(data['floor']) | (data['floor']<geometry.floor_required-EPS))
        if 'applied_floor' in data:
            floorbad|=(~np.isfinite(data['applied_floor']) | (data['applied_floor']<geometry.floor_required-EPS))
    finite=np.isfinite(raw).all(axis=1)&np.isfinite(out).all(axis=1)&np.isfinite(norm).all(axis=1)
    physical=(out<geometry.low-EPS).any(axis=1)|(out>geometry.high+EPS).any(axis=1)
    q=bool(np.all(finite[valid]) and not np.any(floorbad[valid]) and
           not np.any(physical[valid]) and excess[valid].max(initial=0.)<=allowance+EPS)
    if allowance==0:q=q and not np.any((data['limited']|data['clipped'])[valid])
    ep=episodes(data['time'],active&(excess>EPS))
    indices=np.where(active)[0];pairs=indices[1:][np.diff(indices)==1]
    step=(np.max(np.abs(out[pairs]-out[pairs-1]),axis=1) if len(pairs) else np.array([0.]))
    requestedstep=(np.max(np.abs(raw[pairs]-raw[pairs-1]),axis=1) if len(pairs) else np.array([0.]))
    amps=motion_amplitudes([np.clip(norm[active],-1,1)])
    return {'qualified':q,'scope':'observed capture ticks; NOT measured physical pose or safety approval',
        'active_ticks':int(active.sum()),'all_applied_ticks_checked':int(valid.sum()),
        'maximum_requested_excess_mm':float(excess[valid].max(initial=0.)),
        'requested_excess_ticks':int(np.sum(active&(excess>EPS))),
        'requested_excess_duration_s':round(float(np.sum(active&(excess>EPS))*.05),6),
        'longest_excess_episode_s':max((e['duration_s'] for e in ep),default=0.),
        'episodes':ep,'workspace_limited_ticks':int(np.sum(active&data['limited'])),
        'final_clip_ticks':int(np.sum(active&data['clipped'])),
        'floor_violation_ticks':int(np.sum(valid&floorbad)),
        'output_range_violation_ticks':int(np.sum(valid&physical)),
        'nonfinite_ticks':int(np.sum(valid&~finite)),
        'normalized_saturation_fraction':float(np.mean(np.abs(norm[active])>1.+EPS)),
        'max_output_step_mm':float(step.max(initial=0.)), 'p99_output_step_mm':float(np.percentile(step,99)),
        'max_requested_step_mm':float(requestedstep.max(initial=0.)),
        'axis_motion_amplitudes':dict(zip(AXES,amps)),
        'allowance_mm':float(allowance),
        'floor_required_mm':geometry.floor_required,
        'minimum_requested_floor_mm':float(np.nanmin(data['floor'][valid])) if geometry.floor_required is not None else None,
        'minimum_output_length_mm':float(out[valid].min()),'maximum_output_length_mm':float(out[valid].max())}


def diagnostic_rows(data,series):
    for i,t in enumerate(data['time']):
        row={'ride_elapsed_s':float(t),'profile_phase_s':float(series.phase[i]),'match_status':series.status[i],
             'applied':int(data['valid'][i]),'workspace_limited':int(data['limited'][i]),
             'final_clip':int(data['clipped'][i])}
        for j,a in enumerate(AXES):
            row['multiplier_'+a]=float(data['gains'][i,j]);row['post_washout_'+a]=float(series.norm[i,j])
            row['requested_normalized_'+a]=float(data['norm'][i,j])
        for j in range(6):
            row['requested_muscle_%d_mm'%(j+1)]=float(data['requested'][i,j])
            row['output_muscle_%d_mm'%(j+1)]=float(data['output'][i,j])
        row['requested_floor_mm']=float(data['floor'][i]) if np.isfinite(data['floor'][i]) else ''
        yield row
