# optimizer.py ver 2
"""Batch offline NL2 ride optimisation; no Qt, simulator or hardware output.

From the POC root:
    py -m tools.sim_player.optimizer captures/nl2_ride_study --output studies

Open exported candidate CSVs normally in Sim Player. Keep each *_optimizer.json
beside its CSV. Normalised source telemetry is NEVER replaced by processed data.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from copy import deepcopy
import csv
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
from pathlib import Path
import re
import shutil
import sys
import time

import numpy as np

from config.platforms import PLATFORMS
from sims.nolimits2 import ride_config
from sims.nolimits2.angular_cues import normalize_quaternion
from sims.nolimits2.variant_store import VariantStore
from tools.analyze_nl2_csv import read_capture, make_backend
from .optimization_search import (AXES, Capture, Evaluation, Evaluator, Parameters,
                                  SearchOptions, motion_amplitudes, priority_scores,
                                  rotation_evidence, search_candidates)
from .platform_selection import platform_metadata
from .recording import load_recording
from .study_bundle import (SCHEMA, bundle_path, checked_global_settings,
                           model_fingerprint, sha256_file)
from .tuning import (ExperimentSettings, current_poc_global_settings,
                     save_experiment, validate_gain_percent)
from .variant_presets import candidate_entry

EPS = 1.e-7
DEFAULT_PLATFORMS = ("chair_v3", "platform_v2")


def read_input(path: Path) -> Capture:
    """Strict telemetry parsing. Never infer the physical park from a filename."""
    path = Path(path).resolve()
    source_digest = sha256_file(path)
    with path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        fields = list(reader.fieldnames or [])
        if len(set(fields)) != len(fields):
            raise ValueError("Duplicate CSV column names")
        if "opt_schema" in fields:
            raise ValueError("Already an optimiser study, not an original capture")
        originals = list(reader)
    rows = read_capture(path)
    if len(rows) != len(originals) or sha256_file(path) != source_digest:
        raise ValueError("Capture changed while being read")
    rides = {str(row.get("ride", "")).strip() for row in originals}
    if len(rides) != 1 or not next(iter(rides)):
        raise ValueError("Capture must identify one physical park in every ride field")
    ride = next(iter(rides))
    park = ride_config.park_key_for_capture(ride)
    if park is None:
        raise ValueError(f"Unrecognised physical park {ride!r}")
    if rows[0][0] < 0:
        raise ValueError("Capture elapsed_s must not be negative")
    quaternions = None
    quaternion_issue = None
    qfields = ("quat_x", "quat_y", "quat_z", "quat_w")
    if all(f in fields for f in qfields):
        try:
            quaternions = [normalize_quaternion(tuple(float(row[f]) for f in qfields))
                           for row in originals]
        except (KeyError, TypeError, ValueError) as exc:
            quaternion_issue = f"Invalid quaternion series: {exc}"
            quaternions = None
    rotation = rotation_evidence(quaternions)
    if quaternion_issue:
        rotation["reason"] = quaternion_issue + "; retain current Euler cue only"
    return Capture(path, park, ride, rows, fields, originals, source_digest, quaternions, rotation)


def discover_inputs(paths: list[Path], *, exclude_root: Path | None = None) -> tuple[list[Path], list[dict]]:
    """Recursive directories or explicit files; auxiliary/nontelemetry CSVs skip."""
    found = set()
    issues = []
    for item in paths:
        item = Path(item).resolve()
        if not item.exists():
            issues.append({"path": str(item), "status": "error", "reason": "Path does not exist"})
            continue
        choices = sorted(item.rglob("*.csv")) if item.is_dir() else [item]
        for path in choices:
            path = path.resolve()
            if exclude_root is not None and (path == exclude_root or exclude_root in path.parents):
                continue
            if path.suffix.lower() != ".csv" or path.name.lower().endswith("_state_events.csv"):
                continue
            try:
                with path.open(newline="", encoding="utf-8-sig") as stream:
                    fields = next(csv.reader(stream), [])
                required = ("elapsed_s", "pos_y", *("norm_" + a for a in AXES))
                if "opt_schema" in fields:
                    issues.append({"path": str(path), "status": "skipped", "reason": "Existing optimiser output"})
                elif all(f in fields for f in required):
                    found.add(path)
                else:
                    issues.append({"path": str(path), "status": "skipped", "reason": "Not an NL2 ride-study CSV"})
            except (OSError, UnicodeError, csv.Error) as exc:
                issues.append({"path": str(path), "status": "error", "reason": str(exc)})
    return sorted(found), issues


def choose_cues(captures: list[Capture], snapshot: dict, *, policy="auto", force=False) -> tuple[tuple[str, ...], str]:
    current = snapshot["angular_cue"]
    known = all(c.quaternions is not None for c in captures)
    if current != "euler" and not known:
        raise ValueError(f"Current cue {current} needs complete quaternion data in every capture")
    compare = force or policy == "all" or (policy == "auto" and any(c.rotation["full_rotation"] for c in captures))
    if policy == "current" and not force:
        compare = False
    if compare and not known:
        if force or policy == "all":
            raise ValueError("Cue comparison explicitly requested but quaternion data are missing/invalid")
        return (current,), "Full-turn evidence in a recording, but incomplete quaternion data; current cue only"
    if compare:
        return tuple(dict.fromkeys((current, "euler", "sine", "gravity"))), "Compare current/Euler/Sine/Gravity: full-turn evidence or explicit override"
    return (current,), "Retain current cue; no unnecessary angular-mode sweep"


def snapshot_for_parameters(base: dict, p: Parameters, label: str) -> dict:
    snapshot = deepcopy(base)
    snapshot.update(label=label, angular_cue=p.cue, neutral_height_delta_mm=p.neutral_mm,
                    gain_scale_pct=dict(zip(AXES, p.gains)))
    return snapshot


def analyse_candidate(captures: list[Capture], platform: str, base: dict, p: Parameters,
                      label: str, globals_snapshot: dict, catalog: VariantStore,
                      allowance_mm: float, active_primary: tuple[int, ...]):
    """Independent, entire-capture normal-player replay of a proposed candidate."""
    snapshot = snapshot_for_parameters(base, p, label)
    backend = make_backend(platform)
    floor_required = (backend._workspace_min_floor_clearance + backend._workspace_hull_margin
                      if backend._workspace_hull_xy else None)
    recordings = []
    summaries = []
    for capture in captures:
        recording = load_recording(capture.path, platform=platform, cue_mode=p.cue,
                    gain_percent=p.gains, variant_label=label, variant_snapshot=snapshot,
                    variant_store=catalog, global_settings=globals_snapshot)
        metrics = recording_summary(recording, floor_required)
        qualifies = (metrics["maximum_excess_mm"] <= allowance_mm + EPS
                     and metrics["floor_violation_frames"] == 0
                     and metrics["nonfinite_frames"] == 0
                     and metrics["output_out_of_range_frames"] == 0)
        if allowance_mm == 0:
            qualifies = qualifies and metrics["workspace_limited_frames"] == 0 and metrics["final_clipped_frames"] == 0
        metrics["qualifies"] = bool(qualifies)
        metrics["allowance_mm"] = allowance_mm
        summaries.append(metrics)
        recordings.append(recording)
    amplitudes = motion_amplitudes([np.clip(np.array([f.processed_axes for f in r.frames]), -1, 1)
                                    for r in recordings])
    scores = priority_scores(amplitudes, active_primary)
    # The analytical workspace pose is available on MDX. Chair's final
    # independent muscle clamp has no forward pose solve, so do not pretend
    # applied_real there measures the actual post-clamp platform pose.
    return recordings, {"qualified": all(s["qualifies"] for s in summaries),
                        "captures": summaries, "priority_scores": scores,
                        "axis_motion_amplitudes": dict(zip(AXES, amplitudes)),
                        "score_basis": "requested pose after normalized-axis saturation; not measured motion"}


def recording_summary(recording, required_floor: float | None) -> dict:
    rows = [f.analysis for f in recording.frames]
    lengths = np.asarray([f.muscle_lengths_mm for f in recording.frames])
    requested = np.asarray([f.analysis["requested"] for f in recording.frames])
    lo, hi = recording.min_length_mm, recording.max_length_mm
    overs = np.maximum(np.maximum(lo - requested, requested - hi), 0).max(axis=1)
    raw_out = overs > EPS
    durations = np.asarray([r["sample_dt_s"] for r in rows])
    floor_bad = np.asarray([bool(r["below_floor"]) or
                           (required_floor is not None and r["applied_floor"] is not None
                            and r["applied_floor"] < required_floor - EPS) for r in rows])
    normalized = np.asarray([f.processed_axes for f in recording.frames])
    finite = np.isfinite(lengths).all(axis=1) & np.isfinite(requested).all(axis=1) & np.isfinite(normalized).all(axis=1)
    for i, row in enumerate(rows):
        finite[i] &= all(v is None or math.isfinite(float(v)) for v in (row["requested_floor"], row["applied_floor"]))
    steps = np.abs(np.diff(lengths, axis=0))
    raw_steps = np.abs(np.diff(requested, axis=0))
    dt = np.diff(np.asarray(recording.times))
    velocities = steps / dt[:, None] if len(dt) else np.zeros((0, 6))
    current = longest = 0.0
    for outside, duration in zip(raw_out, durations):
        current = current + duration if outside else 0.0
        longest = max(longest, current)
    return {"capture": str(recording.csv_path), "frames": len(rows),
            "duration_s": float(sum(durations)), "min_muscle_mm": lo, "max_muscle_mm": hi,
            "stroke_mm": hi - lo, "maximum_excess_mm": float(np.max(overs)),
            "maximum_excess_percent_stroke": float(np.max(overs) / (hi - lo) * 100),
            "out_of_range_frames": int(np.sum(raw_out)),
            "out_of_range_percent_frames": float(np.mean(raw_out) * 100),
            "out_of_range_duration_s": float(np.sum(durations[raw_out])),
            "longest_out_of_range_s": float(longest),
            "workspace_limited_frames": sum(bool(r["workspace_limited"]) for r in rows),
            "final_clipped_frames": sum(bool(r["output_clipped"]) for r in rows),
            "floor_required_mm": required_floor,
            "min_requested_floor_mm": min((r["requested_floor"] for r in rows if r["requested_floor"] is not None), default=None),
            "min_applied_floor_mm": min((r["applied_floor"] for r in rows if r["applied_floor"] is not None), default=None),
            "floor_violation_frames": int(np.sum(floor_bad)),
            "nonfinite_frames": int(np.sum(~finite)),
            "output_out_of_range_frames": int(np.sum(np.any((lengths < lo - EPS) | (lengths > hi + EPS), axis=1))),
            "peak_requested_step_mm": float(np.max(raw_steps)) if raw_steps.size else 0.0,
            "peak_output_step_mm": float(np.max(steps)) if steps.size else 0.0,
            "peak_output_velocity_mm_s": float(np.max(velocities)) if velocities.size else 0.0,
            "p95_output_velocity_mm_s": float(np.percentile(velocities, 95)) if velocities.size else 0.0,
            "normalized_saturated_percent": float(np.mean(np.abs(normalized) > 1) * 100),
            "effective_midpoint_mm": recording.platform_neutral_height_mm + recording.variant_snapshot["neutral_height_delta_mm"]}


def write_json(path: Path, data: dict) -> None:
    """Replace only generated result files inside the newly created run folder."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def export_candidate(folder: Path, capture: Capture, recording, target: str,
                     summary: dict, provenance: dict) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    tag = capture.sha256[:8]
    path = folder / f"{capture.path.stem}_{tag}.csv"
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite candidate CSV {path}")
    # Check the source still matches what was optimised, including any quaternions.
    if sha256_file(capture.path) != capture.sha256:
        raise ValueError(f"Source changed during optimisation: {capture.path}")
    extras = []
    for frame in recording.frames:
        info = frame.analysis
        row = {"opt_schema": SCHEMA, "opt_platform": recording.platform_key,
               "opt_target": target, "opt_label": recording.variant_label,
               "opt_neutral_delta_mm": recording.variant_snapshot["neutral_height_delta_mm"],
               "opt_raw_oob": int(info["raw_out"]), "opt_raw_excess_mm": info["raw_exceed_mm"],
               "opt_workspace_limited": int(info["workspace_limited"]),
               "opt_workspace_scale": info["scale"], "opt_workspace_reason": info["reason"],
               "opt_final_clamped": int(info["output_clipped"]),
               "opt_requested_floor_mm": info["requested_floor"],
               "opt_applied_floor_mm": info["applied_floor"],
               "opt_below_floor": int(info["below_floor"])}
        for i, axis in enumerate(AXES):
            row[f"opt_gain_{axis}_pct"] = recording.gain_percent[i]
            row[f"opt_processed_{axis}"] = info["norm"][i]
            row[f"opt_requested_real_{axis}"] = info["requested_real"][i]
            row[f"opt_applied_real_{axis}"] = info["applied_real"][i]
        for i in range(6):
            row[f"opt_M{i+1}_requested_mm"] = info["requested"][i]
            row[f"opt_M{i+1}_output_mm"] = info["permitted"][i]
        extras.append(row)
    with path.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=capture.csv_fields + list(extras[0]))
        writer.writeheader()
        for original, derived in zip(capture.csv_rows, extras):
            writer.writerow(dict(original, **derived))
    # Carry clocks/events and link (do not copy) the potentially large video.
    old_sync = capture.path.with_name(capture.path.stem + "_video_sync.json")
    from .recording import _read_sync, read_saved_calibration
    manifest, _warning = _read_sync(old_sync)
    if recording.video_path is not None:
        manifest["video_path"] = str(recording.video_path.resolve())
        manifest["video_filename"] = recording.video_path.name
    if manifest:
        write_json(path.with_name(path.stem + "_video_sync.json"), manifest)
    old_events = capture.path.with_name(capture.path.stem + "_state_events.csv")
    if old_events.is_file():
        shutil.copyfile(old_events, path.with_name(path.stem + "_state_events.csv"))
    if read_saved_calibration(capture.path) is not None:
        write_json(path.with_name(path.stem + "_sim_player_sync.json"), {
            "schema": 1, "capture_csv": path.name, "video_offset_s": recording.video_offset_s,
            "comment": "User-calibrated offset copied by optimiser"})
    settings = ExperimentSettings(cue_mode=recording.cue_mode, gain_percent=recording.gain_percent,
                variant_label=recording.variant_label, variant_snapshot=recording.variant_snapshot,
                global_settings=recording.global_settings_snapshot, platform_key=recording.platform_key,
                platform_config_module=recording.platform_config_module,
                platform_config_sha256=recording.platform_config_sha256)
    save_experiment(path.with_name(path.stem + "_experiment.json"), settings)
    data = {"schema": SCHEMA, "csv_name": path.name, "csv_sha256": sha256_file(path),
            "source_csv": str(capture.path), "source_sha256": capture.sha256,
            "target": target, "platform_key": recording.platform_key,
            "angular_cue": recording.cue_mode, "relative_gains_percent": list(recording.gain_percent),
            "variant_snapshot": recording.variant_snapshot,
            "poc_global_settings_snapshot": recording.global_settings_snapshot,
            "model_fingerprint": provenance, "validation": summary,
            "note": "Modelled kinematic candidate only. Final commands are clamped to physical limits. No force/dynamic/comfort validation."}
    # Sidecar is the completion marker. A partial CSV is deliberately not loadable.
    write_json(bundle_path(path), data)
    return path


