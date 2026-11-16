# quality.py ver 1
"""Additional diagnostics; these never remove inconvenient complete runs."""
from __future__ import annotations
import math
import numpy as np
from .common import finite


def choose_reference(items, machine=None, policy="representative"):
    pool = [t for t in items if t.machine == machine] if machine else []
    pool = pool or list(items)
    if policy == "first":
        return min(pool, key=lambda t: (t.meta.get("started_utc", ""), str(t.path)))
    median = float(np.median([t.summary["motion_to_return_s"] for t in pool]))
    # Median duration is a declared, repeatable heuristic, NOT the run whose
    # errors happen to be smallest. Interrupted runs remain in every comparison.
    return min(pool, key=lambda t: (abs(t.summary["motion_to_return_s"]-median),
                                   t.summary.get("gaps_above_threshold", 0), str(t.path)))


def interruptions(trace, threshold=.25):
    rows = trace.raw
    motion = trace.meta.get("marks", {}).get("first_motion")
    end = trace.meta.get("marks", {}).get("view_train_returned")
    if motion is None or end is None:
        return []
    out = []
    previous = None
    for r in rows:
        t = finite(r.get("elapsed_s"))
        if t is None or not motion <= t <= end:
            continue
        if previous:
            pt = float(previous["elapsed_s"])
            gap = t-pt
            if gap > threshold:
                step = (int(r["frame"])-int(previous["frame"])) % 2**32
                rtt = finite(r.get("rtt_ms"))
                dx = math.sqrt(sum((float(r[k])-float(previous[k]))**2 for k in ("pos_x","pos_y","pos_z")))
                classification = "delayed_observation"
                if step <= 2 and rtt is not None and rtt >= gap*500:
                    classification = "possible_server_progress_pause"
                elif step > 2:
                    classification = "source_advanced_during_gap"
                out.append(dict(park=trace.meta["park_key"], machine=trace.machine,
                    trial=trace.name, start_phase_s=pt-motion, end_phase_s=t-motion,
                    gap_s=gap, source_frame_increment=step, position_delta_m=dx,
                    rtt_ms=rtt, classification=classification,
                    note="Protocol evidence only; not proof of a visible/rendering freeze"))
        previous = r
    return out


def raw_angles(trace):
    """Same Euler decomposition as POC, before cue scaling; NL2 axis convention."""
    q = trace.quat / np.linalg.norm(trace.quat, axis=1)[:,None]
    x,y,z,w = q.T
    roll = np.unwrap(np.arctan2(2*(x*y+w*z), 1-2*(x*x+z*z)))
    pitch = np.arcsin(np.clip(2*(w*x-y*z),-1,1))
    return np.rad2deg(np.column_stack((roll,pitch)))


def angular_events(trace, rate_threshold=15., separation=1., max_per_axis=12, max_gap=.25):
    """Offline notable roll/pitch changes, with values BEFORE each event.

    Euler pitch folds at +/-90 degrees; full quaternion rotation is separately
    assessed for stationary tilt. These are diagnostic features, not cues.
    """
    if len(trace.time)<3:
        return []
    angles = raw_angles(trace)
    dt = np.diff(trace.time)
    good = (dt>0)&(dt<=max_gap)
    rates = np.divide(np.diff(angles,axis=0),dt[:,None],out=np.zeros((len(dt),2)),where=good[:,None])
    phase = trace.time-trace.meta["marks"]["first_motion"]
    events = []
    for axis,name in enumerate(("roll","pitch")):
        accepted=[]
        for i in np.argsort(-np.abs(rates[:,axis])):
            if len(accepted)>=max_per_axis or abs(rates[i,axis])<rate_threshold:
                break
            center=(phase[i]+phase[i+1])/2
            if any(abs(center-e)<separation for e in accepted):
                continue
            accepted.append(center)
            events.append(dict(park=trace.meta["park_key"], machine=trace.machine,
                trial=trace.name, axis=name, phase_s=float(center), sample_index=int(i+1),
                rate_deg_s=float(rates[i,axis]),
                value_before_500ms_deg=float(np.interp(center-.5,phase,angles[:,axis])),
                value_before_250ms_deg=float(np.interp(center-.25,phase,angles[:,axis])),
                value_at_event_deg=float(np.interp(center,phase,angles[:,axis])),
                sample_interval_s=float(dt[i])))
    return sorted(events,key=lambda e:e["phase_s"])


