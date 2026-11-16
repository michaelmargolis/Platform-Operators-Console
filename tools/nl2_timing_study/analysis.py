# analysis.py ver 1
"""Offline repeatability analysis. NumPy only; never connects to any device.

Landmarks are ordered crossings of planes normal to a reference route. They
are matched using position AND travel direction, not timing alignment. This
avoids using a time-warping fit to claim that time drift is small. Samples with
large gaps and ambiguous crossings are rejected, not silently interpolated.
"""
from __future__ import annotations
from collections import defaultdict
import csv
from dataclasses import dataclass
import html
import json
import math
from pathlib import Path
from typing import Any
import numpy as np
from .common import SCHEMA, finite, sha256, stamp, unique_id, write_json, write_table
from .system_info import vr_from_states


@dataclass
class Trace:
    path: Path
    meta: dict
    rows: list[dict]
    time: np.ndarray
    xyz: np.ndarray
    quat: np.ndarray
    speed: np.ndarray
    distance: np.ndarray
    raw: list[dict]
    issues: list[str]
    summary: dict

    @property
    def machine(self):
        return self.meta.get("machine", {}).get("host") or self.meta.get("machine", {}).get("label", "unknown")

    @property
    def name(self):
        stem = self.path.stem
        return stem[:-6] if stem.endswith("_trial") else stem


def stats(values):
    a = np.asarray([x for x in values if x is not None and np.isfinite(x)], dtype=float)
    if not len(a):
        return {k: None for k in ("n", "median", "p95_abs", "max_abs", "min", "max", "std")}
    return dict(n=len(a), median=float(np.median(a)),
                p95_abs=float(np.percentile(np.abs(a), 95)), max_abs=float(np.max(np.abs(a))),
                min=float(a.min()), max=float(a.max()), std=float(a.std()))