def seed_parameters(catalog: VariantStore, base: dict, cues: tuple[str, ...]) -> list[Parameters]:
    seeds = []
    fixed = ("heave_mode", "normalized_height_amplitude", "roll_washout", "pitch_washout")
    for label in catalog.player_labels(base["park"]):
        snapshot = catalog.snapshot(label)
        if any(snapshot.get(k) != base.get(k) for k in fixed) or snapshot["angular_cue"] not in cues:
            continue
        gains = tuple(int(v) for v in validate_gain_percent(tuple(snapshot["gain_scale_pct"][a] for a in AXES)))
        seeds.append(Parameters(snapshot["angular_cue"], gains, snapshot["neutral_height_delta_mm"]))
    return seeds


def optimise_group(captures: list[Capture], platform: str, output: Path,
                   options: SearchOptions, *, cue_policy="auto", force_cues=False,
                   progress=print, independent=False) -> dict:
    started = time.perf_counter()
    park = captures[0].park
    if any(c.park != park for c in captures):
        raise ValueError("One optimisation group must contain one physical park")
    catalog = VariantStore.load_readonly(platform)
    label = catalog.default_label(park)
    if label is None:
        # A deliberately hidden park may still be selected for offline work.
        labels = catalog.player_labels(park)
        label = labels[0] if labels else None
    if label is None:
        raise ValueError(f"No non-rejected variant for {park} on {platform}")
    snapshot = catalog.snapshot(label)
    globals_snapshot = checked_global_settings(current_poc_global_settings())
    provenance = model_fingerprint(platform)
    cues, cue_reason = choose_cues(captures, snapshot, policy=cue_policy, force=force_cues)
    baseline = Parameters(snapshot["angular_cue"], tuple(int(v) for v in validate_gain_percent(
                 tuple(snapshot["gain_scale_pct"][a] for a in AXES))), snapshot["neutral_height_delta_mm"])
    evaluator = Evaluator(captures, platform, snapshot, cues, globals_snapshot, options)
    results = search_candidates(evaluator, baseline, seed_parameters(catalog, snapshot, cues), progress)
    output.mkdir(parents=True, exist_ok=True)
    report = {"schema": "poc_optimizer_group_v1", "park": park, "platform": platform,
              "search_options": asdict(options), "baseline_parameters": asdict(baseline),
              "capture_count": len(captures), "joint_validation": not independent,
              "captures": [{"path": str(c.path), "sha256": c.sha256, "rotation": c.rotation} for c in captures],
              "cue_modes_tested": list(cues), "cue_reason": cue_reason,
              "global_settings_snapshot": globals_snapshot, "model_fingerprint": provenance,
              "candidates": [], "failures": []}
    try:
        _, report["baseline"] = analyse_candidate(captures, platform, snapshot, baseline,
                                    label, globals_snapshot, catalog, 0.0, evaluator.active_primary)
    except (ValueError, RuntimeError) as exc:
        report["baseline_error"] = str(exc)
    entries = []
    short_platform = "Chair" if platform == "chair_v3" else "Ground-Loading"
    for target in ("strict", "relaxed5"):
        allowance = (0.0 if target == "strict" else .05 * evaluator.stroke_mm)
        if not results[target]:
            report["failures"].append({"target": target, "reason": "No moving candidate found within the bounded search; no falsely qualified output"})
            continue
        for index, trial in enumerate(results[target], 1):
            note = (" " + captures[0].sha256[:8]) if independent else ""
            new_label = f"{ride_config.PARKS[park]['name']} [Opt {short_platform} {target} {index}{note}]"
            progress(f"independent player replay: {target} candidate {index}")
            recordings, metrics = analyse_candidate(captures, platform, snapshot, trial.parameters,
                        new_label, globals_snapshot, catalog, allowance, evaluator.active_primary)
            if not metrics["qualified"]:
                report["failures"].append({"target": target, "parameters": asdict(trial.parameters),
                                          "reason": "Independent full player replay rejected finalist", "metrics": metrics})
                continue
            if model_fingerprint(platform) != provenance:
                raise ValueError("POC geometry/processing source changed during search; no new candidates exported")
            exported = []
            for capture, recording, summary in zip(captures, recordings, metrics["captures"]):
                path = export_candidate(output / f"{target}_{index:02}", capture, recording,
                                        target, summary, provenance)
                # Reopen what was actually written, using the player's initial
                # open path. Catch CSV/snapshot/double-gain export mistakes now.
                reopened = load_recording(path, default_standard_variant=True)
                expected = np.array([f.analysis["requested"] for f in recording.frames])
                actual = np.array([f.analysis["requested"] for f in reopened.frames])
                if not np.allclose(expected, actual, rtol=0, atol=1.e-8):
                    raise RuntimeError("Exported CSV does not replay identically; study is not qualified")
                exported.append(str(path))
            entry = candidate_entry(new_label, recordings[0])
            entries.append(entry)
            report["candidates"].append({"target": target, "rank": index,
                    "label": new_label, "parameters": asdict(trial.parameters),
                    "metrics": metrics, "csv_files": exported})
    report["evaluations"] = evaluator.evaluations
    report["screen_evaluations"] = evaluator.screen_evaluations
    report["all_frame_evaluations"] = evaluator.full_evaluations
    report["elapsed_seconds"] = time.perf_counter() - started
    report["status"] = "complete" if not report["failures"] else "partial"
    write_json(output / "result.json", report)
    write_json(output / "candidate_variants.json", {"schema_version": 1, "platform_key": platform,
                                                    "variants": entries})
    return report


