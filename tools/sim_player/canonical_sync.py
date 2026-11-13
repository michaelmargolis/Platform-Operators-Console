"""Offline B+C mapping to a ride's canonical video. No motion arrays are changed.

The player clock remains the selected recording's clock. Video PTS is a mapped
presentation, not a second driver for filters, Twin output or loop boundaries.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import math
import numpy as np
from .media_trajectory import (MediaError, check_cancel, compare_identity,
                                read_trajectory, recording_key)
from .canonical_media import CanonicalMedia, load_media

GOOD = frozenset(('xyz','orientation','stationary_time','stale'))
BAD = frozenset(('lost','lost_hold','reacquiring','invalid_observation','waiting'))


def recording_media_key(recording):
    """Candidate gains/cue/platform do not create duplicate video assets/maps."""
    dynamic=getattr(recording,'dynamic_profile',None)
    if dynamic:
        return ('dynamic',dynamic.get('capture_timing_sha256'),
                dynamic['profile']['identity'].get('reference_sha256'),
                tuple(recording.times),
                tuple((f.analysis.get('dynamic',{}).get('phase_s'),
                       f.analysis.get('dynamic',{}).get('matched_phase_s'),
                       f.analysis.get('dynamic',{}).get('status')) for f in recording.frames))
    path=Path(recording.csv_path)
    stat=path.stat()
    return ('capture',str(path.resolve()),stat.st_size,stat.st_mtime_ns)


@dataclass(frozen=True)
class MediaSample:
    available: bool
    video_s: float | None
    canonical_phase_s: float | None
    status: str
    source: str


@dataclass
class MediaBinding:
    media: CanonicalMedia
    times: np.ndarray
    phase: np.ndarray
    valid: np.ndarray
    statuses: tuple[str,...]
    source: str
    summary: dict
    warnings: tuple[str,...] = ()
    source_key: object = None
    max_sample_gap_s: float = .25

    def sample(self, seconds):
        t=float(seconds)
        if not math.isfinite(t) or t<self.times[0]-1e-6 or t>self.times[-1]+1e-6:
            return MediaSample(False,None,None,'outside recorded coverage',self.source)
        i=int(np.clip(np.searchsorted(self.times,t,side='right')-1,0,len(self.times)-1))
        j=min(i+1,len(self.times)-1)
        exact=abs(t-self.times[i])<1e-6 or j==i
        okay=bool(self.valid[i])
        if not exact:
            okay=okay and bool(self.valid[j]) and self.times[j]-self.times[i]<=self.max_sample_gap_s
        if not okay:
            return MediaSample(False,None,None,'unmatched / telemetry gap',self.source)
        f=0. if exact else (t-self.times[i])/(self.times[j]-self.times[i])
        phase=float(self.phase[i] if exact else self.phase[i]+f*(self.phase[j]-self.phase[i]))
        if not math.isfinite(phase):
            return MediaSample(False,None,None,'unmatched phase',self.source)
        video=float(self.media.video_time(phase))
        if video<0:
            return MediaSample(False,None,phase,'before video starts',self.source)
        status='stationary timing estimate' if 'stationary_time' in (self.statuses[i],self.statuses[j]) else self.statuses[i]
        return MediaSample(True,video,phase,status,self.source)

    def first_available_time(self):
        """First selected-recording time that maps to a real video frame."""
        for t, phase, valid in zip(self.times, self.phase, self.valid):
            if not valid or not math.isfinite(float(phase)):
                continue
            if self.media.video_time(float(phase)) >= 0:
                return float(t)
        return None

    def rate(self, seconds, window=.30):
        """Local precomputed media slope, not frame-by-frame decoder seeking."""
        t=float(seconds)
        a=self.sample(max(float(self.times[0]),t-window))
        b=self.sample(min(float(self.times[-1]),t+window))
        dt=min(float(self.times[-1]),t+window)-max(float(self.times[0]),t-window)
        if not (a.available and b.available) or dt<1e-6: return 1.
        return max(0.,min(4.,(b.video_s-a.video_s)/dt))


def match_trajectory(media, time, xyz, quat, speed, frames, *, first_motion, cancel=None, progress=None):
    from tools.nl2_sync.matcher import Matcher
    ref=media.trajectory.reference()
    times=np.asarray(time,dtype=float)
    phase=np.full(len(times),np.nan)
    valid=np.zeros(len(times),dtype=bool)
    residual=np.full(len(times),np.nan)
    status=[]
    matcher=Matcher(ref,initial_phase=0.)
    previous=-math.inf
    for i,t in enumerate(times):
        if i%100==0:
            check_cancel(cancel)
            if progress: progress(f'Matching video reference: {i}/{len(times)} observations')
        live=float(t-first_motion)
        if live<0:
            # Before departure, use the common first-motion anchor only where
            # the pose is compatible. It is explicitly NOT a spatial time cue.
            p=live
            if ref.time[0]<=p<=ref.time[-1]:
                k,f=ref.sample_position(p)
                error=float(np.linalg.norm(ref.xyz[k]+f*ref.delta[k]-xyz[i]))
                qref=ref.quat[k]+f*ref.dq[k]; qref/=np.linalg.norm(qref)
                qlive=np.asarray(quat[i]);qlive=qlive/np.linalg.norm(qlive)
                angle=math.degrees(2*math.acos(np.clip(abs(qref@qlive),0,1)))
                valid[i]=error<=.15 and angle<=2.
                phase[i]=p;residual[i]=error
            status.append('stationary_time')
            continue
        result=matcher.update(xyz[i],quat[i],live,int(frames[i]),float(speed[i]))
        p=result.phase_s
        okay=(result.status in GOOD and ref.time[0]-1e-6<=p<=ref.time[-1]+1e-6)
        # Very small floating differences may be flattened; a material
        # backward jump is a loss of presentation alignment, not a video rewind.
        if p<previous-.05: okay=False
        if okay:
            p=max(previous,p);previous=p
        phase[i]=p; valid[i]=okay
        residual[i]=result.position_residual_m if result.position_residual_m is not None else np.nan
        status.append(result.status)
    moving=(np.abs(np.asarray(speed))>=.2)&(times>=first_motion)
    coverage=float(np.mean(valid[moving])) if moving.any() else 0.
    finite=residual[np.isfinite(residual)&moving&valid]
    if coverage<.90:
        raise MediaError(f'Reference route is incompatible or incomplete: only {coverage:.1%} of moving samples matched. '
                         'New video capture required, or select the correct video/telemetry pair. Plot review remains available.')
    return phase,valid,tuple(status),{
        'moving_match_fraction':coverage,'matched_samples':int(np.sum(valid)),
        'sample_count':len(times),'unmatched_samples':int(np.sum(~valid)),
        'stationary_estimate_samples':status.count('stationary_time'),
        'position_residual_p95_m':float(np.percentile(finite,95)) if len(finite) else None,
        'position_residual_max_m':float(np.max(finite)) if len(finite) else None,
        'interpretation':'Spatial consistency, not measured video latency or independent timing accuracy',
    }


def _interpolate_map(times, phase, valid, statuses, query):
    times=np.asarray(times);query=np.asarray(query,dtype=float)
    idx=np.clip(np.searchsorted(times,query,side='right')-1,0,len(times)-1)
    nxt=np.minimum(idx+1,len(times)-1)
    exact=np.abs(query-times[idx])<=1e-6
    gap=times[nxt]-times[idx]
    good=(valid[idx] & (valid[nxt]|exact) & ((gap<=.25)|exact) &
          (query>=times[0]-1e-6)&(query<=times[-1]+1e-6)&np.isfinite(query))
    f=np.divide(query-times[idx],gap,out=np.zeros_like(query),where=gap>0)
    f=np.clip(f,0,1)
    out=np.where(exact, phase[idx], phase[idx]+f*(phase[nxt]-phase[idx]))
    states=tuple(statuses[int(i)] for i in idx)
    return out,good,states


def bind_recording(recording, *, root=None, media=None, cancel=None, progress=None):
    key=recording_key(recording)
    if media is None:
        media=load_media(key) if root is None else load_media(key,root)
    if media is None: return None
    check_cancel(cancel)
    dynamic=getattr(recording,'dynamic_profile',None)
    if not dynamic:
        trace=read_trajectory(recording.csv_path,park_key=key,cancel=cancel)
        warnings=compare_identity(media.trajectory.identity,trace.identity)
        if trace.source_hash==media.trajectory.source_hash:
            phase=trace.phase.copy();valid=np.ones(len(phase),dtype=bool)
            states=('same_capture',)*len(phase)
            summary={'moving_match_fraction':1.,'sample_count':len(phase),'unmatched_samples':0,
                     'source':'Original reference capture: direct calibrated timestamps'}
            source='original reference capture'
        else:
            phase,valid,states,summary=match_trajectory(media,trace.time,trace.xyz,trace.quat,trace.speed,trace.frame,
                                                      first_motion=trace.first_motion,cancel=cancel,progress=progress)
            source='B+C from selected raw telemetry'
        return MediaBinding(media,trace.time,phase,valid,states,source,summary,
                            tuple(media.trajectory.warnings)+warnings,recording_media_key(recording))

    from tools.nl2_sync.profile import DynamicProfile,load_reference
    profile=DynamicProfile.load(dynamic['profile_path'])
    reference=load_reference(dynamic['profile_path'],profile)
    warnings=compare_identity(media.trajectory.identity,profile.identity)
    rp,rv,rs,summary=match_trajectory(media,reference.time,reference.xyz,reference.quat,reference.speed,
                                    np.arange(len(reference.time)),first_motion=0.,cancel=cancel,progress=progress)
    lookup=[];source_ok=[];upstream_status=[];used_archived_phase=False
    last_good_time=None
    for frame in recording.frames:
        d=frame.analysis['dynamic']
        t=float(d['ride_time_s'])
        matched=d.get('matched_phase_s')
        if t<0:
            value=t;state='stationary_time';okay=True
        else:
            if matched is None:
                matched=d.get('phase_s');used_archived_phase=True
            value=float(matched) if matched is not None else math.nan
            state=str(d.get('status','waiting'))
            if state in ('xyz','orientation','stationary_time') and frame.analysis.get('valid',True):
                last_good_time=t;okay=True
            else:
                okay=(state=='stale' and last_good_time is not None and t-last_good_time<=.25)
        lookup.append(value);source_ok.append(okay);upstream_status.append(state)
    phase,valid,states=_interpolate_map(reference.time,rp,rv,rs,lookup)
    valid &= np.asarray(source_ok,dtype=bool)
    states=list('stationary_time' if 'stationary_time' in (a,b) else a for a,b in zip(states,upstream_status))
    # Phase-1 references usually start at motion zero, while diagnostic ticks
    # include a short pre-roll. First-motion-relative time may describe that
    # pre-roll only when the canonical pose is still compatible with the bound
    # reference start. This remains an explicitly unobservable timing estimate.
    canonical=media.trajectory.reference()
    preroll_estimates=0
    for i, value in enumerate(lookup):
        if not math.isfinite(value) or value>=0 or not canonical.time[0]<=value<=canonical.time[-1]:
            continue
        k,f=canonical.sample_position(value)
        point=canonical.xyz[k]+f*canonical.delta[k]
        q=canonical.quat[k]+f*canonical.dq[k];q/=np.linalg.norm(q)
        angle=math.degrees(2*math.acos(np.clip(abs(float(q@reference.quat[0])),0,1)))
        if np.linalg.norm(point-reference.xyz[0])<=.15 and angle<=2.:
            phase[i]=value;valid[i]=True;states[i]='stationary_time';preroll_estimates+=1
    states=tuple(states)
    summary=dict(summary,selected_tick_count=len(phase),selected_unmatched_ticks=int(np.sum(~valid)),
                 preroll_estimate_ticks=preroll_estimates)
    if used_archived_phase:
        warnings=warnings+('Saved study uses its archived B+C profile progress; raw target residual is unavailable. '
                            'Reference-to-video mapping is checked, but this is not fresh target-telemetry verification.',)
    source='saved B+C progress -> profile reference -> video reference' if used_archived_phase else 'raw B+C match -> video reference'
    return MediaBinding(media,np.asarray(recording.times),phase,valid,states,source,summary,
                        tuple(media.trajectory.warnings)+warnings,recording_media_key(recording))