def read_rows(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def safe_companion(meta_path, name):
    if not isinstance(name, str) or not name or Path(name).name != name:
        raise ValueError("Companion must be a basename in the same trial folder")
    p = (meta_path.parent / name).resolve()
    if p.parent != meta_path.parent.resolve() or p.is_symlink():
        raise ValueError("Unsafe companion path")
    return p


def read_trial(path, verify_hashes=True, max_gap=.25):
    path = Path(path).resolve()
    meta = json.loads(path.read_text(encoding="utf-8"))
    if meta.get("schema") != SCHEMA:
        raise ValueError("Not a timing-study v1 trial")
    files = meta.get("files", {})
    raw_path = safe_companion(path, files.get("timing"))
    if verify_hashes:
        expected = meta.get("file_sha256", {}).get("timing")
        if expected and sha256(raw_path) != expected:
            raise ValueError("Timing CSV hash does not match its trial metadata")
    raw = read_rows(raw_path)
    issues = list(meta.get("quality_issues", []))
    if meta.get("status") != "complete":
        issues.append("incomplete_trial")
    if meta.get("warmup"):
        issues.append("warmup_excluded")
    marks = meta.get("marks", {})
    required = ("dispatch_tx", "first_motion", "view_train_departed", "view_train_returned")
    if any(finite(marks.get(k)) is None for k in required):
        issues.append("missing_lifecycle_marks")
    motion = finite(marks.get("first_motion"))
    end = finite(marks.get("view_train_returned"))
    start = finite(marks.get("dispatch_tx"))
    if motion is not None and start is not None and end is not None and not start <= motion < end:
        issues.append("invalid_lifecycle_order")
    # Retain raw rows in report metrics, but use one observation per source frame
    # for geometry. Do not count repeated frozen frames as independent evidence.
    rows, previous, previous_t = [], None, -math.inf
    duplicates = 0
    equal_times = 0
    backwards_times = 0
    previous_receipt = -math.inf
    for row in raw:
        t = finite(row.get("elapsed_s"))
        if t is None or motion is None or end is None or not motion <= t <= end:
            continue
        try:
            frame = int(row["frame"])
            numeric = [float(row[k]) for k in ("pos_x", "pos_y", "pos_z", "quat_x", "quat_y", "quat_z", "quat_w", "speed")]
            state = int(row["state"])
            if not all(math.isfinite(v) for v in numeric):
                raise ValueError()
            if t < previous_receipt:
                backwards_times += 1
                issues.append("backwards_receipts")
                continue
            tied = (t == previous_receipt)
            previous_receipt = t
            if tied:
                equal_times += 1
            if not state & 1 or state == 7:
                issues.append("invalid_play_state")
        except (KeyError, TypeError, ValueError):
            issues.append("invalid_telemetry")
            continue
        if frame == previous:
            duplicates += 1
            continue
        if previous is not None and frame < previous and not (previous > 0xF0000000 and frame < 0x10000000):
            issues.append("frame_reset")
        # Keep the first observation of a timestamp; never manufacture time.
        # Later tied packets remain in raw logs/transport statistics.
        previous = frame
        if t == previous_t:
            continue
        previous_t = t
        rows.append(row)
    t = np.array([float(r["elapsed_s"]) for r in rows])
    xyz = np.array([[float(r[k]) for k in ("pos_x", "pos_y", "pos_z")] for r in rows]).reshape((-1, 3))
    quat = np.array([[float(r[k]) for k in ("quat_x", "quat_y", "quat_z", "quat_w")] for r in rows]).reshape((-1, 4))
    speed = np.array([float(r["speed"]) for r in rows])
    d = np.r_[0., np.cumsum(np.linalg.norm(np.diff(xyz, axis=0), axis=1))] if len(t) else np.array([])
    if len(rows) < 20:
        issues.append("too_few_moving_samples")
    if len(t) and (np.any(np.linalg.norm(quat, axis=1) < .1) or (d[-1] < 10)):
        issues.append("invalid_or_short_trajectory")
    dt = np.diff(t)
    gaps = dt[dt > max_gap]
    active_raw = [r for r in raw if finite(r.get("elapsed_s")) is not None
                  and motion is not None and end is not None
                  and motion <= float(r["elapsed_s"]) <= end]
    receipt_times = np.array([float(r["elapsed_s"]) for r in active_raw])
    rtt = stats([finite(r.get("rtt_ms")) for r in active_raw])
    qdelay = stats([finite(r.get("queue_delay_ms")) for r in active_raw])
    vr = vr_from_states(r.get("state") for r in active_raw)
    summary = dict(trial=str(path), ride=meta.get("ride"), park=meta.get("park_key"),
                   machine=meta.get("machine", {}).get("host") or meta.get("machine", {}).get("label", "unknown"),
                   vr_observed=vr["state"], vr_on_samples=vr["headset_on_samples"],
                   vr_off_samples=vr["headset_off_samples"],
                   repeat=meta.get("repeat"), warmup=meta.get("warmup", False),
                   status=meta.get("status"), motion_to_return_s=(end-motion if end is not None and motion is not None else None),
                   dispatch_to_motion_s=(motion-start if motion is not None and start is not None else None),
                   departure_observation_delay_s=(marks.get("view_train_departed", motion)-motion if motion is not None else None),
                   samples=len(rows), replies=len(active_raw), duplicate_replies=duplicates,
                   equal_timestamp_observations=equal_times, backwards_timestamp_observations=backwards_times,
                   median_unique_dt_ms=float(np.median(dt)*1000) if len(dt) else None,
                   p95_unique_dt_ms=float(np.percentile(dt,95)*1000) if len(dt) else None,
                   max_unique_gap_ms=float(dt.max()*1000) if len(dt) else None,
                   gaps_above_threshold=len(gaps), gap_threshold_s=max_gap,
                   median_reply_dt_ms=float(np.median(np.diff(receipt_times))*1000) if len(receipt_times)>1 else None,
                   median_rtt_ms=rtt["median"], p95_rtt_ms=rtt["p95_abs"],
                   p95_queue_delay_ms=qdelay["p95_abs"], path_distance_m=float(d[-1]) if len(d) else None,
                   nl2_version=meta.get("nl2_version"), view=json.dumps(meta.get("view_identity")),
                   station=json.dumps(meta.get("departure_station")),
                   conditions=meta.get("conditions", ""), error=meta.get("error", ""))
    # Report transport/frame-rate observations, not inferred simulation FPS.
    if len(rows)>1:
        ids = np.array([int(r["frame"]) for r in rows], dtype=np.int64)
        increments = np.diff(ids)
        increments[increments < 0] += 2**32
        summary["observed_frame_counter_hz"] = float(increments.sum()/(t[-1]-t[0])) if t[-1] > t[0] else None
    if "polls" in files:
        try:
            polls = read_rows(safe_companion(path, files["polls"]))
            summary["skipped_poll_ticks"] = sum(int(r["skipped_ticks"]) for r in polls)
            summary["p95_poll_lateness_ms"] = stats([finite(r["lateness_ms"]) for r in polls])["p95_abs"]
        except (OSError, ValueError, KeyError):
            summary["poll_log_error"] = True
    issues = sorted(set(issues))
    summary["issues"] = "; ".join(issues)
    summary["eligible"] = not issues
    return Trace(path, meta, rows, t, xyz, quat, speed, d, raw, issues, summary)


def stationary_sections(trace, speed_threshold=.5, minimum_s=.5):
    """Describe waits/rotations where route distance is a poor phase clock."""
    out = []
    indices = np.flatnonzero(np.abs(trace.speed) < speed_threshold)
    if not len(indices):
        return out
    chunks = np.split(indices, np.flatnonzero(np.diff(indices)>1)+1)
    for chunk in chunks:
        a, b = int(chunk[0]), int(chunk[-1])
        duration = float(trace.time[b]-trace.time[a])
        if duration < minimum_s:
            continue
        angle = max(quaternion_angle(trace.quat[a], q) for q in trace.quat[a:b+1])
        motion = trace.meta["marks"]["first_motion"]
        out.append(dict(park=trace.meta["park_key"], machine=trace.machine, trial=trace.name,
                        start_phase_s=float(trace.time[a]-motion), end_phase_s=float(trace.time[b]-motion),
                        duration_s=duration, orientation_excursion_deg=float(angle),
                        position_excursion_m=float(np.linalg.norm(trace.xyz[a:b+1]-trace.xyz[a],axis=1).max()),
                        stationary_rotation=bool(angle>2.)))
    return out


def group_key(trace):
    m = trace.meta
    # Separate track/seat/train/start differences rather than silently mixing
    # a racing track or adjacent station with a different view trajectory.
    return (m.get("park_key"), m.get("park_sha256") or "HASH_UNKNOWN",
            tuple(m.get("view_identity") or ()), tuple(m.get("departure_station") or ()),
            trace.summary.get("vr_observed", "unknown"))


def quaternion_angle(a, b):
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na < .1 or nb < .1:
        return math.inf
    dot = np.clip(abs(float(np.dot(a/na,b/nb))), 0., 1.)
    return math.degrees(2*math.acos(dot))


def interp_quat(a, b, fraction):
    # Sign-correct normalized interpolation; exact arc interpolation is not
    # needed for the rejection gate at these short sample intervals.
    if np.dot(a,b) < 0:
        b = -b
    q = (1-fraction)*a + fraction*b
    n = np.linalg.norm(q)
    return q/n if n else q


def landmarks(reference, target, spacing=10., max_gap=.25, position_tolerance=1.0):
    """Return independently matched route checkpoints and rejection counters."""
    if len(reference.time)<2 or len(target.time)<2:
        return [], {"attempted": 0, "matched": 0, "coverage": 0.0}
    rd, td = reference.distance, target.distance
    rdt, tdt = np.diff(reference.time), np.diff(target.time)
    tv = np.diff(target.xyz, axis=0)
    tvnorm = np.linalg.norm(tv, axis=1)
    counters = dict(attempted=0, matched=0, ambiguous=0, missing=0, reference_gap=0, reference_stationary=0)
    matched = []
    previous_target_t = -math.inf
    # Exclude the stationary ends; match directed route planes through moving
    # samples only. Arc length chooses reference checkpoints, not their times.
    for s in np.arange(spacing, max(spacing, rd[-1]-spacing), spacing):
        counters["attempted"] += 1
        i = min(len(rd)-2, max(0, int(np.searchsorted(rd, s, side="right")-1)))
        ds = rd[i+1]-rd[i]
        if rdt[i] > max_gap:
            counters["reference_gap"] += 1
            continue
        if ds < 1e-6 or abs(reference.speed[i]) < .5:
            counters["reference_stationary"] += 1
            continue
        f = (s-rd[i])/ds
        point = reference.xyz[i] + f*(reference.xyz[i+1]-reference.xyz[i])
        direction = (reference.xyz[i+1]-reference.xyz[i])/ds
        rt = reference.time[i]+f*rdt[i]
        rq = interp_quat(reference.quat[i], reference.quat[i+1], f)
        # Approximate progress limits disambiguate geometrically crossing track.
        # No timing window is used; large true drift remains measurable.
        progress = td[:-1]/td[-1]
        projected = (target.xyz-point) @ direction
        dot = tv @ direction
        candidates = np.flatnonzero(
            (projected[:-1] <= 0) & (projected[1:] >= 0)
            & (dot > 1e-7) & (tvnorm > 1e-7)
            & (dot/np.maximum(tvnorm,1e-7) > .5)
            & (tdt > 0) & (tdt <= max_gap)
            & (np.abs(progress-s/rd[-1]) <= .08))
        options = []
        for j in candidates:
            u = -projected[j]/dot[j]
            tt = target.time[j]+u*tdt[j]
            if tt <= previous_target_t:
                continue
            tp = target.xyz[j]+u*tv[j]
            err = float(np.linalg.norm(tp-point))
            angle = quaternion_angle(rq, interp_quat(target.quat[j], target.quat[j+1], u))
            if err <= position_tolerance and angle <= 30.:
                options.append((tt, err, angle, j, u))
        # Consecutive segments meeting at the same vertex are not two routes.
        unique = []
        for option in sorted(options):
            if not unique or abs(option[0]-unique[-1][0]) > .001:
                unique.append(option)
        if len(unique)>1:
            counters["ambiguous"] += 1
            continue
        if not unique:
            counters["missing"] += 1
            continue
        tt, err, angle, j, u = unique[0]
        previous_target_t = tt
        counters["matched"] += 1
        matched.append(dict(reference_time_s=float(rt), target_time_s=float(tt),
                            reference_distance_m=float(s),
                            target_distance_m=float(td[j]+u*(td[j+1]-td[j])),
                            position_error_m=err, orientation_error_deg=angle,
                            reference_sample_interval_s=float(rdt[i]),
                            target_sample_interval_s=float(tdt[j])))
    counters["coverage"] = counters["matched"]/counters["attempted"] if counters["attempted"] else 0.
    return matched, counters


def compare(reference, target, spacing=10., max_gap=.25, position_tolerance=1., anchor_every=5., budget=.2):
    hits, coverage = landmarks(reference, target, spacing, max_gap, position_tolerance)
    result: dict[str, Any] = dict(park=reference.meta["park_key"], reference=reference.name,
                               target=target.name, reference_machine=reference.machine,
                               target_machine=target.machine, landmarks=coverage["matched"],
                               coverage=coverage["coverage"], budget_s=budget)
    rm, tm = reference.meta["marks"], target.meta["marks"]
    result["duration_difference_s"] = target.summary["motion_to_return_s"]-reference.summary["motion_to_return_s"]
    result["dispatch_to_motion_difference_s"] = target.summary["dispatch_to_motion_s"]-reference.summary["dispatch_to_motion_s"]
    result["path_length_difference_pct"] = 100*(target.distance[-1]/reference.distance[-1]-1)
    warnings = []
    if not reference.meta.get("park_sha256") or not target.meta.get("park_sha256"):
        warnings.append("park content hash missing; identical park version not established")
    if reference.meta.get("source_hashes") != target.meta.get("source_hashes"):
        warnings.append("capture/adapter source versions differ")
    if reference.meta.get("nl2_version") != target.meta.get("nl2_version"):
        warnings.append("NoLimits versions differ")
    if reference.meta.get("conditions") != target.meta.get("conditions"):
        warnings.append("declared rendering/test conditions differ")
    if not reference.meta.get("conditions") or not target.meta.get("conditions"):
        warnings.append("rendering/test conditions were not documented")
    for setting in ("hz", "first_motion_threshold_mps", "first_motion_confirmation_frames"):
        if reference.meta.get(setting) != target.meta.get(setting):
            warnings.append(f"capture setting differs: {setting}")
    if abs(result["path_length_difference_pct"]) > 2:
        warnings.append("path length differs by over 2%; check route or sampling")
    sufficient = coverage["matched"] >= 10 and coverage["coverage"] >= .8
    if not sufficient:
        warnings.append("insufficient unambiguous landmark coverage")
    if any(x["stationary_rotation"] for trace in (reference, target) for x in stationary_sections(trace)):
        warnings.append("stationary rotation present: moving-route landmarks do not assess synchronization inside it")
    result["sufficient_coverage"] = sufficient
    if not hits:
        result["warnings"] = "; ".join(warnings)
        return result, [], coverage
    rphase = np.array([h["reference_time_s"]-rm["first_motion"] for h in hits])
    tphase = np.array([h["target_time_s"]-tm["first_motion"] for h in hits])
    delta = tphase-rphase
    # Offline fit is a diagnostic LOWER COMPLEXITY MODEL, not a deployable
    # predictor. It uses the entire target run; report it as hindsight only.
    slope, intercept = np.polyfit(rphase, tphase, 1) if len(hits)>1 else (1., float(delta[0]))
    result["best_fit_clock_ratio"] = float(slope)
    result["best_fit_offset_s"] = float(intercept)
    result["constant_offset_s"] = float(np.median(delta))
    result["drift_end_minus_start_s"] = float(np.median(delta[-min(3,len(delta)):])-np.median(delta[:min(3,len(delta))]))
    last_anchor_offset, last_anchor_phase = 0., 0.
    for h, rp, tp in zip(hits, rphase, tphase):
        h.update(reference_phase_s=float(rp), target_phase_s=float(tp),
                 dispatch_clock_error_s=float((h["target_time_s"]-tm["dispatch_tx"])
                     -(h["reference_time_s"]-rm["dispatch_tx"])),
                 motion_clock_error_s=float(tp-rp),
                 constant_offset_error_s=float(tp-np.median(delta)-rp),
                 affine_error_s=float((tp-intercept)/slope-rp) if slope>0 else None,
                 previous_landmark_error_s=float(tp-last_anchor_offset-rp),
                 distance_phase_error_s=float(np.interp(h["target_distance_m"],
                     reference.distance, reference.time-rm["first_motion"])-rp))
        # Score BEFORE updating at the current landmark. No zero-error cheating
        # by measuring against a checkpoint just used to correct the clock.
        reanchor = bool(rp-last_anchor_phase >= anchor_every)
        h["reanchor_after_measurement"] = int(reanchor)
        if reanchor:
            last_anchor_offset, last_anchor_phase = float(tp-rp), float(rp)
    for label in ("dispatch_clock", "motion_clock", "constant_offset", "affine", "previous_landmark", "distance_phase"):
        st = stats([h[label+"_error_s"] for h in hits])
        result[label+"_p95_abs_s"] = st["p95_abs"]
        result[label+"_max_abs_s"] = st["max_abs"]
    result["within_motion_clock_budget"] = bool(sufficient and result["motion_clock_max_abs_s"]<=budget)
    result["warnings"] = "; ".join(warnings)
    for h in hits:
        h.update(park=result["park"], reference=reference.name, target=target.name,
                 reference_machine=reference.machine, target_machine=target.machine)
    return result, hits, coverage


def markdown_table(rows, columns):
    def cell(x):
        if x is None:
            return "-"
        if isinstance(x, float):
            return f"{x:.4f}"
        return str(x).replace("|", "/").replace("\n", " ")
    return "\n".join(["| " + " | ".join(columns) + " |",
                     "| " + " | ".join("---" for _ in columns) + " |"]
                    + ["| " + " | ".join(cell(row.get(k)) for k in columns) + " |" for row in rows])


def html_table(rows, columns):
    def cell(x):
        return html.escape("-" if x is None else f"{x:.4f}" if isinstance(x,float) else str(x))
    return ("<table><thead><tr>"+"".join(f"<th>{html.escape(c)}</th>" for c in columns)
            +"</tr></thead><tbody>"+"".join("<tr>"+"".join(f"<td>{cell(row.get(k))}</td>" for k in columns)+"</tr>" for row in rows)+"</tbody></table>")


def analyse_paths(inputs, output, reference_machine=None, spacing=10., max_gap=.25,
                  position_tolerance=1., anchor_every=5., budget=.2, verify_hashes=True,
                  reference_policy="representative"):
    paths = set()
    for arg in inputs:
        p = Path(arg).resolve()
        if not p.exists():
            raise FileNotFoundError(p)
        if p.is_file() and p.name.endswith("_trial.json"):
            paths.add(p)
        elif p.is_file() and p.name == "session.json":
            paths.update(p.parent.rglob("*_trial.json"))
        elif p.is_dir():
            paths.update(p.rglob("*_trial.json"))
    if not paths:
        raise ValueError("No *_trial.json files found. Legacy CSVs need lifecycle/timing evidence and are not treated as equivalent.")
    output = Path(output).resolve()
    if output.exists():
        # Re-analysis must not overwrite an earlier report.
        output = output / unique_id("report")
    output.mkdir(parents=True, exist_ok=False)
    traces, rejected, duplicates = [], [], []
    seen_content = set()
    for path in sorted(paths):
        try:
            trace = read_trial(path, verify_hashes, max_gap)
            signature = (trace.meta.get("started_utc"), trace.machine,
                         trace.meta.get("file_sha256", {}).get("timing") or sha256(safe_companion(path, trace.meta["files"]["timing"])))
            if signature in seen_content:
                duplicates.append(str(path))
                continue
            seen_content.add(signature)
            traces.append(trace)
        except (ValueError, KeyError, OSError, TypeError) as exc:
            rejected.append(dict(trial=str(path), reason=f"{type(exc).__name__}: {exc}"))
    groups = defaultdict(list)
    for trace in traces:
        if not trace.issues:
            groups[group_key(trace)].append(trace)
    comparisons, checkpoint_rows, group_rows = [], [], []
    for key, items in sorted(groups.items(), key=lambda item: str(item[0])):
        items.sort(key=lambda t: (t.meta.get("started_utc", ""), str(t.path)))
        preferred = [t for t in items if t.machine == reference_machine] if reference_machine else []
        # Use the first qualified reference, never silently cherry-pick the
        # least variable repeat. Each reference is named in every output.
        from .quality import choose_reference
        reference = choose_reference(items, reference_machine, reference_policy)
        group_rows.append(dict(park=key[0], park_hash=key[1], view=json.dumps(key[2]),
                               departure_station=json.dumps(key[3]),
                               complete_measured_runs=len(items), reference=reference.name,
                               reference_machine=reference.machine, reference_policy=reference_policy,
                               note=("requested reference machine absent" if reference_machine and not preferred else "")
                                    or ("only one comparable run; repeatability unknown" if len(items)<2 else "")))
        for trace in items:
            if trace is reference:
                continue
            result, hits, _ = compare(reference, trace, spacing, max_gap, position_tolerance, anchor_every, budget)
            comparisons.append(result)
            checkpoint_rows.extend(hits)
    slow_sections = [row for t in traces if not t.issues for row in stationary_sections(t)]
    run_rows = [t.summary for t in traces]
    write_table(output/"runs.csv", run_rows, None if run_rows else ["trial", "status"])
    write_table(output/"stationary_sections.csv", slow_sections,
                None if slow_sections else ["park", "trial", "duration_s", "orientation_excursion_deg"])
    write_table(output/"comparisons.csv", comparisons, None if comparisons else ["park", "target"])
    write_table(output/"landmarks.csv", checkpoint_rows, None if checkpoint_rows else ["park", "reference_time_s", "target_time_s"])
    write_table(output/"groups.csv", group_rows, None if group_rows else ["park", "reference"])
    envelope_groups = defaultdict(list)
    for h in checkpoint_rows:
        envelope_groups[(h["park"], h["reference"], h["reference_distance_m"])].append(h)
    envelopes = []
    for key, hits in sorted(envelope_groups.items()):
        errors = [0.] + [h["motion_clock_error_s"] for h in hits]
        envelopes.append(dict(park=key[0], reference=key[1], reference_distance_m=key[2],
                              reference_phase_s=hits[0]["reference_phase_s"],
                              runs_including_reference=len(errors),
                              min_motion_clock_error_s=min(errors), max_motion_clock_error_s=max(errors),
                              observed_pairwise_spread_s=max(errors)-min(errors)))
    write_table(output/"landmark_envelopes.csv", envelopes,
                None if envelopes else ["park", "reference_distance_m", "observed_pairwise_spread_s"])
    write_table(output/"rejected_files.csv", rejected, ["trial", "reason"])
    summary_by_machine = []
    machine_groups = defaultdict(list)
    for t in traces:
        if not t.issues:
            machine_groups[(t.meta["park_key"], t.machine, group_key(t)[1:])].append(t)
    for key, runs in sorted(machine_groups.items(), key=lambda item: str(item[0])):
        st = stats([r.summary["motion_to_return_s"] for r in runs])
        summary_by_machine.append(dict(park=key[0], machine=key[1],
            vr_observed=runs[0].summary.get("vr_observed", "unknown"), runs=len(runs),
            median_duration_s=st["median"], duration_spread_s=st["max"]-st["min"],
            duration_std_s=st["std"], median_dispatch_to_motion_s=stats([r.summary["dispatch_to_motion_s"] for r in runs])["median"],
            median_p95_rtt_ms=stats([r.summary["p95_rtt_ms"] for r in runs])["median"],
            median_p95_queue_ms=stats([r.summary["p95_queue_delay_ms"] for r in runs])["median"]))
    write_table(output/"machine_summary.csv", summary_by_machine, None if summary_by_machine else ["park", "machine"])
    config = dict(spacing_m=spacing, max_interpolation_gap_s=max_gap, position_tolerance_m=position_tolerance,
                  anchor_interval_s=anchor_every, clock_budget_s=budget, reference_machine=reference_machine,
                  reference_policy=reference_policy, tool_version="2.1.0")
    report = dict(schema=SCHEMA, generated_utc=stamp(), parameters=config,
                  runs=run_rows, groups=group_rows, comparisons=comparisons,
                  rejected_files=rejected, duplicate_copies_ignored=duplicates,
                  machine_summary=summary_by_machine, landmark_envelopes=envelopes, stationary_sections=slow_sections)
    write_json(output/"report.json", report)
    comparison_cols = ["park", "target_machine", "target", "coverage", "duration_difference_s",
                       "dispatch_clock_max_abs_s", "motion_clock_max_abs_s", "previous_landmark_max_abs_s",
                       "distance_phase_max_abs_s", "sufficient_coverage"]
    machine_cols = ["park", "machine", "vr_observed", "runs", "median_duration_s", "duration_spread_s",
                    "median_dispatch_to_motion_s", "median_p95_rtt_ms", "median_p95_queue_ms"]
    excluded_rows = [r for r in run_rows if not r["eligible"]]
    notes = [
        "Equal receipt timestamps are retained in raw evidence and collapsed for interpolation only. Backwards timestamps remain errors. Gaps are reported, not whole-run exclusions.",
        "Representative reference is the recording nearest median duration (ties: fewer gaps, then filename), within the requested PC if present. This differs from v1 first-run selection; use --reference-policy first to reproduce that policy.",
        "All errors are seconds of reference ride phase at independently matched route landmarks; smaller is better. Positive error means the clock is ahead of the observed ride position.",
        "Dispatch clock uses the actual DISPATCH packet, not the operator Start request. Motion clock starts at first sustained movement (three distinct frames, timestamp backdated to the first).",
        "Return time is the adapter's confirmed station-arrival observation, with station polling delay. Interior route landmarks give better evidence of drift than end time alone.",
        "Previous-landmark correction is a causal replay simulation: errors are measured BEFORE correcting at the current landmark. It uses earlier spatial checkpoints, not a future best-fit warp. This is not installed in live POC.",
        "Moving-route checkpoints do not validate phase inside stationary rotations (for example a tilt mechanism). stationary_sections.csv describes slow sections and quaternion excursions; full raw orientation is retained for a later orientation-aware synchronizer.",
        "Distance phase integrates observed 3D position increments and looks up reference progress, without normalizing by the future final distance. Chord error, missed samples and stationary waits can make it unsuitable.",
        "Constant-offset and affine fits in comparisons.csv are hindsight-only diagnostics. They cannot establish accuracy of an unmeasured future ride. A gain/bias learned on all runs has not been held-out validated here.",
        "No source simulation timestamp is exposed by the current protocol. Receive time, request RTT and adapter queue delay are measured on each PC's monotonic clock; clocks are never subtracted across PCs. Frame IDs are NOT a simulation clock or a proof of FPS.",
        f"Interpolation rejects sample gaps over {max_gap:g}s, ambiguous crossings, spatial errors over {position_tolerance:g}m and large orientation disagreement. At least 10 matches and 80% checkpoint coverage are needed for the 'sufficient' flag.",
        f"The {budget:g}s clock budget is a configurable screening value, NOT a validated safe motion-profile tolerance. Small timestamp error can matter where pose changes rapidly. Look at maximum errors and coverage, not only averages.",
        "A complete sample of five runs is evidence for those observed conditions, not a worst-case guarantee. Incomplete, paused, changed-view, invalid and warmup trials remain listed but are excluded from timing comparisons.",
        "Observed VR headset status comes from NL2 telemetry state bit 3 (0x08), not command-line labels. The report groups VR-on and VR-off traces separately, and mixed status warrants inspection. Keep NL2 version, park files, view/car/seat, rendering/VSync, power plan and background load comparable.",
        "Exact park hashes, train/view identities and departure stations form separate groups. Different source versions and declared conditions are warnings. Repeated copies of the same recording are not additional trials.",
        "landmark_envelopes.csv gives the observed minimum/maximum clock errors and pairwise spread at each matched checkpoint, including the reference run. A smaller count means some runs did not match that checkpoint; missing coverage is not zero drift.",
        "Reports describe synchronization only. They neither generate nor approve angular-limiting profiles; kinematic limits and a live fallback remain necessary.",
    ]
    intro = (f"{len(run_rows)} unique trials read; {sum(r['eligible'] for r in run_rows)} complete, measured, quality-qualified; "
             f"{len(excluded_rows)} excluded; {len(rejected)} unreadable; {len(duplicates)} duplicate copies ignored. "
             f"{len(comparisons)} reference comparisons.")
    md = ["# NL2 timing/repeatability report", intro, "## PC / ride summary", markdown_table(summary_by_machine, machine_cols),
          "## Synchronization comparison", markdown_table(comparisons, comparison_cols),
          "## Group coverage", markdown_table(group_rows, ["park", "complete_measured_runs", "reference_machine", "view", "departure_station", "note"]),
          "## Stationary / slow sections", markdown_table(slow_sections, ["park", "machine", "trial", "start_phase_s", "duration_s", "orientation_excursion_deg", "stationary_rotation"]),
          "## Excluded trials", markdown_table(excluded_rows, ["park", "machine", "repeat", "status", "issues", "error"]),
          "## Warnings", markdown_table([c for c in comparisons if c.get("warnings")], ["park", "target", "warnings"]),
          "## Read errors", markdown_table(rejected, ["trial", "reason"]),
          "## Interpretation and limitations", "\n\n".join(notes)]
    (output/"report.md").write_text("\n\n".join(md)+"\n", encoding="utf-8")
    doc = ('<!doctype html><html lang="en"><meta charset="utf-8"><title>NL2 timing report</title>'
           '<style>body{font:15px system-ui;margin:30px;line-height:1.5}table{border-collapse:collapse;font-size:13px;display:block;overflow:auto}th,td{padding:7px;border:1px solid #aaa;text-align:left}th{background:#eee}h2{margin-top:32px}p{max-width:1100px}</style>'
           '<h1>NL2 timing / repeatability</h1><p>'+html.escape(intro)+'</p>')
    for title, rows, cols in [("PC / ride summary", summary_by_machine, machine_cols),
                              ("Synchronization comparison", comparisons, comparison_cols),
                              ("Group coverage", group_rows, ["park", "complete_measured_runs", "reference_machine", "view", "departure_station", "note"]),
                              ("Stationary / slow sections", slow_sections, ["park", "machine", "trial", "start_phase_s", "duration_s", "orientation_excursion_deg", "stationary_rotation"]),
                              ("Excluded trials", excluded_rows, ["park", "machine", "repeat", "status", "issues", "error"]),
                              ("Warnings", [c for c in comparisons if c.get("warnings")], ["park", "target", "warnings"]),
                              ("Read errors", rejected, ["trial", "reason"])]:
        doc += '<h2>'+title+'</h2>'+html_table(rows,cols)
    doc += '<h2>Interpretation and limitations</h2>'+''.join('<p>'+html.escape(n)+'</p>' for n in notes)+'</html>'
    (output/"report.html").write_text(doc, encoding="utf-8")
    from .report_extra import extend_report
    extend_report(output, traces, groups, comparisons, checkpoint_rows, budget, max_gap)
    return output
