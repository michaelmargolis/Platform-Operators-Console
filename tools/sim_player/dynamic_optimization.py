"""Bounded offline search for C1, post-washout gain curves.

Uses production POC kinematics. Static midpoint/cue seeds reuse v192's search;
local coordinate and tradeoff moves improve motion without reducing every DOF
throughout the ride. Search screening is followed by ALL-TICK repair/validation.
"""
from __future__ import annotations
from dataclasses import dataclass
import math
import numpy as np
from tools.analyze_nl2_csv import make_backend, lengths_for_real
from tools.nl2_sync.profile import weights, interpolate
from .optimization_search import (Evaluator,Parameters,SearchOptions,search_candidates,
                                   motion_amplitudes,PRIMARY)

EPS=1e-7


class PreparedEvaluator(Evaluator):
    """Use the existing static search with real service-tick filtered arrays."""
    def __init__(self, by_cue, platform, options):
        first=next(iter(by_cue.values()))
        self.captures=first;self.platform=platform;self.snapshot=first[0].snapshot;self.options=options
        self.backend=make_backend(platform)
        self.stroke_mm=float(self.backend.cfg.MUSCLE_MAX_LENGTH-self.backend.cfg.MUSCLE_MIN_LENGTH)
        self.floor_required=(self.backend._workspace_min_floor_clearance+self.backend._workspace_hull_margin
                             if self.backend._workspace_hull_xy else None)
        self.prepared={k:[x.norm for x in v] for k,v in by_cue.items()}
        amps=np.max([motion_amplitudes([np.clip(a,-1,1) for a in v]) for v in self.prepared.values()],axis=0)
        self.active_primary=tuple(i for i in PRIMARY if amps[i]>=.005)
        self.indices=[]
        for ci,c in enumerate(first):
            n=len(c.norm);idx=set(np.linspace(0,n-1,min(options.sample_frames,n),dtype=int))
            for arrays in self.prepared.values():
                for axis in range(6):
                    for k in (np.argmin(arrays[ci][:,axis]),np.argmax(arrays[ci][:,axis])):
                        idx.update(j for j in (k-1,k,k+1) if 0<=j<n)
            self.indices.append(idx)
        self.cache={};self.full_cache={};self.evaluations=0;self.screen_evaluations=0;self._trial_ids=set()
        self.full_evaluations=0;self.neutral_cache={};self.bad_frames=[set() for _ in first]


def static_seeds(by_cue,platform,*,evaluations=420,neutral_min=-60.,neutral_max=60.,progress=print):
    opts=SearchOptions(evaluations=evaluations,sample_frames=96,starts=2,alternatives=0,
                       neutral_min=neutral_min,neutral_max=neutral_max)
    ev=PreparedEvaluator(by_cue,platform,opts)
    base=Parameters(next(iter(by_cue)),(100,)*6,0.)
    found=search_candidates(ev,base,[],progress)
    return found,ev


class Geometry:
    def __init__(self,platform,neutral):
        self.backend=make_backend(platform);self.backend.set_ride_neutral_height_delta_mm(neutral)
        b=self.backend;self.low=float(b.cfg.MUSCLE_MIN_LENGTH);self.high=float(b.cfg.MUSCLE_MAX_LENGTH)
        self.stroke=self.high-self.low
        probe=b.k.muscle_lengths_from_lengths([b.cfg.MIN_ACTUATOR_LENGTH-2.]*6)
        self.minimum_output_clamped=bool(min(probe)>=self.low)
        self.active_high=(float(b.cfg.MAX_ACTIVE_ACTUATOR_LENGTH-b.cfg.FIXED_HARDWARE_LENGTH)
                          if b.workspace_limit_mode=='reserve' else self.high)
        self.floor_required=(b._workspace_min_floor_clearance+b._workspace_hull_margin if b._workspace_hull_xy else None)
    def requested(self,norm):
        lengths=[];floors=[];real=[];b=self.backend
        for row in norm:
            pose=b.real_with_ride_neutral_offset(b.k.norm_to_real(tuple(row)))
            lengths.append(lengths_for_real(b,pose));real.append(pose)
            f=b._floor_clearance_for_real_transform(pose)
            floors.append(np.nan if f is None else f)
        return np.array(lengths),np.array(floors),np.array(real)
    def violation(self,lengths,floor,allowance,margin=0.):
        high=self.active_high if allowance==0 else self.high
        # Legacy POC only clamps maximum length. Do not qualify a relaxed
        # lower-limit violation as if an absent lower clamp made it safe.
        low_allowance=allowance if self.minimum_output_clamped else 0.
        excess=np.maximum.reduce((self.low+margin-low_allowance-np.min(lengths,axis=1),
                                  np.max(lengths,axis=1)-high+margin-allowance,
                                  np.zeros(len(lengths))))
        if self.floor_required is not None:
            excess+=2*np.maximum(0.,self.floor_required+margin-floor)
        return excess