def write_index(root: Path, reports: list[dict]) -> None:
    rows = []
    for result in reports:
        for item in result["candidates"]:
            params = item["parameters"]
            for path, metrics in zip(item["csv_files"], item["metrics"]["captures"]):
                row = {"park": result["park"], "platform": result["platform"],
                       "target": item["target"], "rank": item["rank"], "label": item["label"],
                       "csv": str(Path(path).relative_to(root)),
                       "cue": params["cue"], "neutral_delta_mm": params["neutral_mm"],
                       "effective_midpoint_mm": metrics["effective_midpoint_mm"],
                       "maximum_excess_mm": metrics["maximum_excess_mm"],
                       "maximum_excess_percent_stroke": metrics["maximum_excess_percent_stroke"],
                       "out_of_range_frames": metrics["out_of_range_frames"],
                       "out_of_range_duration_s": metrics["out_of_range_duration_s"],
                       "floor_violation_frames": metrics["floor_violation_frames"],
                       "peak_output_step_mm": metrics["peak_output_step_mm"],
                       "qualified": metrics["qualifies"]}
                row.update({f"{a}_gain_pct": g for a, g in zip(AXES, params["gains"])})
                rows.append(row)
    destination = root / "index.csv"
    with destination.open("w", newline="", encoding="utf-8") as stream:
        fields = list(rows[0]) if rows else ["park", "platform", "target", "csv", "qualified"]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("captures", nargs="+", type=Path, help="Original NL2 capture files or recursively scanned folders")
    parser.add_argument("--output", type=Path, default=Path("optimizer_results"), help="Parent for a new, non-overwriting timestamped run")
    parser.add_argument("--platforms", nargs="+", choices=DEFAULT_PLATFORMS, default=list(DEFAULT_PLATFORMS))
    parser.add_argument("--only", nargs="+", choices=tuple(ride_config.PARKS), help="Restrict by park key")
    parser.add_argument("--include-grandnational", action="store_true", help="Offline investigation only; does not unhide POC selection")
    parser.add_argument("--per-capture", action="store_true", help="Optimise each recording independently instead of one common setting per ride")
    parser.add_argument("--cue-policy", choices=("auto", "current", "all"), default="auto")
    parser.add_argument("--cue-search-rides", nargs="+", choices=tuple(ride_config.PARKS), default=[], help="Explicit full-turn override for ambiguous captures")
    parser.add_argument("--evaluations", type=int, default=900, help="Approximate unique parameter exploration budget per ride/platform")
    parser.add_argument("--sample-frames", type=int, default=128, help="Initial screening frames; final validation ALWAYS uses all frames")
    parser.add_argument("--alternatives", type=int, default=1, help="Extra distinct candidates per target, 0..3")
    parser.add_argument("--neutral-min", type=float, default=-100)
    parser.add_argument("--neutral-max", type=float, default=100)
    parser.add_argument("--list", action="store_true", help="Validate/list inputs and coverage without running a search or writing files")
    args = parser.parse_args(argv)
    try:
        options = SearchOptions(evaluations=args.evaluations, sample_frames=args.sample_frames,
                                alternatives=args.alternatives, neutral_min=args.neutral_min, neutral_max=args.neutral_max)
    except ValueError as exc:
        parser.error(str(exc))
    # Logs are diagnostic, but thousands of deliberately infeasible trial
    # workspaces must not hide the progress/result output.
    logging.basicConfig(level=logging.ERROR)
    paths, issues = discover_inputs(args.captures, exclude_root=args.output.resolve())
    captures = []
    seen_captures = set()
    for path in paths:
        try:
            capture = read_input(path)
            if args.only and capture.park not in args.only:
                continue
            if capture.park in ride_config.OPERATOR_HIDDEN_PARKS and not args.include_grandnational:
                issues.append({"path": str(path), "status": "skipped", "reason": "Park suppressed from normal POC selection"})
                continue
            if capture.sha256 in seen_captures:
                issues.append({"path": str(path), "status": "skipped", "reason": "Duplicate capture content"})
                continue
            seen_captures.add(capture.sha256)
            captures.append(capture)
        except (ValueError, OSError, UnicodeError, csv.Error) as exc:
            issues.append({"path": str(path), "status": "error", "reason": str(exc)})
    supported = set(args.only or ride_config.PARKS)
    if not args.include_grandnational:
        supported -= ride_config.OPERATOR_HIDDEN_PARKS
    missing = sorted(supported - {c.park for c in captures})
    print(f"Validated {len(captures)} capture(s); platforms: {', '.join(args.platforms)}", flush=True)
    for c in captures:
        print(f"  {c.park}: {c.path.name} ({len(c.rows)} frames); {c.rotation['reason']}", flush=True)
    if missing:
        print("Missing captures (not silently substituted): " + ", ".join(missing), flush=True)
    for issue in issues:
        if issue["status"] == "error":
            print(f"ERROR {issue['path']}: {issue['reason']}", file=sys.stderr, flush=True)
    if args.list or not captures:
        return 1 if not captures or any(i["status"] == "error" for i in issues) else 0
    now = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    root = args.output.resolve() / ("run_" + now)
    root.mkdir(parents=True, exist_ok=False)
    (root / "OPEN_IN_SIM_PLAYER.txt").write_text(
        "Open a candidate CSV listed in index.csv using File > Open in Sim Player.\n"
        "Keep the CSV and all its companion files together. The player automatically\n"
        "selects the candidate platform/cue/gains/neutral and frozen global settings.\n"
        "Its anomaly display is recalculated from ORIGINAL telemetry. Use the usual\n"
        "gain/neutral controls for manual tuning; save a candidate to POC only explicitly.\n"
        "candidate_variants.json is an export, NOT a replacement for your live catalogue.\n"
        "Strict: no length/workspace/floor violation. Relaxed: maximum request exceeds\n"
        "physical stroke by at most 5%, never a 5% hardware-limit extension.\n"
        "These are geometric candidates, not force/dynamic/comfort certification.\n", encoding="utf-8")
    groups = defaultdict(list)
    for capture in captures:
        groups[(capture.park, capture.sha256 if args.per_capture else "")].append(capture)
    reports = []
    run = {"schema": "poc_optimizer_run_v1", "status": "running", "missing_captures": missing,
           "input_issues": issues, "jobs": [], "options": asdict(options)}
    write_json(root / "run.json", run)
    try:
        jobs = [(platform, key, group) for platform in dict.fromkeys(args.platforms)
                for key, group in sorted(groups.items())]
        for i, (platform, (park, key), group) in enumerate(jobs, 1):
            label = f"[{i}/{len(jobs)}] {park}/{platform}"
            print(label, flush=True)
            output = root / platform / (park + ("_" + key[:8] if key else ""))
            try:
                report = optimise_group(group, platform, output, options, cue_policy=args.cue_policy,
                    force_cues=park in args.cue_search_rides, independent=args.per_capture,
                    progress=lambda s, prefix=label: print(f"  {prefix}: {s}", flush=True))
                reports.append(report)
                run["jobs"].append({"park": park, "platform": platform, "status": report["status"],
                                    "report": str((output / "result.json").relative_to(root))})
                print(f"  -> {len(report['candidates'])} candidate(s), {report['evaluations']} trials", flush=True)
            except (OSError, ValueError, RuntimeError) as exc:
                run["jobs"].append({"park": park, "platform": platform, "status": "error", "reason": str(exc)})
                print(f"  FAILED: {exc}", file=sys.stderr, flush=True)
            write_index(root, reports)
            write_json(root / "run.json", run)
    except KeyboardInterrupt:
        run["status"] = "interrupted"
        write_json(root / "run.json", run)
        print(f"Interrupted. Completed candidates preserved in {root}", flush=True)
        return 130
    failed = (any(j["status"] != "complete" for j in run["jobs"])
              or any(i["status"] == "error" for i in issues))
    run["status"] = "partial" if failed else "complete"
    write_json(root / "run.json", run)
    print(f"Results: {root}\nOpen candidate CSVs from index.csv in Sim Player.", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
