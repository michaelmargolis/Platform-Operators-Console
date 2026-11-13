"""Causal B+C: local XYZ segment projection, quaternion fallback at low speed.

Reference arrays may look ahead (they are a prerecorded ride). Live observations
are processed one at a time, with no knowledge of the live run's next sample.
NL2 XYZ is in metres, quaternion order x,y,z,w. There is no global track search.
"""
from __future__ import annotations
from dataclasses import dataclass
import math
import numpy as np


@dataclass(frozen=True)
class Settings:
    window_s: float = 2.0
    position_tolerance_m: float = 1.0
    orientation_tolerance_deg: float = 20.0
    stationary_speed_mps: float = 0.2
    stationary_rotation_deg_s: float = 1.0
    stationary_hold_position_tolerance_m: float = 0.10
    max_reference_gap_s: float = 0.25
    max_stationary_observation_gap_s: float = 0.25
    lost_grace_s: float = 0.5
    resync_window_s: float = 8.0
    resync_after_s: float = 0.5
    resync_interval_s: float = 0.5
    recovery_confirmations: int = 2
    profile_max_rate: float = 2.5

    def __post_init__(self):
        for name, value in vars(self).items():
            if not math.isfinite(value) or value <= 0:
                raise ValueError(name + " must be positive and finite")
        if self.resync_window_s < self.window_s:
            raise ValueError("Resync window must not be narrower than the local window")


class Reference:
    def __init__(self, time_s, xyz, quaternion, speed=None):
        self.time = np.asarray(time_s,dtype=float)
        self.xyz = np.asarray(xyz,dtype=float)
        q = np.array(quaternion,dtype=float,copy=True)
        n=len(self.time)
        if n<2 or self.xyz.shape!=(n,3) or q.shape!=(n,4):
            raise ValueError("Reference needs at least two matching time/XYZ/quaternion samples")
        if not all(np.isfinite(x).all() for x in (self.time,self.xyz,q)) or np.any(np.diff(self.time)<=0):
            raise ValueError("Reference values must be finite, times strictly increasing")
        norm=np.linalg.norm(q,axis=1)
        if np.any(norm<.1):raise ValueError("Invalid reference quaternion")
        q/=norm[:,None]
        for i in range(1,n):
            if np.dot(q[i-1],q[i])<0:q[i]*=-1
        self.quat=q
        self.dt=np.diff(self.time)
        self.delta=np.diff(self.xyz,axis=0)
        self.length2=np.einsum('ij,ij->i',self.delta,self.delta)
        self.dq=np.diff(q,axis=0)
        self.q_length2=np.einsum('ij,ij->i',self.dq,self.dq)
        self.speed=np.asarray(speed,dtype=float) if speed is not None else np.r_[np.linalg.norm(self.delta,axis=1)/self.dt,0.]
        if self.speed.shape!=(n,) or not np.isfinite(self.speed).all():raise ValueError("Invalid reference speed")

    @classmethod
    def from_trace(cls, trace):
        return cls(trace.time-trace.meta['marks']['first_motion'],trace.xyz,trace.quat,trace.speed)

    @property
    def bytes(self):
        return sum(getattr(self,name).nbytes for name in ('time','xyz','quat','dt','delta','length2','dq','q_length2','speed'))

    def sample_position(self, phase):
        i=int(np.clip(np.searchsorted(self.time,phase,side='right')-1,0,len(self.time)-2))
        f=float(np.clip((phase-self.time[i])/self.dt[i],0,1))
        return i,f


@dataclass
class Match:
    phase_s: float
    profile_phase_s: float
    reference_index: float
    status: str
    position_residual_m: float | None
    orientation_residual_deg: float | None
    phase_correction_s: float
    confidence: str
    candidates: int
    resync_used: bool = False


