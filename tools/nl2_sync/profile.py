"""Validated, offline research motion profiles. No hardware or file I/O per tick.

Six non-negative multipliers are applied AFTER TelemetryConfig.process and
BEFORE norm_to_real. Progress is seconds along a particular immutable reference,
not wall time. Smoothstep interpolation is C1 and cannot overshoot its knots.
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import numpy as np

SCHEMA = 'poc.nl2.dynamic-profile/1'
AXES = ('surge', 'sway', 'heave', 'roll', 'pitch', 'yaw')
STAGE = 'post_washout_pre_norm_to_real'


def digest_json(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode('utf-8')).hexdigest()


def weights(times, phase):
    """Indices/weights for exactly the same interpolation used by scalar lookup."""
    t = np.asarray(times, dtype=float)
    x = np.asarray(phase, dtype=float)
    if not np.isfinite(x).all():
        raise ValueError('Nonfinite reference progress')
    i = np.clip(np.searchsorted(t, x, side='right') - 1, 0, len(t)-2)
    u = np.clip((x-t[i])/(t[i+1]-t[i]), 0., 1.)
    return i, u*u*(3.-2.*u)


def interpolate(times, gains, phase):
    i, w = weights(times, phase)
    g = np.asarray(gains, dtype=float)
    return g[i]*(1.-w[..., None]) + g[i+1]*w[..., None]


@dataclass
class DynamicProfile:
    time: np.ndarray
    gains: np.ndarray
    fallback: np.ndarray
    identity: dict
    metadata: dict

    def __post_init__(self):
        self.time = np.array(self.time, dtype=float, copy=True)
        self.gains = np.array(self.gains, dtype=float, copy=True)
        self.fallback = np.array(self.fallback, dtype=float, copy=True)
        if self.time.ndim != 1 or len(self.time)<2 or self.gains.shape != (len(self.time),6):
            raise ValueError('Profile needs >=2 time knots and six gains at each knot')
        if self.fallback.shape != (6,):
            raise ValueError('Profile requires six conservative fallback multipliers')
        if not all(np.isfinite(a).all() for a in (self.time,self.gains,self.fallback)):
            raise ValueError('Profile values must be finite')
        if self.time[0] != 0. or np.any(np.diff(self.time)<=0):
            raise ValueError('Profile starts at zero and reference times increase strictly')
        if np.any(self.gains<0) or np.any(self.gains>2) or np.any(self.fallback<0) or np.any(self.fallback>2):
            raise ValueError('Research multiplier range is 0..2, not unlimited amplification')
        required = ('park','platform','reference_sha256','processing_sha256')
        if any(not isinstance(self.identity.get(k),str) or not self.identity[k] for k in required):
            raise ValueError('Incomplete profile identity')
        if self.identity['platform'] not in ('chair_v3','platform_v2'):
            raise ValueError('Unsupported platform')
        for k in ('reference_sha256','processing_sha256'):
            value=self.identity[k]
            if len(value)!=64 or any(c not in '0123456789abcdef' for c in value):
                raise ValueError('Invalid SHA256 identity: '+k)
        if 'processing' in self.metadata and digest_json(self.metadata['processing'])!=self.identity['processing_sha256']:
            raise ValueError('Processing metadata fingerprint does not match profile identity')
        # Snapshot mutable dictionaries so callers cannot alter the schema in place.
        self.identity=json.loads(json.dumps(self.identity, allow_nan=False))
        self.metadata=json.loads(json.dumps(self.metadata, allow_nan=False))
        for a in (self.time,self.gains,self.fallback):a.flags.writeable=False

    def lookup(self, phase):
        if not math.isfinite(float(phase)):
            raise ValueError('Nonfinite reference progress')
        return tuple(float(x) for x in interpolate(self.time,self.gains,float(phase)))

    def lookup_many(self, phase):
        return interpolate(self.time,self.gains,phase)

    def assert_compatible(self, identity):
        for key,value in self.identity.items():
            if identity.get(key)!=value:
                raise ValueError('Profile compatibility mismatch: '+key)

    def to_dict(self):
        data={'schema':SCHEMA,'axes':list(AXES),'application_stage':STAGE,
                'interpolation':'smoothstep_c1','status':'offline_research_only',
                'anticipation_s':0.,'identity':self.identity,
                'reference_time_s':self.time.tolist(),'multipliers':self.gains.tolist(),
                'fallback_multipliers':self.fallback.tolist(),'metadata':self.metadata}
        data['content_sha256']=digest_json(data)
        return data

    def save(self, path):
        # Exclusive write: never replace an original profile or a live catalogue.
        p=Path(path)
        with p.open('x',encoding='utf-8') as f:
            json.dump(self.to_dict(),f,indent=2,allow_nan=False)
            f.write('\n')

    @classmethod
    def load(cls, path, *, identity=None):
        p=Path(path)
        if p.stat().st_size>10_000_000:
            raise ValueError('Unreasonably large profile')
        data=json.loads(p.read_text(encoding='utf-8'))
        if (data.get('schema')!=SCHEMA or data.get('axes')!=list(AXES)
                or data.get('application_stage')!=STAGE
                or data.get('interpolation')!='smoothstep_c1'
                or data.get('status')!='offline_research_only'
                or data.get('anticipation_s')!=0.):
            raise ValueError('Unsupported profile schema/stage; not a static ride variant')
        checksum=data.pop('content_sha256',None)
        if checksum!=digest_json(data):
            raise ValueError('Profile content checksum mismatch; regenerate/revalidate edited profiles')
        result=cls(data['reference_time_s'],data['multipliers'],data['fallback_multipliers'],
                   data['identity'],data.get('metadata',{}))
        if identity is not None:result.assert_compatible(identity)
        return result


class ProfilePlayback:
    """Offline candidate fallback policy, to be qualified before live integration.

    Smoothly crossfade to a separately validated static fallback during loss or
    excessive observation age. Quiet holds are intentionally usable. A repeated
    frame holds lookup progress in Matcher; it is not itself a reason to fallback.
    """
    def __init__(self, profile, transition_s=.25, max_age_s=.25):
        if not 0<transition_s<=5 or not 0<max_age_s<=5:raise ValueError('Invalid fallback settings')
        self.profile=profile;self.transition_s=float(transition_s);self.max_age_s=float(max_age_s)
        self.blend=0.;self.held=np.array(profile.fallback,copy=True);self.last_time=None

    def update(self, phase, status, now, observation_age=0.):
        now=float(now)
        if not math.isfinite(now) or (self.last_time is not None and now<self.last_time):
            raise ValueError('Playback clock must be finite and monotonic')
        dt=0. if self.last_time is None else now-self.last_time
        self.last_time=now
        usable=(phase is not None and math.isfinite(float(phase)) and now>=0 and
                status in ('xyz','orientation','stationary_time','stale') and
                math.isfinite(float(observation_age)) and 0<=observation_age<=self.max_age_s and
                -.25<=phase<=self.profile.time[-1]+.25)
        if usable:self.held=np.asarray(self.profile.lookup(phase))
        self.blend=float(np.clip(self.blend+(dt/self.transition_s)*(1 if usable else -1),0.,1.))
        b=self.blend*self.blend*(3.-2.*self.blend)
        return tuple((1.-b)*self.profile.fallback+b*self.held)


def load_reference(profile_path, profile):
    """Load the bound reference safely; relocating a complete bundle is allowed."""
    from .matcher import Reference
    base=Path(profile_path).resolve().parent
    name=profile.metadata.get('reference_file')
    if not isinstance(name,str) or Path(name).name!=name:
        raise ValueError('Reference must be a basename beside the profile')
    path=base/name
    if path.is_symlink() or path.resolve().parent!=base or path.stat().st_size>100_000_000:
        raise ValueError('Unsafe or oversized reference file')
    if hashlib.sha256(path.read_bytes()).hexdigest()!=profile.identity['reference_sha256']:
        raise ValueError('Reference SHA256 mismatch')
    with np.load(path,allow_pickle=False) as data:
        return Reference(data['time_s'],data['xyz_m'],data['quaternion_xyzw'],data['speed_mps'])