@dataclass
class Operator:
    index: np.ndarray
    weight: np.ndarray
    blend: np.ndarray
    norm: np.ndarray
    time: np.ndarray
    source: np.ndarray

    def gains(self,controls,fallback):
        target=controls[self.index]*(1.-self.weight[:,None])+controls[self.index+1]*self.weight[:,None]
        return self.blend[:,None]*target+(1.-self.blend[:,None])*fallback


def operator(series,knots,offset=0.,loss=None):
    """Linear interpolation operator identical to ProfilePlayback, including fallback."""
    indexes=[];ws=[];bs=[];norm=[];times=[];sources=[]
    for si,s in enumerate(series):
        blend=0.;last=None;held_i=0;held_w=0.
        for j,t in enumerate(s.time):
            dt=0. if last is None else float(t-last);last=t
            status='lost' if loss and loss[0]<=t<loss[1] else s.status[j]
            phase=s.phase[j]+offset
            good=(t>=0 and status in ('xyz','orientation','stationary_time','stale')
                  and 0<=s.age[j]<=.25 and -.25<=phase<=knots[-1]+.25)
            if good:
                i,w=weights(knots,float(phase));held_i=int(i);held_w=float(w)
            blend=float(np.clip(blend+dt/.25*(1 if good else -1),0.,1.))
            if not s.valid[j]:continue
            indexes.append(held_i);ws.append(held_w);bs.append(blend*blend*(3.-2.*blend))
            norm.append(s.norm[j]);times.append(t);sources.append(si)
    return Operator(np.array(indexes,dtype=int),np.array(ws),np.array(bs),np.array(norm),np.array(times),np.array(sources))


def rank_motion(norm,lengths,controls,knots,*,soft_step_mm=5.,smooth_weight=.015,times=None,sources=None):
    # Local meaningful amplitude; no gain reward when its source channel is zero.
    a=np.mean(np.abs(np.clip(norm,-1,1)),axis=0)
    active=[a[i] for i in PRIMARY if a[i]>.0001]
    primary=(.7*np.mean(active)+.3*math.prod(active)**(1/len(active))) if active else 0.
    slope=np.diff(controls,axis=0)/np.diff(knots)[:,None] if len(knots)>1 else np.zeros((1,6))
    rough=float(np.mean(slope*slope))
    # Advisory 5mm/20Hz is a search preference, not a certified muscle limit.
    steps=np.max(np.abs(np.diff(lengths,axis=0)),axis=1) if len(lengths)>1 else np.array([0.])
    if times is not None and len(lengths)>1:
        dt=np.diff(times);okay=(dt>0)&(dt<=.251)&(np.diff(sources)==0)
        steps=steps[okay]*.05/dt[okay]
    step_cost=float(np.mean(np.maximum(0.,steps-soft_step_mm)))/200. if len(steps) else 0.
    p=primary-smooth_weight*rough-.02*step_cost
    # Lower tiers may trade less than 0.5% of local normalized primary amplitude.
    return (round(p/.005),round(float(a[5])/.005),round(float(a[0]+a[1])/.01),
            p,float(a[5]),float(a[0]+a[1]),-rough)