class Matcher:
    def __init__(self, reference: Reference, settings: Settings | None=None, initial_phase=0.):
        self.ref=reference;self.cfg=settings or Settings()
        self.phase=float(np.clip(initial_phase,reference.time[0],reference.time[-1]))
        self.profile_phase=self.phase
        self.last_time=None;self.last_frame=None;self.last_q=None
        self.last_trusted_phase=self.phase;self.last_trusted_time=None
        self.lost_since=None;self.last_resync=-math.inf
        self.pending_phase=None;self.pending_count=0

    def _nearest(self, point, q, center, radius, predicted, stationary, quiet):
        r,c=self.ref,self.cfg
        lo=max(float(r.time[0]),center-radius);hi=min(float(r.time[-1]),center+radius)
        a=max(0,int(np.searchsorted(r.time,lo,side='right')-1))
        b=min(len(r.time)-1,int(np.searchsorted(r.time,hi,side='left')+1))
        idx=np.arange(a,b)
        if not len(idx):return None,0
        use_orientation=stationary & (np.abs(r.speed[idx])<c.stationary_speed_mps)
        p=point-r.xyz[idx]
        f=np.einsum('ij,ij->i',p,r.delta[idx])/np.maximum(r.length2[idx],1e-15)
        # Project onto the short, sign-continuous quaternion chord. Normalizing
        # afterwards is adequate at 20Hz; always check the resulting residual.
        signs=np.where(r.quat[idx]@q<0,-1.,1.)
        qlive=signs[:,None]*q
        fq=np.einsum('ij,ij->i',qlive-r.quat[idx],r.dq[idx])/np.maximum(r.q_length2[idx],1e-15)
        # Tiny quaternion noise during a stationary hold is not a phase cue.
        # Treat low-rate orientation segments as unobservable and use only the
        # time prediction within a pose-compatible hold, not the closest noise.
        angular_rate=np.rad2deg(4*np.arcsin(np.clip(np.sqrt(r.q_length2[idx])/2,0,1)))/r.dt[idx]
        flat=use_orientation & (angular_rate<c.stationary_rotation_deg_s)
        f=np.where(use_orientation,fq,f)
        f=np.where(flat,(predicted-r.time[idx])/r.dt[idx],f)
        f=np.clip(f,0.,1.)
        phase=r.time[idx]+f*r.dt[idx]
        phase=np.clip(phase,lo,hi)
        f=(phase-r.time[idx])/r.dt[idx]
        projection=r.xyz[idx]+f[:,None]*r.delta[idx]
        p_err=np.linalg.norm(projection-point,axis=1)
        qi=r.quat[idx]+f[:,None]*r.dq[idx]
        qi/=np.maximum(np.linalg.norm(qi,axis=1)[:,None],1e-15)
        angles=np.rad2deg(2*np.arccos(np.clip(np.abs(qi@q),0,1)))
        angle_tol=np.where(use_orientation & quiet, .5,c.orientation_tolerance_deg)
        valid=(p_err<=c.position_tolerance_m)&(angles<=angle_tol)&(r.dt[idx]<=c.max_reference_gap_s)
        # Do not claim a timestamp lying on the search boundary is a confident
        # match to a moving train further outside it. This is a confidence
        # check, not track-topology/disambiguation code.
        edge=((phase<=lo+1e-7)&(lo>r.time[0]+1e-7))|((phase>=hi-1e-7)&(hi<r.time[-1]-1e-7))
        valid &= ~edge
        score=np.where(use_orientation,(angles/5.)**2+.05*p_err**2,p_err**2+.0001*angles**2)
        # For a quiet hold, orientation residual below the compatibility gate
        # cannot identify a unique time. Prefer the predicted time over matching
        # meaningless float noise to an earlier stationary sample.
        quiet_hold=flat & quiet
        score=np.where(quiet_hold,(phase-predicted)**2,score)
        score+=1e-6*(phase-predicted)**2
        score[~valid]=np.inf
        # A long stopped train can finish centimetres short of the reference
        # stop. Pure nearest-XYZ scoring then repeatedly picks an exact point
        # on the preceding *moving* segment and pins profile time at arrival.
        # During a quiet live hold, prefer a nearby, pose-compatible *stopped*
        # reference segment at the predicted time. Only extrapolate time within
        # such a segment; do not use this rule for motion or real rotations.
        quiet_candidate=(quiet_hold & valid
                         & (p_err <= min(c.stationary_hold_position_tolerance_m,
                                         c.position_tolerance_m))
                         & (np.abs(phase-predicted) <= c.max_reference_gap_s))
        if np.any(quiet_candidate):
            score[~quiet_candidate]=np.inf
        j=int(np.argmin(score))
        if not np.isfinite(score[j]):return None,len(idx)
        kind='orientation' if use_orientation[j] and not flat[j] else 'stationary_time' if use_orientation[j] else 'xyz'
        return (float(phase[j]),float(p_err[j]),float(angles[j]),kind),len(idx)

    def update(self, xyz, quaternion, elapsed_s, frame=None, speed=None):
        now=float(elapsed_s)
        if not math.isfinite(now) or (self.last_time is not None and now<self.last_time):
            raise ValueError("Live observation clock must not go backwards")
        dt=0. if self.last_time is None else now-self.last_time
        self.last_time=now
        if self.last_trusted_time is None:self.last_trusted_time=now
        point=np.asarray(xyz,dtype=float);q=np.asarray(quaternion,dtype=float)
        if point.shape!=(3,) or q.shape!=(4,) or not np.isfinite(point).all() or not np.isfinite(q).all() or np.linalg.norm(q)<.1:
            return self._lost(now,dt,'invalid_observation',0)
        q=q/np.linalg.norm(q)
        if frame is not None and self.last_frame==frame:
            # Do not advance an unobservable profile on a repeated NL2 frame.
            # Also do NOT lose the timestamp of the last accepted observation:
            # the next fresh stationary observation must account for these ticks.
            return self._result(self.phase,dt,'stale',None,None,0.,'unobservable',0,freeze=True)
        self.last_frame=frame
        angle_change=0. if self.last_q is None else math.degrees(2*math.acos(np.clip(abs(float(q@self.last_q)),0,1)))
        self.last_q=q.copy()
        stationary=speed is not None and abs(float(speed))<self.cfg.stationary_speed_mps
        prediction_step=dt
        if stationary and self.lost_since is None:
            # During a pose-compatible quiet hold, time is the ONLY phase cue.
            # The service loop may tick with no new NL2 frame: self.last_time
            # then advances, but self.last_trusted_time does not. Predict from
            # the last ACCEPTED observation, not merely the preceding tick.
            observation_gap=max(0.,now-self.last_trusted_time)
            # An extended gap may be a genuine simulator/telemetry stall.
            # Never silently count that whole gap as simulated ride progress.
            prediction_step=(observation_gap
                             if observation_gap<=self.cfg.max_stationary_observation_gap_s
                             else 0.)
        predicted=float(np.clip(self.phase+prediction_step,self.ref.time[0],self.ref.time[-1]))
        found,n=self._nearest(point,q,self.last_trusted_phase,self.cfg.window_s,predicted,stationary,angle_change<.05)
        resync=False
        if found is None and self.lost_since is not None and now-self.lost_since>=self.cfg.resync_after_s and now-self.last_resync>=self.cfg.resync_interval_s:
            self.last_resync=now;resync=True
            found,n=self._nearest(point,q,self.last_trusted_phase,self.cfg.resync_window_s,predicted,stationary,angle_change<.05)
        if found is None:return self._lost(now,dt,'lost',n)
        phase,pe,qe,kind=found
        if self.lost_since is not None:
            # Two temporally consistent observations before reacquisition; the
            # broad local search is repeated while a candidate is pending.
            if self.pending_phase is not None and abs(phase-self.pending_phase)<=max(.25,4*dt):
                self.pending_count+=1
            else:self.pending_count=1
            self.pending_phase=phase
            if self.pending_count<self.cfg.recovery_confirmations:
                self.last_resync=-math.inf
                return self._lost(now,dt,'reacquiring',n)
            self.lost_since=None;self.pending_count=0;self.pending_phase=None
            status='reacquired_'+kind
        else:status=kind
        correction=phase-predicted
        self.last_trusted_phase=phase;self.last_trusted_time=now;self.phase=phase
        return self._result(phase,dt,status,pe,qe,correction,
                            'low' if kind=='stationary_time' else 'matched',n,resync)

    def _lost(self,now,dt,status,n):
        if self.lost_since is None:self.lost_since=now
        age=now-self.lost_since
        # Short explicitly flagged time fallback; then HOLD. The next window
        # stays tied to last verified trajectory position, not wall clock drift.
        phase=min(float(self.ref.time[-1]),self.last_trusted_phase+min(age,self.cfg.lost_grace_s))
        self.phase=phase
        mode=status if status=='reacquiring' else ('lost_time' if age<self.cfg.lost_grace_s else 'lost_hold')
        return self._result(phase,dt,mode,None,None,0.,'lost',n)

    def _result(self,phase,dt,status,pe,qe,correction,confidence,n,resync=False,freeze=False):
        if not freeze:
            # Expose both the raw match and a bounded-slew profile lookup phase.
            limit=self.cfg.profile_max_rate*dt
            self.profile_phase+=float(np.clip(phase-self.profile_phase,-limit,limit))
        i,f=self.ref.sample_position(phase)
        return Match(phase,self.profile_phase,i+f,status,pe,qe,correction,confidence,n,resync)


class UnityProfile:
    """Exercise interpolation of SIX gain channels; no motion optimization yet."""
    def __init__(self,reference):
        self.ref=reference
        self.gains=np.ones((len(reference.time),6),dtype=float)

    def lookup(self,phase):
        i,f=self.ref.sample_position(phase)
        return tuple(self.gains[i]+f*(self.gains[i+1]-self.gains[i]))
