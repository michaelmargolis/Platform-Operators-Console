# analyze_nl2_csv.py ver 14
"""Standalone NL2 CSV playback / muscle-length study; no POC outputs are created.

Place in POC/tools; run: python tools/analyze_nl2_csv.py PATH_TO_CAPTURE.csv
Requires numpy and Tkinter. It imports the existing POC kinematics, telemetry,
and workspace methods without instantiating the live physical backend.
"""
from __future__ import annotations

import argparse
import csv
import importlib
import math
import os
import shutil
import sys
import traceback
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, ttk, messagebox

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.platforms import PLATFORMS
from core.telemetry_config import TelemetryConfig
from sims.nolimits2 import config as nl2_config
from core.platform_controller import SopPlatformBackend
from kinematics.kinematics_V3 import Kinematics, PlatformParams

AXES = ("surge", "sway", "heave", "roll", "pitch", "yaw")


def read_capture(path):
    """Read a capture strictly: a malformed ride cannot yield partial results."""
    with open(path, newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        required = ["pos_y", "elapsed_s", *(f"norm_{axis}" for axis in AXES)]
        if not reader.fieldnames or any(name not in reader.fieldnames for name in required):
            raise ValueError("CSV lacks required NL2 ride-study fields: " + ", ".join(required))
        data = []
        prior_time = None
        for lineno, row in enumerate(reader, start=2):
            try:
                height = float(row["pos_y"])
                elapsed = float(row["elapsed_s"])
                values = tuple(float(row[f"norm_{axis}"]) for axis in AXES)
                extra = row.get("vertical_g_heave")
                vertical_g = float(extra) if extra not in (None, "") else None
                if not all(math.isfinite(v) for v in (height, elapsed, *values)):
                    raise ValueError("nonfinite mandatory value")
                if vertical_g is not None and not math.isfinite(vertical_g):
                    raise ValueError("nonfinite vertical_g_heave")
                if prior_time is not None and elapsed <= prior_time:
                    raise ValueError("elapsed_s must increase strictly")
            except (ValueError, TypeError, KeyError) as exc:
                raise ValueError(f"Invalid data in {Path(path).name}, row {lineno}: {exc}") from exc
            data.append((elapsed, height, values, vertical_g))
            prior_time = elapsed
    if not data:
        raise ValueError("CSV contains no usable frames")
    return data


def capture_ride_name(path):
    """Use the explicit CSV ride field; never infer a ride from its filename."""
    with open(path, newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        row = next(reader, None)
    return str(row.get("ride", "")).strip() if row else ""


def make_backend(platform_key):
    """Use POC workspace implementation, without constructing MuscleOutput/Festo."""
    cfg = importlib.import_module(PLATFORMS[platform_key]["sop_config_module"]).PlatformConfig()
    k = Kinematics()
    # Offline replay must use the same per-platform axis convention as the
    # production backend. Otherwise MDX Roll and actuator indices disagree.
    axis_flip_mask = getattr(cfg, "AXIS_FLIP_MASK", None)
    if axis_flip_mask is not None:
        k.set_axis_flip_mask(axis_flip_mask)
    params = PlatformParams(cfg.MUSCLE_MIN_LENGTH, cfg.MUSCLE_MAX_LENGTH,
                            cfg.FIXED_HARDWARE_LENGTH, cfg.LIMITS_1DOF_TRANFORM)
    platform_coords = getattr(cfg, "platform_coords", None)
    if platform_coords is None:
        platform_coords = cfg.platform_coords_xy
    from core.platform_geometry import clearance_settings, configure_workspace
    offset, reserve = clearance_settings(cfg)
    k.set_geometry(
        cfg.base_coords, platform_coords, params, offset, reserve,
        neutral_height_offset_mm=getattr(cfg, "NEUTRAL_HEIGHT_OFFSET_MM", 0.0),
    )
    backend = SopPlatformBackend.__new__(SopPlatformBackend)
    backend.cfg, backend.k = cfg, k
    configure_workspace(backend)
    return backend


def lengths_for_real(backend, real):
    _, actuator = backend.k.inverse_kinematics(real, return_lengths=True)
    return [float(x) - float(backend.cfg.FIXED_HARDWARE_LENGTH) for x in actuator]


def analyze(data, platform_key, mode, amplitude, intensity, process_telemetry, ride_name=None,
            variant_snapshot=None):
    backend = make_backend(platform_key)
    if variant_snapshot is not None:
        backend.set_ride_neutral_height_delta_mm(
            variant_snapshot.get("neutral_height_delta_mm", 0.0))
    cfg = backend.cfg
    telem = TelemetryConfig(ROOT / "sims/nolimits2/telemetry_config.ini")
    if variant_snapshot is None:
        overrides = nl2_config.angular_washout_overrides(ride_name)
    else:
        overrides = (variant_snapshot.get("roll_washout"), variant_snapshot.get("pitch_washout"))
        telem.set_temporary_classical_z(variant_snapshot.get("heave_mode") == "vertical_g")
    telem.set_angular_washout_overrides(*overrides)
    policy = telem.effective_washout()
    lo, hi = min(row[1] for row in data), max(row[1] for row in data)
    if hi <= lo and mode == "Normalized height":
        raise ValueError("Recorded height range is zero")
    frames = []
    limited_count = raw_out_count = output_clipped_count = 0
    longest_intervention_s = current_intervention_s = 0.0
    max_raw_exceed_mm = 0.0
    has_floor = (
        backend.workspace_limit_mode == "geometry"
        and backend._workspace_floor_z is not None
        and backend._workspace_underside_z is not None
        and backend._workspace_min_floor_clearance is not None
        and bool(backend._workspace_hull_xy)
    )
    required_floor = (
        float(backend._workspace_min_floor_clearance) + backend._workspace_hull_margin
        if has_floor else None
    )
    below_floor_count = floor_limited_count = 0
    min_requested_floor = min_applied_floor = None
    min_requested_time = min_applied_time = None
    for i, row in enumerate(data):
        elapsed, height, values = row[:3]
        norm = list(values)
        if mode == "Normalized height":
            # Inverted suspended platform: Kinematics.FLIP_TRANSLATION[2] is -1.
            # Negative POC heave raises the platform and shortens its muscles.
            # Map the coaster's highest point to negative heave so the platform
            # rises (and contracts) with the coaster instead of descending.
            height_fraction = 2 * (height - lo) / (hi - lo) - 1
            norm[2] = max(-1.0, min(1.0, -amplitude * height_fraction))
        elif mode == "Recorded heave":
            norm[2] = values[2]
        elif mode == "Vertical G":
            vertical_g = row[3] if len(row) >= 4 else None
            if vertical_g is None:
                raise ValueError("Selected CSV has no vertical_g_heave data")
            norm[2] = vertical_g
        norm_input = tuple(norm)
        dt = max(0.0, elapsed - data[i-1][0]) if i else 0.05
        norm = (telem.process(norm, intensity, dt)
                if process_telemetry else tuple(x * intensity/100.0 for x in norm))
        real = backend.real_with_ride_neutral_offset(backend.k.norm_to_real(norm))
        raw = lengths_for_real(backend, real)
        # Call POC's existing CAD-outline clearance calculation for the
        # requested pose. It is not available when floor limiting is disabled.
        requested_floor = (backend._floor_clearance_for_real_transform(real)
                           if has_floor else None)
        limited_real, permitted_act = backend._limit_workspace(real)
        applied_floor = backend.workspace_floor_clearance if has_floor else None
        if has_floor:
            if requested_floor is None or applied_floor is None:
                raise RuntimeError("Geometry floor clearance was not computed by POC")
            below_floor_count += requested_floor < required_floor - 1.0e-7
            floor_limited_count += bool(
                backend.workspace_limited and
                "FLOOR" in (backend.workspace_limit_reason or "").split("+")
            )
            if min_requested_floor is None or requested_floor < min_requested_floor:
                min_requested_floor, min_requested_time = requested_floor, elapsed
            if min_applied_floor is None or applied_floor < min_applied_floor:
                min_applied_floor, min_applied_time = applied_floor, elapsed
        permitted = (lengths_for_real(backend, limited_real) if permitted_act is None
                     else [float(x) - cfg.FIXED_HARDWARE_LENGTH for x in permitted_act])
        # Distinguish the unconstrained request, POC workspace scaling, and
        # final physical-length clamping. The Chair has workspace limiting off
        # but can still require the latter.
        output_lengths = [max(cfg.MUSCLE_MIN_LENGTH, min(cfg.MUSCLE_MAX_LENGTH, v))
                          for v in permitted]
        raw_exceed_mm = max((max(float(cfg.MUSCLE_MIN_LENGTH) - x,
                                 x - float(cfg.MUSCLE_MAX_LENGTH), 0.0)
                             for x in raw), default=0.0)
        raw_out = raw_exceed_mm > 1e-7
        output_exceed_mm = max((abs(a-b) for a, b in zip(permitted, output_lengths)), default=0.0)
        output_clipped = output_exceed_mm > 1e-7
        intervention = bool(backend.workspace_limited) or output_clipped
        limited = intervention or raw_out
        limited_count += bool(backend.workspace_limited)
        raw_out_count += raw_out
        output_clipped_count += output_clipped
        max_raw_exceed_mm = max(max_raw_exceed_mm, raw_exceed_mm)
        # Count the longest consecutive interval requiring POC workspace
        # limiting or final physical-length clipping (not merely a request).
        # Attribute each sample its recorded interval to the next frame.
        sample_dt = (max(0.0, data[i+1][0] - elapsed) if i+1 < len(data)
                     else max(0.0, elapsed - data[i-1][0]) if i else 0.05)
        if intervention:
            current_intervention_s += sample_dt
            longest_intervention_s = max(longest_intervention_s, current_intervention_s)
        else:
            current_intervention_s = 0.0
        frames.append(dict(time=elapsed, height=height,
                           norm_input=norm_input, norm=tuple(norm),
                           requested_real=tuple(float(v) for v in real),
                           applied_real=tuple(float(v) for v in limited_real),
                           requested=raw, workspace_lengths=permitted,
                           permitted=output_lengths,
                           limited=limited, reason=backend.workspace_limit_reason,
                           scale=backend.workspace_scale, raw_out=raw_out,
                           raw_exceed_mm=raw_exceed_mm,
                           output_clipped=output_clipped,
                           output_exceed_mm=output_exceed_mm,
                           workspace_limited=bool(backend.workspace_limited),
                           floor_limited=bool(backend.workspace_limited and
                                              "FLOOR" in (backend.workspace_limit_reason or "").split("+")),
                           intervention=intervention, sample_dt_s=sample_dt,
                           floor=applied_floor, requested_floor=requested_floor,
                           applied_floor=applied_floor,
                           below_floor=(requested_floor < required_floor - 1.0e-7
                                        if has_floor else False)))
    return frames, dict(lo=lo, hi=hi,
                        platform_neutral_height_mm=float(backend.k.PLATFORM_MID_HEIGHT),
                        neutral_height_delta_mm=float(backend.ride_neutral_height_delta_mm),
                        min_length=float(cfg.MUSCLE_MIN_LENGTH),
                        max_length=float(cfg.MUSCLE_MAX_LENGTH),
                        limited=limited_count, raw_out=raw_out_count,
                        output_clipped=output_clipped_count,
                        max_raw_exceed_mm=max_raw_exceed_mm,
                        longest_intervention_s=longest_intervention_s,
                        workspace_mode=backend.workspace_limit_mode,
                        floor_enabled=has_floor, floor_required=required_floor,
                        floor_safety_margin=(float(backend._workspace_min_floor_clearance)
                                             if has_floor else None),
                        floor_hull_margin=(backend._workspace_hull_margin
                                           if has_floor else None),
                        floor_below_count=below_floor_count,
                        floor_limited_count=floor_limited_count,
                        floor_min_requested=min_requested_floor,
                        floor_min_applied=min_applied_floor,
                        floor_min_requested_time=min_requested_time,
                        floor_min_applied_time=min_applied_time,
                        extension_reserve_mm=float(getattr(cfg, "MUSCLE_EXTENSION_RESERVE", 0.0)),
                        ride_name=ride_name or "",
                        washout_type=(policy["type"] if process_telemetry else "not_applied"),
                        washout_roll_enabled=(policy["enabled"][3] if process_telemetry else False),
                        washout_pitch_enabled=(policy["enabled"][4] if process_telemetry else False),
                        washout_roll_source=policy["roll_source"],
                        washout_pitch_source=policy["pitch_source"],
                        active_max_length=(float(cfg.MUSCLE_MAX_ACTIVE_LENGTH)
                            if backend.workspace_limit_mode != "off"
                            and float(getattr(cfg, "MUSCLE_EXTENSION_RESERVE", 0.0)) > 0
                            else None))


SWEEP_AMPLITUDES = (60, 70, 80, 90, 100)


def amplitude_sweep(data, platform_key, intensity, process_telemetry,
                    amplitudes=SWEEP_AMPLITUDES, ride_name=None):
    """Recalculate a full capture for each normalized-height amplitude.

    This intentionally uses the same `analyze` path as playback. Each run
    starts with a fresh TelemetryConfig state so washout remains comparable.
    """
    results = []
    for percent in amplitudes:
        frames, stats = analyze(data, platform_key, "Normalized height",
                                float(percent) / 100.0, intensity, process_telemetry,
                                ride_name=ride_name)
        n = len(frames)
        events = extract_events(frames, stats)
        results.append(dict(amplitude=percent, frames=n,
                            intervention_events=sum(e["type"] == "intervention" for e in events),
                            raw_oob_events=sum(e["type"] == "raw_out" for e in events),
                            raw_out_percent=100.0 * stats["raw_out"] / n,
                            output_clipped_percent=100.0 * stats["output_clipped"] / n,
                            workspace_limited_percent=100.0 * stats["limited"] / n,
                            floor_limited_percent=(100.0 * stats["floor_limited_count"] / n
                                                   if stats["floor_enabled"] else None),
                            min_applied_floor=stats["floor_min_applied"],
                            min_requested_floor=stats["floor_min_requested"],
                            longest_intervention_s=stats["longest_intervention_s"],
                            max_raw_exceed_mm=stats["max_raw_exceed_mm"]))
    return results


EVENT_FLAGS = ("raw_out", "workspace_limited", "output_clipped",
               "floor_limited", "below_floor", "intervention")


def extract_events(frames, stats):
    """Group consecutive flagged samples into real-time intervals."""
    if not frames:
        return []
    lo, hi = stats["min_length"], stats["max_length"]
    events = []
    for flag in EVENT_FLAGS:
        start = None
        for i in range(len(frames) + 1):
            active = i < len(frames) and bool(frames[i].get(flag, False))
            if active and start is None:
                start = i
            if start is None or active:
                continue
            section = frames[start:i]
            first, last = section[0], section[-1]
            related = sorted({f"M{j + 1}" for f in section
                              for j, length in enumerate(f["requested"])
                              if length < lo - 1e-7 or length > hi + 1e-7})
            events.append(dict(type=flag,
                               start_frame=start, end_frame=i - 1,
                               start_s=first["time"],
                               end_s=last["time"] + last["sample_dt_s"],
                               duration_s=sum(f["sample_dt_s"] for f in section),
                               frame_count=len(section),
                               muscles=";".join(related),
                               max_request_excess_mm=max(f["raw_exceed_mm"] for f in section),
                               max_final_clamp_mm=max(f["output_exceed_mm"] for f in section),
                               min_workspace_scale=min(f["scale"] for f in section),
                               min_requested_clearance_mm=min(
                                   (f["requested_floor"] for f in section
                                    if f["requested_floor"] is not None), default=None),
                               min_applied_clearance_mm=min(
                                   (f["applied_floor"] for f in section
                                    if f["applied_floor"] is not None), default=None),
                               reasons=";".join(sorted({f["reason"] for f in section if f["reason"]}))))
            start = None
    return sorted(events, key=lambda e: (e["start_s"], e["type"]))


def muscle_statistics(frames, stats):
    """Requested, workspace and output extremes for each of the six muscles."""
    lo, hi = stats["min_length"], stats["max_length"]
    out = []
    for index in range(6):
        raw = [f["requested"][index] for f in frames]
        workspace = [f["workspace_lengths"][index] for f in frames]
        final = [f["permitted"][index] for f in frames]
        out.append(dict(muscle=f"M{index + 1}",
                        requested_min_mm=min(raw), requested_max_mm=max(raw),
                        workspace_min_mm=min(workspace), workspace_max_mm=max(workspace),
                        final_min_mm=min(final), final_max_mm=max(final),
                        requested_oob_frames=sum(x < lo - 1e-7 or x > hi + 1e-7 for x in raw),
                        final_clamp_frames=sum(abs(a-b) > 1e-7 for a,b in zip(workspace,final)),
                        max_requested_excess_mm=max(max(lo-x, x-hi, 0.0) for x in raw),
                        max_final_clamp_mm=max(abs(a-b) for a,b in zip(workspace,final))))
    return out


def _csv_value(value):
    if value is None:
        return ""
    if isinstance(value, float):
        return round(value, 6)
    return value


def _write_csv(path, rows, fieldnames=None):
    if fieldnames is None:
        fieldnames = list(rows[0]) if rows else []
    with open(path, "w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: _csv_value(v) for k,v in row.items()})


def export_results(directory, frames, stats, sweep, metadata):
    """Create inspectable Phase 1 CSV exports; no platform commands."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    rows = []
    for index, frame in enumerate(frames):
        row = dict(frame=index, time_s=frame["time"], sample_dt_s=frame["sample_dt_s"],
                   height_m=frame["height"], raw_oob=frame["raw_out"],
                   raw_excess_mm=frame["raw_exceed_mm"],
                   workspace_limited=frame["workspace_limited"],
                   workspace_scale=frame["scale"], workspace_reason=frame["reason"],
                   final_clamped=frame["output_clipped"],
                   final_clamp_excess_mm=frame["output_exceed_mm"],
                   intervention=frame["intervention"],
                   floor_limited=frame["floor_limited"],
                   below_floor_target=frame["below_floor"],
                   requested_floor_mm=frame["requested_floor"],
                   applied_floor_mm=frame["applied_floor"])
        for j, axis in enumerate(AXES):
            row[f"input_{axis}"] = frame["norm_input"][j]
            row[f"processed_{axis}"] = frame["norm"][j]
            row[f"requested_real_{axis}"] = frame["requested_real"][j]
            row[f"applied_real_{axis}"] = frame["applied_real"][j]
            row[f"pose_change_{axis}"] = frame["applied_real"][j] - frame["requested_real"][j]
        for j in range(6):
            row[f"M{j + 1}_requested_mm"] = frame["requested"][j]
            row[f"M{j + 1}_workspace_mm"] = frame["workspace_lengths"][j]
            row[f"M{j + 1}_output_mm"] = frame["permitted"][j]
        rows.append(row)
    events = extract_events(frames, stats)
    muscles = muscle_statistics(frames, stats)
    _write_csv(directory / "frames.csv", rows)
    _write_csv(directory / "events.csv", events, fieldnames=(
        "type", "start_frame", "end_frame", "start_s", "end_s", "duration_s",
        "frame_count", "muscles", "max_request_excess_mm", "max_final_clamp_mm",
        "min_workspace_scale", "min_requested_clearance_mm", "min_applied_clearance_mm", "reasons"))
    _write_csv(directory / "muscles.csv", muscles)
    _write_csv(directory / "sweep.csv", sweep)
    summary = dict(metadata)
    summary.update(frames=len(frames), recorded_height_min_m=stats["lo"],
                   recorded_height_max_m=stats["hi"], raw_oob_frames=stats["raw_out"],
                   workspace_limited_frames=stats["limited"],
                   final_clipped_frames=stats["output_clipped"],
                   floor_limited_frames=stats["floor_limited_count"],
                   min_requested_floor_mm=stats["floor_min_requested"],
                   min_applied_floor_mm=stats["floor_min_applied"],
                   maximum_raw_excess_mm=stats["max_raw_exceed_mm"],
                   longest_intervention_s=stats["longest_intervention_s"],
                   intervention_events=sum(e["type"] == "intervention" for e in events),
                   raw_oob_events=sum(e["type"] == "raw_out" for e in events))
    _write_csv(directory / "summary.csv", [summary])
    return summary


def batch_export(paths, platform_key, mode, amplitude, intensity, process_telemetry,
                 output_dir, progress=None):
    """Process each selected ride independently; log errors and continue."""
    output_dir = Path(output_dir)
    summaries, failures = [], []
    used_names = set()
    for path in paths:
        path = Path(path)
        if progress:
            progress(f"Analysing {path.name}")
        staging = None
        try:
            data = read_capture(path)
            ride_name = capture_ride_name(path)
            frames, stats = analyze(data, platform_key, mode, amplitude, intensity,
                                    process_telemetry, ride_name=ride_name)
            sweep = amplitude_sweep(data, platform_key, intensity, process_telemetry,
                                    ride_name=ride_name)
            name = path.stem
            suffix = 2
            while name.lower() in used_names or (output_dir / name).exists():
                name = f"{path.stem}_{suffix}"
                suffix += 1
            used_names.add(name.lower())
            metadata = dict(source_csv=str(path.resolve()), platform=platform_key,
                            ride_name=ride_name,
                            heave_mode=mode, amplitude_percent=100*amplitude,
                            intensity_percent=intensity, saved_gains_washout=process_telemetry,
                            washout_type=stats["washout_type"],
                            washout_roll_enabled=stats["washout_roll_enabled"],
                            washout_pitch_enabled=stats["washout_pitch_enabled"],
                            washout_roll_source=stats["washout_roll_source"],
                            washout_pitch_source=stats["washout_pitch_source"])
            staging = output_dir / f".{name}.partial"
            if staging.exists():
                shutil.rmtree(staging)
            summary = export_results(staging, frames, stats, sweep, metadata)
            staging.rename(output_dir / name)
            staging = None
            summaries.append(summary)
            print(f"[OK] {path.name}: {len(frames)} frames -> {output_dir / name}", flush=True)
        except Exception as exc:
            if staging is not None and staging.exists():
                shutil.rmtree(staging)
            message = f"[ERROR] {path}: {type(exc).__name__}: {exc} (ride skipped)"
            print(message, file=sys.stderr, flush=True)
            if not isinstance(exc, (OSError, ValueError, RuntimeError, KeyError, ArithmeticError)):
                traceback.print_exc()
            failures.append(dict(source_csv=str(path), error_type=type(exc).__name__,
                                 error=str(exc)))
            continue
    if summaries or failures:
        output_dir.mkdir(parents=True, exist_ok=True)
        _write_csv(output_dir / "batch_summary.csv", summaries)
        _write_csv(output_dir / "batch_errors.csv", failures,
                   fieldnames=("source_csv", "error_type", "error"))
    return summaries, failures


def new_export_dir():
    """Create a unique destination under the current working directory."""
    root = Path.cwd() / "analysis_exports"
    root.mkdir(parents=True, exist_ok=True)
    name = datetime.now().strftime("nl2_%Y%m%d_%H%M%S")
    candidate = root / name
    suffix = 2
    while candidate.exists():
        candidate = root / f"{name}_{suffix}"
        suffix += 1
    candidate.mkdir()
    return candidate


class AnalyzerApp:
    def __init__(self, root, initial_csv=None):
        self.root = root
        root.title("POC Motion Analyzer — NL2 CSV")
        root.geometry("1120x760")
        self.data, self.frames, self.stats = None, [], None
        self.loaded_csv = None
        self.loaded_ride_name = ""
        self.batch_paths = []
        self.path = tk.StringVar(value="")
        self.platform = tk.StringVar(value="platform_v2")
        self.mode = tk.StringVar(value="Normalized height")
        self.amplitude = tk.DoubleVar(value=80)
        self.intensity = tk.DoubleVar(value=100)
        self.with_filters = tk.BooleanVar(value=True)
        self.frame_index = tk.IntVar(value=0)
        self.playing = False

        main = ttk.Frame(root, padding=10)
        main.pack(fill="both", expand=True)

        left_panel = ttk.Frame(main, width=340)
        left_panel.pack(side="left", fill="y", padx=(0, 10))
        left_panel.pack_propagate(False)

        right_panel = ttk.Frame(main)
        right_panel.pack(side="left", fill="both", expand=True)

        controls = ttk.Frame(left_panel)
        controls.pack(fill="x", anchor="n")
        ttk.Button(controls, text="Open CSV", command=self.open_csv).grid(row=0, column=0, sticky="w")
        ttk.Label(controls, textvariable=self.path, wraplength=310, justify="left").grid(
            row=1, column=0, columnspan=4, sticky="w", pady=(8, 12)
        )

        ttk.Label(controls, text="Platform").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Combobox(controls, textvariable=self.platform, values=list(PLATFORMS),
                     state="readonly", width=14).grid(row=2, column=1, columnspan=3, sticky="ew", pady=4)

        ttk.Label(controls, text="Heave").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Combobox(controls, textvariable=self.mode, values=["Recorded heave", "Normalized height", "Vertical G"],
                     state="readonly", width=18).grid(row=3, column=1, columnspan=3, sticky="ew", pady=4)

        ttk.Label(controls, text="Amplitude %").grid(row=4, column=0, sticky="w", pady=4)
        tk.Spinbox(controls, from_=0, to=100, increment=5, textvariable=self.amplitude, width=6).grid(
            row=4, column=1, sticky="w", pady=4
        )

        ttk.Checkbutton(controls, text="Use saved POC gains/washout", variable=self.with_filters).grid(
            row=5, column=0, columnspan=4, sticky="w", pady=(6, 4)
        )

        ttk.Label(controls, text="Intensity %").grid(row=6, column=0, sticky="w", pady=4)
        tk.Spinbox(controls, from_=0, to=100, increment=10, textvariable=self.intensity, width=6).grid(
            row=6, column=1, sticky="w", pady=4
        )
        ttk.Button(controls, text="Recalculate", command=self.recalculate).grid(
            row=7, column=0, columnspan=4, sticky="ew", pady=(10, 0)
        )
        ttk.Button(controls, text="Sweep 60–100%", command=self.show_sweep).grid(
            row=8, column=0, columnspan=4, sticky="ew", pady=(5, 0)
        )
        ttk.Button(controls, text="Export current ride", command=self.export_current).grid(
            row=9, column=0, columnspan=4, sticky="ew", pady=(5, 0)
        )
        ttk.Button(controls, text="Batch export selected", command=self.export_batch).grid(
            row=10, column=0, columnspan=4, sticky="ew", pady=(5, 0)
        )
        self.batch_note = tk.StringVar(value="Select multiple CSV files to batch export")
        ttk.Label(controls, textvariable=self.batch_note, wraplength=305,
                  justify="left").grid(row=11, column=0, columnspan=4, sticky="w", pady=(6, 0))

        self.info = tk.StringVar(value="Open a ride-study CSV to begin")
        self.info2 = tk.StringVar(value="")
        self.floor_summary = tk.StringVar(value="")
        self.floor_min_distance = tk.StringVar(value="Min distance to floor: —")
        self.floor_current = tk.StringVar(value="")
        ttk.Separator(left_panel, orient="horizontal").pack(fill="x", pady=14)
        ttk.Label(left_panel, textvariable=self.info, wraplength=320, justify="left").pack(anchor="w", fill="x")
        ttk.Label(left_panel, textvariable=self.info2, wraplength=320, justify="left").pack(anchor="w", fill="x", pady=(8, 0))
        ttk.Separator(left_panel, orient="horizontal").pack(fill="x", pady=10)
        ttk.Label(left_panel, text="Floor clearance", font=("Arial", 10, "bold")).pack(anchor="w")
        ttk.Label(left_panel, textvariable=self.floor_min_distance,
                  font=("Arial", 11, "bold"), wraplength=320).pack(anchor="w", pady=(5, 0))
        ttk.Label(left_panel, textvariable=self.floor_summary, wraplength=320,
                  justify="left").pack(anchor="w", fill="x", pady=(5, 0))
        ttk.Label(left_panel, textvariable=self.floor_current, wraplength=320,
                  justify="left").pack(anchor="w", fill="x", pady=(10, 0))

        self.canvas = tk.Canvas(right_panel, background="#f7f8fa", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        nav = ttk.Frame(root, padding=(12, 4))
        nav.pack(fill="x")
        self.play_btn = ttk.Button(nav, text="Play", command=self.toggle_play)
        self.play_btn.pack(side="left")
        ttk.Button(nav, text="<", command=lambda: self.jump(-1)).pack(side="left", padx=(8, 0))
        ttk.Button(nav, text=">", command=lambda: self.jump(1)).pack(side="left")
        self.scrub = tk.Scale(nav, from_=0, to=0, orient="horizontal", showvalue=False,
                              variable=self.frame_index, command=lambda _: self.draw())
        self.scrub.pack(side="left", fill="x", expand=True, padx=(10, 0))
        self.status = tk.StringVar(value="")
        ttk.Label(root, textvariable=self.status, padding=(12, 4)).pack(anchor="w")
        self.canvas.bind("<Configure>", lambda _: self.draw())
        if initial_csv:
            self.load_csv(initial_csv)

    def open_csv(self):
        paths = filedialog.askopenfilenames(initialdir=os.getcwd(),
                                            filetypes=[("NL2 CSV captures", "*.csv")])
        if not paths:
            return
        self.batch_paths = list(paths)
        if len(paths) == 1:
            self.load_csv(paths[0])
        else:
            self.export_batch()

    def load_csv(self, path):
        try:
            new_data = read_capture(path)
            ride_name = capture_ride_name(path)
            frames, stats = analyze(new_data, self.platform.get(), self.mode.get(),
                                    float(self.amplitude.get()) / 100,
                                    float(self.intensity.get()), self.with_filters.get(),
                                    ride_name=ride_name)
            self.playing = False
            self.play_btn.configure(text="Play")
            self.data, self.frames, self.stats = new_data, frames, stats
            self.loaded_csv = str(path)
            self.loaded_ride_name = ride_name
            self.batch_paths = self.batch_paths or [str(path)]
            self.path.set(Path(path).name)
            self.frame_index.set(0)
            self._display_analysis()
            return True
        except Exception as exc:
            print(f"[ERROR] {path}: {type(exc).__name__}: {exc} (ride skipped)",
                  file=sys.stderr, flush=True)
            messagebox.showerror("CSV error", str(exc))
            return False

    def _run_exports(self, paths):
        if not paths:
            messagebox.showinfo("Batch export", "Select a CSV file first.")
            return
        try:
            destination = new_export_dir()
            self.root.configure(cursor="watch")
            def progress(message):
                self.batch_note.set(message)
                self.root.update_idletasks()
            summaries, failures = batch_export(
                paths, self.platform.get(), self.mode.get(),
                float(self.amplitude.get()) / 100,
                float(self.intensity.get()), self.with_filters.get(),
                destination, progress=progress)
            self.batch_note.set(f"Exported {len(summaries)}/{len(paths)} rides. "
                                f"Errors: {len(failures)}. Output: {destination}")
            print(f"Batch complete: {len(summaries)} succeeded, {len(failures)} failed. "
                  f"Output: {destination}", flush=True)
            # Keep first successfully processed ride available for playback.
            if summaries:
                first_path = summaries[0]["source_csv"]
                if self.loaded_csv != first_path:
                    self.load_csv(first_path)
                self.batch_paths = list(paths)
        except Exception as exc:
            print(f"[ERROR] Export: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            messagebox.showerror("Batch export", str(exc))
        finally:
            self.root.configure(cursor="")

    def export_current(self):
        if self.loaded_csv:
            self._run_exports([self.loaded_csv])
        else:
            messagebox.showinfo("Export", "Open a ride-study CSV first.")

    def export_batch(self):
        self._run_exports(self.batch_paths)

    def recalculate(self):
        if not self.data:
            return
        self.playing = False
        self.play_btn.configure(text="Play")
        try:
            new_frames, new_stats = analyze(self.data, self.platform.get(),
                                            self.mode.get(), float(self.amplitude.get())/100,
                                            float(self.intensity.get()), self.with_filters.get(),
                                            ride_name=self.loaded_ride_name)
        except Exception as exc:
            messagebox.showerror("Analysis error", str(exc))
            return
        self.frames, self.stats = new_frames, new_stats
        self._display_analysis()

    def _display_analysis(self):
        n = len(self.frames)
        self.scrub.configure(to=n-1)
        self.frame_index.set(min(self.frame_index.get(), n-1))
        s = self.stats
        self.info.set(
            f"{n:,} frames\n"
            f"Recorded height range: {s['lo']:.2f} to {s['hi']:.2f} m\n"
            f"Angular washout: {s['washout_type']} "
            f"(Roll {'on' if s['washout_roll_enabled'] else 'off'}, "
            f"Pitch {'on' if s['washout_pitch_enabled'] else 'off'})"
        )
        self.info2.set(
            f"Raw out of bounds: {s['raw_out']/n:.1%}\n"
            f"Workspace limited: {s['limited']/n:.1%} ({s['workspace_mode']})"
        )
        if s['floor_enabled']:
            # The displayed minimum is the smallest modelled clearance after
            # POC workspace limiting over the *entire loaded ride*.
            self.floor_min_distance.set(
                f"Min distance to floor: {s['floor_min_applied']:.1f} mm"
            )
            self.floor_summary.set(
                f"Minimum requested: {s['floor_min_requested']:.1f} mm "
                f"at {s['floor_min_requested_time']:.2f} s\n"
                f"Closest approach after limiting: {s['floor_min_applied_time']:.2f} s\n"
                f"Below clearance target: {s['floor_below_count']/n:.1%} "
                f"({s['floor_below_count']:,} frames)\n"
                f"POC floor-limit reason: {s['floor_limited_count']/n:.1%}\n"
                f"Target: {s['floor_required']:.1f} mm "
                f"({s['floor_safety_margin']:.1f} safety + "
                f"{s['floor_hull_margin']:.1f} hull allowance)"
            )
        else:
            self.floor_min_distance.set("Min distance to floor: N/A")
            self.floor_summary.set("Not applicable: floor limiting disabled for this platform.")
        self.draw()

    def show_sweep(self):
        """Compare normalized-height amplitudes without changing playback."""
        if not self.data:
            messagebox.showinfo("Amplitude sweep", "Open a ride-study CSV first.")
            return
        try:
            # Re-run each candidate through the same stateful POC gains and
            # workspace code. Each trial has a fresh telemetry/filter state.
            self.root.configure(cursor="watch")
            self.root.update_idletasks()
            results = amplitude_sweep(self.data, self.platform.get(),
                                      float(self.intensity.get()), self.with_filters.get(),
                                      ride_name=self.loaded_ride_name)
        except Exception as exc:
            messagebox.showerror("Amplitude sweep", str(exc))
            return
        finally:
            self.root.configure(cursor="")

        popup = tk.Toplevel(self.root)
        popup.title("Normalized-height amplitude sweep")
        popup.geometry("1040x350")
        popup.transient(self.root)
        platform = self.platform.get()
        options = f"{platform} | Intensity {self.intensity.get():.0f}% | Saved gains/washout: {'on' if self.with_filters.get() else 'off'}"
        ttk.Label(popup, text=options, padding=(12, 10)).pack(anchor="w")

        columns = ("amp", "raw", "output", "workspace", "floor", "requestfloor",
                   "appliedfloor", "maxexceed", "longest")
        titles = ("Heave", "Request OOB", "Final clamp", "Workspace", "Floor limit",
                  "Min floor req", "Min floor applied", "Max excess", "Longest limit")
        widths = (74, 108, 106, 106, 102, 116, 124, 116, 116)
        table_frame = ttk.Frame(popup, padding=(12, 0, 12, 0))
        table_frame.pack(fill="both", expand=True)
        table = ttk.Treeview(table_frame, columns=columns, show="headings", height=6)
        for col, title, width in zip(columns, titles, widths):
            table.heading(col, text=title)
            table.column(col, width=width, minwidth=width, anchor="center", stretch=False)
        hscroll = ttk.Scrollbar(table_frame, orient="horizontal", command=table.xview)
        table.configure(xscrollcommand=hscroll.set)
        table.pack(fill="both", expand=True)
        hscroll.pack(fill="x")
        table.tag_configure("selected", background="#dcecfb")
        selected_amp = round(float(self.amplitude.get()))
        for row in results:
            floor_limited = row["floor_limited_percent"]
            requested = row["min_requested_floor"]
            applied = row["min_applied_floor"]
            vals = (f"{row['amplitude']}%",
                    f"{row['raw_out_percent']:.2f}%",
                    f"{row['output_clipped_percent']:.2f}%",
                    f"{row['workspace_limited_percent']:.2f}%",
                    f"{floor_limited:.2f}%" if floor_limited is not None else "N/A",
                    f"{requested:.1f} mm" if requested is not None else "N/A",
                    f"{applied:.1f} mm" if applied is not None else "N/A",
                    f"{row['max_raw_exceed_mm']:.1f} mm",
                    f"{row['longest_intervention_s']:.2f} s")
            table.insert("", "end", values=vals,
                         tags=("selected",) if row["amplitude"] == selected_amp else ())
        ttk.Label(
            popup,
            text="OOB = requested muscle length outside physical range. "
                 "Output clipped = length clamped after workspace limiting. "
                 "Intervention = workspace limiting or final length clamp.",
            wraplength=1020, padding=(12, 10)
        ).pack(anchor="w", fill="x")
        ttk.Button(popup, text="Close", command=popup.destroy).pack(anchor="e", padx=12, pady=(0, 10))

    def jump(self, delta):
        if self.frames:
            self.frame_index.set(max(0, min(len(self.frames)-1, self.frame_index.get()+delta)))
            self.draw()

    def toggle_play(self):
        if not self.frames:
            return
        self.playing = not self.playing
        self.play_btn.configure(text="Pause" if self.playing else "Play")
        if self.playing:
            self.play_tick()

    def play_tick(self):
        if not self.playing or not self.frames:
            return
        i = self.frame_index.get()
        if i >= len(self.frames)-1:
            self.playing = False
            self.play_btn.configure(text="Play")
            return
        next_i = i + 1
        dt = max(1, min(250, int((self.frames[next_i]['time']-self.frames[i]['time']) * 1000)))
        self.frame_index.set(next_i)
        self.draw()
        self.root.after(dt, self.play_tick)

    def _draw_gradient_segment(self, x0, y0, x1, y1, start_rgb, end_rgb, steps=24):
        if y1 <= y0:
            return
        dy = (y1 - y0) / max(1, steps)
        for i in range(steps):
            seg_y0 = y0 + i * dy
            seg_y1 = y0 + (i + 1) * dy if i < steps - 1 else y1
            frac = i / max(1, steps - 1)
            rgb = tuple(round(a + (b - a) * frac) for a, b in zip(start_rgb, end_rgb))
            colour = '#%02x%02x%02x' % rgb
            self.canvas.create_rectangle(x0, seg_y0, x1, seg_y1, fill=colour, outline="")

    def draw(self):
        self.canvas.delete("all")
        if not self.frames or not self.stats:
            return

        frame = self.frames[min(self.frame_index.get(), len(self.frames)-1)]
        if self.stats['floor_enabled']:
            self.floor_current.set(
                f"Current frame ({frame['time']:.2f} s):\n"
                f"Requested: {frame['requested_floor']:.1f} mm\n"
                f"After limiting: {frame['applied_floor']:.1f} mm"
            )
        else:
            self.floor_current.set("Current frame: no floor clearance calculation")
        w = max(500, self.canvas.winfo_width())
        h = max(420, self.canvas.winfo_height())

        top = 28
        bottom = h - 82
        left = 56
        right = w - 48
        if bottom <= top:
            return

        physical_min = self.stats['min_length']
        max_len = self.stats['max_length']
        display_min = max(0.0, max_len * 0.70)
        threshold = self.stats.get('active_max_length')
        if threshold is not None and not (display_min < threshold < max_len):
            threshold = None

        def y_for(val):
            val = max(display_min, min(max_len, val))
            frac = (val - display_min) / max(1e-9, (max_len - display_min))
            return top + frac * (bottom - top)

        # Background zones and scale lines
        self.canvas.create_rectangle(left, top, right, bottom, fill="#ffffff", outline="")
        if physical_min > display_min:
            self.canvas.create_rectangle(left, top, right, y_for(physical_min),
                                         fill="#f7d9d9", outline="")
        self.canvas.create_line(left, top, right, top, fill="#9aa4b2")
        self.canvas.create_line(left, bottom, right, bottom, fill="#9aa4b2")
        self.canvas.create_text(left - 8, top, text=f"{display_min:.0f} mm", anchor="e")
        self.canvas.create_text(left - 8, y_for(physical_min), text=f"{physical_min:.0f} min", anchor="e")
        self.canvas.create_text(left - 8, bottom, text=f"{max_len:.0f} mm", anchor="e")
        if threshold is not None:
            self.canvas.create_line(left, y_for(threshold), right, y_for(threshold), fill="#6941a5", width=2)
            self.canvas.create_text(right + 8, y_for(threshold), text=f"{threshold:.0f} reserve",
                                    anchor="w", fill="#6941a5")

        # six vertical tracks on the right panel
        gap = 16
        available = max(360, right - left)
        track_w = min(40, max(28, int((available - gap * 5) / 6)))
        total_w = track_w * 6 + gap * 5
        start_x = left + max(0, (available - total_w) / 2)

        for i in range(6):
            x0 = start_x + i * (track_w + gap)
            x1 = x0 + track_w
            req = frame['requested'][i]
            permitted = frame['permitted'][i]
            py = y_for(permitted)
            ry = y_for(req)

            self.canvas.create_text((x0 + x1) / 2, bottom + 28, text=f"M{i+1}",
                                    font=("Arial", 11, "bold"))
            self.canvas.create_rectangle(x0, top, x1, bottom, fill="#e5eaf0", outline="#9aa4b2")

            if physical_min > display_min:
                self.canvas.create_line(x0, y_for(physical_min), x1, y_for(physical_min), fill="#c73333", width=1)

            if threshold is None:
                # Fallback: show contraction as green fill from current length to max length.
                self.canvas.create_rectangle(x0 + 3, py, x1 - 3, bottom, fill="#2c9785", outline="")
            else:
                ty = y_for(threshold)
                # Green segment = contraction from reserve toward shorter length.
                if permitted < threshold:
                    self.canvas.create_rectangle(x0 + 3, py, x1 - 3, ty, fill="#2c9785", outline="")
                # Extension warning below reserve: orange -> red toward full extension.
                if permitted > threshold:
                    self._draw_gradient_segment(x0 + 3, ty, x1 - 3, py,
                                                (243, 161, 47), (217, 54, 54), steps=32)
                # Over-compression above physical minimum (if ever shown by requested marker, fill remains clamped)
                self.canvas.create_line(x0, ty, x1, ty, fill="#6941a5", width=2)

            # Requested (unclamped) length marker
            self.canvas.create_line(x0 - 5, ry, x1 + 5, ry, fill="#172433", width=3)
            self.canvas.create_text((x0 + x1) / 2, top - 12, text=f"{req:0.0f}", font=("Consolas", 10))

        legend_y = h - 42
        self.canvas.create_rectangle(left, legend_y - 8, left + 18, legend_y + 8, fill="#2c9785", outline="")
        self.canvas.create_text(left + 26, legend_y, anchor="w", text="contraction from reserve")
        self.canvas.create_rectangle(left + 210, legend_y - 8, left + 228, legend_y + 8, fill="#f3a12f", outline="")
        self.canvas.create_text(left + 236, legend_y, anchor="w", text="extension warning")
        self.canvas.create_line(left + 390, legend_y, left + 410, legend_y, fill="#172433", width=3)
        self.canvas.create_text(left + 418, legend_y, anchor="w", text="requested length")

        self.status.set(
            f"t={frame['time']:.2f}s  height={frame['height']:.2f}m  "
            f"request={'OUTSIDE LIMITS' if frame['raw_out'] else 'within bounds'}  "
            f"reason={frame['reason'] or '-'}  scale={frame['scale']:.3f}. "
            f"Display window: {display_min:.0f}..{max_len:.0f} mm; top zone above {physical_min:.0f} mm is over-compression."
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", nargs="*", help="Ride-study CSV files")
    parser.add_argument("--batch", action="store_true", help="Analyze/export without opening a GUI")
    parser.add_argument("--platform", choices=sorted(PLATFORMS), default="platform_v2")
    parser.add_argument("--heave", choices=["normalized", "recorded", "vertical-g"], default="normalized")
    parser.add_argument("--amplitude", type=float, default=80.0, help="Normalized height amplitude percent")
    parser.add_argument("--intensity", type=float, default=100.0)
    parser.add_argument("--no-filters", action="store_true", help="Disable saved POC gains/washout")
    parser.add_argument("--output-dir", type=Path, help="Batch results directory")
    args = parser.parse_args()
    if args.batch:
        if not args.csv:
            parser.error("--batch requires one or more CSV paths")
        if not 0 <= args.amplitude <= 100 or not 0 <= args.intensity <= 100:
            parser.error("amplitude and intensity must be 0..100 percent")
        mode = {"normalized": "Normalized height", "recorded": "Recorded heave",
                "vertical-g": "Vertical G"}[args.heave]
        destination = args.output_dir if args.output_dir else new_export_dir()
        successes, failures = batch_export(args.csv, args.platform, mode,
                                           args.amplitude / 100, args.intensity,
                                           not args.no_filters, destination)
        return 0 if not failures else 1
    window = tk.Tk()
    app = AnalyzerApp(window, args.csv[0] if args.csv else None)
    if len(args.csv) > 1:
        app.batch_paths = args.csv
        app.export_batch()
    window.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