def slope_ok(controls,knots,maximum):
    # max derivative of smoothstep is 1.5/dt; value bounds alone are insufficient.
    return bool(np.all(1.5*np.abs(np.diff(controls,axis=0))/np.diff(knots)[:,None]<=maximum+EPS))


def conservative_fallback(geometry,series,seed,reserve=2.):
    arrays=np.concatenate([s.norm[s.valid] for s in series]);g=np.array(seed,dtype=float)
    # Neutral legality checked by backend setter/static search. Do not assume
    # feasibility is monotone: actually test each step, including the endpoint.
    for factor in list(np.linspace(1.,0.,51)):
        candidate=g*factor
        lens,floor,_=geometry.requested(arrays*candidate)
        if np.max(geometry.violation(lens,floor,0.,reserve),initial=0)<=EPS:
            return candidate
    raise ValueError('No strict fallback at this neutral height, including zero dynamic motion')


def optimize(series,geometry,seed,*,allowance,spacing=1.,max_slope=1.,sweeps=2,reserve=2.,jitter=.1,progress=print):
    end=max(float(s.phase.max()) for s in series)
    knots=np.linspace(0.,end,max(2,int(np.ceil(end/spacing))+1))
    fallback=conservative_fallback(geometry,series,seed,max(5.,reserve))
    # Start with a strict fallback. It remains an evaluated recovery option, not
    # a claim that only that global amplitude is useful throughout the ride.
    controls=np.tile(fallback,(len(knots),1))
    shifts=(-jitter,0.,jitter) if jitter else (0.,)
    ops=[operator(series,knots,offset=x) for x in shifts]
    op=Operator(*(np.concatenate([getattr(x,field) for x in ops]) for field in ('index','weight','blend','norm','time')),
                np.concatenate([x.source+i*len(series) for i,x in enumerate(ops)]))
    norm=op.norm*op.gains(controls,fallback)
    lengths,floors,_=geometry.requested(norm)
    margin=reserve
    screen=np.arange(len(op.norm))%3==0
    # Include run endpoints and the hardest baseline rows, then enrich on failure.
    for si in np.unique(op.source):
        ii=np.where(op.source==si)[0];screen[ii[[0,-1]]]=True
    violations=geometry.violation(lengths,floors,allowance,margin)
    screen[np.argsort(violations)[-32:]]=True
    supports=[np.where(((op.index==k)|(op.index+1==k))&(op.blend>0))[0] for k in range(len(knots))]
    evaluations=0
    for sweep in range(sweeps):
        step=(.5,.25,.125)[min(sweep,2)]
        progress('local curve sweep %s/%s, step %.3f'%(sweep+1,sweeps,step))
        for k in (range(len(knots)) if sweep%2==0 else reversed(range(len(knots)))):
            full=supports[k];ids=full[screen[full]]
            if not len(ids):continue
            # Never reward quiet/zero channels just for a larger numeric gain.
            axes=[a for a in (3,4,2,5,0,1) if np.max(np.abs(op.norm[ids,a]))>.001]
            lo=max(0,k-1);hi=min(len(knots),k+2)
            for attempt in range(2):
                current=rank_motion(norm[ids],lengths[ids],controls[lo:hi],knots[lo:hi],times=op.time[ids],sources=op.source[ids])
                best=None;best_rank=current
                trials=[]
                for a in axes:
                    v=controls[k].copy();v[a]=min(2.,v[a]+step);trials.append(v)
                    # Trade a lower-priority channel only if a primary benefits.
                    if a in PRIMARY:
                        for secondary in (5,0,1):
                            v=controls[k].copy();v[a]=min(2.,v[a]+step);v[secondary]=max(0.,v[secondary]-step)
                            trials.append(v)
                for value in trials:
                    # Clip the proposal to its neighbours' C1 rate envelope,
                    # so short knot spacing still permits a smaller useful move.
                    for neighbour in (k-1,k+1):
                        if 0<=neighbour<len(knots):
                            allowed=max_slope*abs(knots[neighbour]-knots[k])/1.5
                            value=np.clip(value,controls[neighbour]-allowed,controls[neighbour]+allowed)
                    value=np.clip(value,0.,2.)
                    if np.array_equal(value,controls[k]):continue
                    trial=controls.copy();trial[k]=value
                    if not slope_ok(trial[lo:hi],knots[lo:hi],max_slope):continue
                    mix=(trial[op.index[ids]]*(1-op.weight[ids,None])+trial[op.index[ids]+1]*op.weight[ids,None])
                    g=op.blend[ids,None]*mix+(1-op.blend[ids,None])*fallback
                    n=op.norm[ids]*g;l,f,_=geometry.requested(n);evaluations+=1
                    if np.max(geometry.violation(l,f,allowance,margin),initial=0)>EPS:continue
                    rank=rank_motion(n,l,trial[lo:hi],knots[lo:hi],times=op.time[ids],sources=op.source[ids])
                    if rank>best_rank:best=(trial,n,l,f);best_rank=rank
                if best is None:break
                controls=best[0];norm[ids]=best[1];lengths[ids]=best[2];floors[ids]=best[3]
        # Every actual service tick, not just the screening subset.
        norm=op.norm*op.gains(controls,fallback);lengths,floors,_=geometry.requested(norm)
        bad=geometry.violation(lengths,floors,allowance,margin)>EPS
        screen|=bad
        # Local shrink towards a validated fallback. Feasibility is rechecked
        # after EACH change; no assumption about independent DOF monotonicity.
        for repair in range(32):
            if not np.any(bad):break
            affected=np.unique(np.r_[op.index[bad],op.index[bad]+1])
            controls[affected]=fallback+.7*(controls[affected]-fallback)
            if repair==25:controls[affected]=fallback
            # Enforce the C1 slope bound by propagating reductions, never by
            # increasing a neighbour and potentially creating a new demand peak.
            for _ in range(5):
                for kk in range(len(knots)-1):
                    delta=max_slope*(knots[kk+1]-knots[kk])/1.5
                    controls[kk]=np.minimum(controls[kk],controls[kk+1]+delta)
                    controls[kk+1]=np.minimum(controls[kk+1],controls[kk]+delta)
            norm=op.norm*op.gains(controls,fallback);lengths,floors,_=geometry.requested(norm)
            bad=geometry.violation(lengths,floors,allowance,margin)>EPS;screen|=bad
        if np.any(bad):
            # Revert explicitly rather than export a curve verified on only its knots.
            controls[:]=fallback
            norm=op.norm*op.gains(controls,fallback);lengths,floors,_=geometry.requested(norm)
            if np.max(geometry.violation(lengths,floors,allowance),initial=0)>EPS:
                raise ValueError('Curve repair and validated fallback failed')
    if not slope_ok(controls,knots,max_slope):raise ValueError('Final gain slew bound failed')
    return knots,controls,fallback,{'local_evaluations':evaluations,'knots':len(knots),
        'search_frames':int(screen.sum()),'all_fit_ticks_checked':len(op.norm),
        'max_multiplier_rate_per_reference_s':float(np.max(1.5*np.abs(np.diff(controls,axis=0))/np.diff(knots)[:,None])),
        'priority':'roll/pitch/heave, then yaw, then surge/sway',
        'search_method':'static multi-start seed, local coordinate/tradeoff curves, all-tick repair',
        'fit_reserve_mm':reserve,'fallback_fit_reserve_mm':max(5.,reserve),'fit_phase_offsets_s':list(shifts),'minimum_output_clamped_in_production':geometry.minimum_output_clamped}