def compare_angular(reference, target, hits, max_gap=.25):
    """Match event orientation near independently matched route time.

    This is hindsight feature analysis, never a claim of causal tracking.
    It exposes residual and sample interval; no match is reported across a gap.
    """
    if not hits:
        return []
    rphase = reference.time-reference.meta["marks"]["first_motion"]
    tphase = target.time-target.meta["marks"]["first_motion"]
    rt=np.array([h["reference_phase_s"] for h in hits]);tt=np.array([h["target_phase_s"] for h in hits])
    ta=raw_angles(target); ra=raw_angles(reference)
    results=[]
    for e in angular_events(reference,max_gap=max_gap):
        rp=e["phase_s"]; predicted=float(np.interp(rp,rt,tt))
        axis=0 if e["axis"]=="roll" else 1
        i=int(e["sample_index"])
        # Use a 3D plane checkpoint's timing only to find the local event, then
        # locate the same signed angular-value crossing in that small window.
        value=e["value_at_event_deg"]; desired_sign=np.sign(e["rate_deg_s"])
        options=[]
        for j in np.flatnonzero((tphase[:-1]>=predicted-1.5)&(tphase[:-1]<=predicted+1.5)):
            dt=tphase[j+1]-tphase[j]
            if dt<=0 or dt>max_gap:continue
            a,b=ta[j,axis],ta[j+1,axis]
            # Roll unwrap origin may differ by a full turn between recordings.
            v=value+360*round((a-value)/360) if axis==0 else value
            if (b-a)*desired_sign <= 1e-8:continue
            u=(v-a)/(b-a)
            if 0<=u<=1:
                tp=float(tphase[j]+u*dt)
                options.append((abs(tp-predicted),tp,float(dt)))
        row={k:e[k] for k in ("park","axis","phase_s","rate_deg_s","value_before_500ms_deg","value_before_250ms_deg","value_at_event_deg")}
        row.update(reference=reference.name,target=target.name,target_machine=target.machine,
                   method="offline signed-angle crossing near XYZ checkpoint alignment",matched=False)
        if options:
            options.sort()
            # Multiple equally plausible crossings are not forced into a match.
            if len(options)==1 or options[1][0]-options[0][0]>.05:
                _,tp,dt=options[0]
                row.update(matched=True,target_phase_s=tp,motion_clock_error_s=tp-rp,
                           target_sample_interval_s=dt)
        results.append(row)
    return results


def recommendation(runs, comparisons, budget=.2):
    parks=sorted({t.meta["park_key"] for t in runs})
    out=[]
    for park in parks:
        valid=[t for t in runs if t.meta["park_key"]==park and not t.issues]
        cs=[c for c in comparisons if c["park"]==park]
        reasons=[]
        if len(valid)<3:reasons.append("fewer than three comparable completed runs")
        if not cs or any(not c.get("sufficient_coverage") for c in cs):reasons.append("insufficient landmark coverage")
        if any(c.get("motion_clock_max_abs_s",0)>budget for c in cs):reasons.append("time-only drift exceeds screening budget")
        if any(t.summary["gaps_above_threshold"] for t in valid):reasons.append("gaps need local review, not run deletion")
        out.append(dict(park=park,complete_runs=len(valid),
                        recommendation="extend_or_review" if reasons else "screening_repeatable",
                        reasons="; ".join(reasons),
                        suggested_additional_runs=max(0,5-len(valid)) if reasons else 0,
                        note="Screening only; additional repetitions cannot guarantee absence of rare hitches"))
    return out


def stationary_rotation_events(trace, speed_threshold=.5, excursion_deg=1., max_gap=.25):
    """Observed tilt threshold crossing, not an exact physical rotation onset.

    Require three distinct samples beyond one degree relative to the beginning
    of a low-speed section. Report the bracketing interval and gaps explicitly.
    No ride-specific Tilt Coaster command or motion policy is introduced.
    """
    if len(trace.time)<4:return []
    indices=np.flatnonzero(np.abs(trace.speed)<speed_threshold)
    chunks=np.split(indices,np.flatnonzero(np.diff(indices)>1)+1) if len(indices) else []
    q=trace.quat/np.linalg.norm(trace.quat,axis=1)[:,None]
    phase=trace.time-trace.meta['marks']['first_motion']
    out=[]
    for chunk in chunks:
        if len(chunk)<4:continue
        a,b=int(chunk[0]),int(chunk[-1])
        if phase[b]-phase[a]<.5:continue
        angles=np.rad2deg(2*np.arccos(np.clip(np.abs(q[a:b+1]@q[a]),0,1)))
        peak=float(angles.max())
        if peak<5:continue
        start=None
        for k in range(1,len(angles)-2):
            if np.all(angles[k:k+3]>=excursion_deg):start=k;break
        if start is None:continue
        i=a+start
        threshold_time=float(phase[i]);bracket=float(phase[i]-phase[i-1])
        j98=int(np.flatnonzero(angles>=.98*peak)[0])+a
        out.append(dict(park=trace.meta['park_key'],machine=trace.machine,trial=trace.name,
            stationary_start_phase_s=float(phase[a]),stationary_end_phase_s=float(phase[b]),
            tilt_threshold_deg=excursion_deg,threshold_previous_phase_s=float(phase[i-1]),
            tilt_threshold_phase_s=threshold_time,threshold_bracket_s=bracket,
            threshold_observed=bool(bracket<=max_gap),peak_excursion_deg=peak,
            first_98pct_excursion_phase_s=float(phase[j98]),
            position_excursion_m=float(np.linalg.norm(trace.xyz[a:b+1]-trace.xyz[a],axis=1).max()),
            internal_gaps=int(np.sum(np.diff(trace.time[a:b+1])>max_gap)),
            note='Offline 1-degree excursion crossing confirmed by three samples; not exact onset, not a live detector'))
    return out
