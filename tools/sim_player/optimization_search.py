# optimization_search.py ver 1
"""Deterministic bounded search using production POC motion/geometry methods.

Screening uses a small set of actual frames AFTER whole-trace filtering. All
finalists are checked on EVERY frame and independently replayed by the normal
player. There is no new kinematics or workspace algorithm here.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math
from pathlib import Path
from typing import Callable

import numpy as np

from core.telemetry_config import TelemetryConfig
from sims.nolimits2 import config as nl2_config
from sims.nolimits2.angular_cues import EULER, MODES, gravity_up_local, map_angular_cue
from tools.analyze_nl2_csv import make_backend, lengths_for_real
from .variant_presets import apply_heave

AXES = ("surge", "sway", "heave", "roll", "pitch", "yaw")
PRIMARY = (3, 4, 2)
ROOT = Path(__file__).resolve().parents[2]
EPS = 1.e-7


@dataclass(frozen=True)
class Parameters:
    cue: str
    gains: tuple[int, ...]
    neutral_mm: float

    def __post_init__(self):
        if self.cue not in MODES or len(self.gains) != 6:
            raise ValueError("Invalid optimisation parameters")
        if any(type(v) is not int or not 0 <= v <= 200 for v in self.gains):
            raise ValueError("Search gains must be whole percentages in 0..200")
        if not math.isfinite(self.neutral_mm) or not -100 <= self.neutral_mm <= 100:
            raise ValueError("Search neutral offset must be within +/-100 mm")


@dataclass(frozen=True)
class SearchOptions:
    evaluations: int = 900
    sample_frames: int = 128
    starts: int = 3
    alternatives: int = 1
    neutral_min: float = -100.0
    neutral_max: float = 100.0

    def __post_init__(self):
        if self.evaluations < 80 or self.sample_frames < 12 or self.starts < 1:
            raise ValueError("Need >=80 evaluations, >=12 screening frames and >=1 start")
        if not -100 <= self.neutral_min <= self.neutral_max <= 100:
            raise ValueError("Neutral search range must be ordered within +/-100 mm")
        if not 0 <= self.alternatives <= 3:
            raise ValueError("Alternatives must be between 0 and 3")


@dataclass
class Capture:
    path: Path
    park: str
    ride: str
    rows: list
    csv_fields: list[str]
    csv_rows: list[dict]
    sha256: str
    quaternions: list[tuple] | None
    rotation: dict


def rotation_evidence(quaternions: list[tuple] | None) -> dict:
    """Conservative automatic full-turn detection, not a ride-name whitelist.

    Unwrap projected gravity Roll and Pitch only where that projection is
    meaningful. A >=330 degree excursion PLUS inverted samples enables cue
    comparison. Yaw-only turning does not. Explicit CLI overrides are available
    for ambiguous recordings; missing quaternions never mean 'no rotation'.
    """
    if not quaternions:
        return {"full_rotation": False, "known": False,
                "reason": "No complete quaternion series; retain current cue only"}
    up = np.asarray([gravity_up_local(q) for q in quaternions])
    spans = []
    for lateral in (up[:, 0], -up[:, 2]):
        valid = np.hypot(lateral, up[:, 1]) > .15
        angles = np.arctan2(lateral, up[:, 1])
        # Do not bridge a singularity or a missing sample with an invented turn.
        spans_for_axis = []
        start = 0
        while start < len(angles):
            if not valid[start]:
                start += 1
                continue
            end = start + 1
            while end < len(angles) and valid[end]:
                end += 1
            unwrapped = np.unwrap(angles[start:end])
            spans_for_axis.append(float(np.ptp(unwrapped)))
            start = end
        spans.append(max(spans_for_axis, default=0.0))
    inverted = bool(np.any(up[:, 1] < -.1))
    detected = inverted and max(spans, default=0.0) >= math.radians(330)
    return {"full_rotation": bool(detected), "known": True,
            "roll_excursion_deg": math.degrees(spans[0]),
            "pitch_excursion_deg": math.degrees(spans[1]), "inverted": inverted,
            "reason": "Full-turn evidence" if detected else "No full-turn evidence; retain current cue"}


def prepare_normalized(capture: Capture, snapshot: dict, cue: str, global_settings: dict) -> np.ndarray:
    """Whole-capture filtering, fresh state each cue, using production classes.

    Constant nonnegative per-axis gains commute with the current disabled,
    classical and homogeneous legacy filters. Search scales these precomputed
    outputs; normal player replay independently validates finalists from raw
    telemetry with gains BEFORE filters. Saturation is AFTER filtering in POC.
    """
    rows = apply_heave(capture.rows, snapshot,
                       intensity_factor=float(nl2_config.TELEMETRY_INTENSITY_FACTOR),
                       initial_lift_m=float(nl2_config.INITIAL_LIFT_HEIGHT_M))
    if cue != EULER and capture.quaternions is None:
        raise ValueError(f"{capture.path.name}: {cue} requires valid captured quaternions")
    telem = TelemetryConfig(ROOT / "sims/nolimits2/telemetry_config.ini")
    telem.apply_values(global_settings)
    telem.set_temporary_classical_z(snapshot["heave_mode"] == "vertical_g")
    telem.set_angular_washout_overrides(snapshot.get("roll_washout"), snapshot.get("pitch_washout"))
    output = []
    for i, row in enumerate(rows):
        values = row[2]
        if cue != EULER:
            values = map_angular_cue(values, capture.quaternions[i], cue,
                roll_gain=float(nl2_config.TELEMETRY_INTENSITY_FACTOR) * float(nl2_config.ROLL_SCALE) / math.pi,
                pitch_gain=float(nl2_config.TELEMETRY_INTENSITY_FACTOR) * float(nl2_config.PITCH_SCALE) * .6)
        dt = row[0] - rows[i - 1][0] if i else .05
        output.append(telem.process(values, 100, dt))
    result = np.asarray(output, dtype=float)
    if not np.isfinite(result).all():
        raise ValueError(f"Nonfinite processed motion in {capture.path.name}")
    result.flags.writeable = False
    return result


def motion_amplitudes(arrays: list[np.ndarray]) -> tuple[float, ...]:
    """Motion variation in fractions of each platform's 1-DOF range.

    Subtracting the DC value means a constant heave bias cannot win the score.
    Equal weighting by recording prevents longer/duplicate recordings dominating.
    """
    all_scores = []
    for values in arrays:
        spread = np.percentile(values, (5, 95), axis=0)
        rms = np.sqrt(np.mean((values - np.mean(values, axis=0)) ** 2, axis=0))
        all_scores.append(.5 * rms + .25 * (spread[1] - spread[0]))
    return tuple(float(v) for v in np.mean(all_scores, axis=0))


def priority_scores(amplitudes: tuple[float, ...], active_primary: tuple[int, ...]) -> tuple[float, float, float]:
    values = [amplitudes[i] for i in active_primary]
    if not values:
        primary = 0.0
    else:
        # Balance Roll/Pitch/Heave rather than letting one replace the others.
        geometric = math.prod(values) ** (1.0 / len(values))
        primary = .7 * sum(values) / len(values) + .3 * geometric
    return (primary, amplitudes[5], (amplitudes[0] + amplitudes[1]) / 2)


@dataclass(frozen=True)
class Evaluation:
    parameters: Parameters
    scores: tuple[float, float, float]
    amplitudes: tuple[float, ...]
    excess_mm: float
    floor_deficit_mm: float
    reserve_excess_mm: float
    saturation_fraction: float
    neutral_valid: bool = True

    def feasible(self, allowance_mm: float) -> bool:
        return (self.neutral_valid and self.excess_mm <= allowance_mm + EPS
                and self.floor_deficit_mm <= EPS
                and (allowance_mm > 0 or self.reserve_excess_mm <= EPS))

    def rank(self, allowance_mm: float) -> tuple:
        if not self.feasible(allowance_mm):
            violation = (max(0.0, self.excess_mm - allowance_mm)
                         + 2 * self.floor_deficit_mm
                         + (self.reserve_excess_mm if allowance_mm == 0 else 0))
            return (0, -violation, *self.scores)
        # A lower tier cannot buy more than 0.5% of normalised primary range.
        # Within a bin, retain Yaw, then translations; exact scores break ties.
        a, b, c = self.scores
        return (1, int(a / .005 + 1e-10), int(b / .005 + 1e-10),
                int(c / .005 + 1e-10), a, b, c,
                -self.saturation_fraction, -self.excess_mm,
                -sum(self.parameters.gains), -abs(self.parameters.neutral_mm),
                self.parameters.cue, self.parameters.gains, self.parameters.neutral_mm)


class Evaluator:
    def __init__(self, captures: list[Capture], platform: str, snapshot: dict,
                 cues: tuple[str, ...], global_settings: dict, options: SearchOptions):
        self.captures, self.platform, self.snapshot = captures, platform, snapshot
        self.options = options
        self.backend = make_backend(platform)
        self.stroke_mm = float(self.backend.cfg.MUSCLE_MAX_LENGTH - self.backend.cfg.MUSCLE_MIN_LENGTH)
        self.floor_required = (self.backend._workspace_min_floor_clearance + self.backend._workspace_hull_margin
                               if self.backend._workspace_hull_xy else None)
        self.prepared = {cue: [prepare_normalized(c, snapshot, cue, global_settings) for c in captures]
                         for cue in cues}
        baseline_amplitude = np.max([motion_amplitudes([np.clip(a, -1, 1) for a in arrays])
                                     for arrays in self.prepared.values()], axis=0)
        self.active_primary = tuple(i for i in PRIMARY if baseline_amplitude[i] >= .005)
        self.indices = []
        for j, capture in enumerate(captures):
            n = len(capture.rows)
            chosen = set(np.linspace(0, n - 1, min(options.sample_frames, n), dtype=int))
            for arrays in self.prepared.values():
                a = arrays[j]
                for axis in range(6):
                    for index in (int(np.argmin(a[:, axis])), int(np.argmax(a[:, axis]))):
                        chosen.update(k for k in (index - 1, index, index + 1) if 0 <= k < n)
            self.indices.append(chosen)
        self.cache: dict[Parameters, Evaluation] = {}
        self.full_cache: dict[Parameters, Evaluation] = {}
        self.evaluations = 0
        self.screen_evaluations = 0
        self._trial_ids = set()
        self.full_evaluations = 0
        self.neutral_cache: dict[float, bool] = {}
        self.bad_frames: list[set[int]] = [set() for _ in captures]

    def valid_neutral(self, offset: float) -> bool:
        if offset not in self.neutral_cache:
            try:
                self.backend.set_ride_neutral_height_delta_mm(offset)
                anchor = self.backend._ride_neutral_real_transform()
                act = self.backend._actuator_lengths_for_real_transform(anchor)
                maximum = float(self.backend.cfg.MAX_ACTUATOR_LENGTH)
                if self.backend.workspace_limit_mode == "reserve":
                    maximum = float(self.backend.cfg.MAX_ACTIVE_ACTUATOR_LENGTH)
                ok = self.backend._actuator_bounds_ok(act, maximum)
                floor = self.backend._floor_clearance_for_real_transform(anchor)
                ok = ok and (floor is None or floor >= self.floor_required - EPS)
            except ValueError:
                ok = False
            self.neutral_cache[offset] = bool(ok)
        return self.neutral_cache[offset]

    def evaluate(self, parameters: Parameters, *, full=False) -> Evaluation:
        cache = self.full_cache if full else self.cache
        if parameters in cache:
            return cache[parameters]
        self.full_evaluations += int(full)
        if not full:
            self.screen_evaluations += 1
            self._trial_ids.add(parameters)
            self.evaluations = len(self._trial_ids)
        gain = np.asarray(parameters.gains, dtype=float) / 100
        unclipped = [a * gain for a in self.prepared[parameters.cue]]
        clipped = [np.clip(a, -1, 1) for a in unclipped]
        amplitudes = motion_amplitudes(clipped)
        scores = priority_scores(amplitudes, self.active_primary)
        saturation = float(np.mean([np.mean(np.abs(a) > 1) for a in unclipped]))
        if not self.valid_neutral(parameters.neutral_mm):
            result = Evaluation(parameters, scores, amplitudes, 1e9, 1e9, 1e9, saturation, False)
            cache[parameters] = result
            return result
        backend = self.backend
        backend.set_ride_neutral_height_delta_mm(parameters.neutral_mm)
        lo, hi = float(backend.cfg.MUSCLE_MIN_LENGTH), float(backend.cfg.MUSCLE_MAX_LENGTH)
        active_hi = (float(backend.cfg.MAX_ACTIVE_ACTUATOR_LENGTH - backend.cfg.FIXED_HARDWARE_LENGTH)
                     if backend.workspace_limit_mode == "reserve" else hi)
        excess = floor_deficit = reserve_excess = 0.0
        for ci, array in enumerate(clipped):
            indices = range(len(array)) if full else sorted(self.indices[ci])
            bad = []
            for index in indices:
                # Tuple protects immutable prepared data from norm_to_real's
                # intentional in-place normalised-axis clipping.
                real = backend.real_with_ride_neutral_offset(backend.k.norm_to_real(tuple(array[index])))
                lengths = lengths_for_real(backend, real)
                over = max(lo - min(lengths), max(lengths) - hi, 0.0)
                excess = max(excess, over)
                reserve_excess = max(reserve_excess, max(lengths) - active_hi)
                floor = backend._floor_clearance_for_real_transform(real)
                deficit = max(0.0, self.floor_required - floor) if floor is not None else 0.0
                floor_deficit = max(floor_deficit, deficit)
                if full and (over > EPS or deficit > EPS):
                    bad.append((over + 2 * deficit, index))
            if full:
                # Add worst peaks AND neighbours; do not depend on frame stride.
                for _, index in sorted(bad, reverse=True)[:24]:
                    self.bad_frames[ci].update(k for k in (index - 1, index, index + 1)
                                               if 0 <= k < len(array))
        result = Evaluation(parameters, scores, amplitudes, excess, floor_deficit,
                            max(0.0, reserve_excess), saturation)
        cache[parameters] = result
        return result

    def enrich(self) -> bool:
        added = False
        for indices, bad in zip(self.indices, self.bad_frames):
            added |= not bad.issubset(indices)
            indices.update(bad)
        if added:
            self.cache.clear()
        return added


def diverse(evaluation: Evaluation, selected: list[Evaluation]) -> bool:
    a = evaluation.parameters
    return all(a.cue != b.parameters.cue or abs(a.neutral_mm - b.parameters.neutral_mm) >= 10
               or max(abs(x - y) for x, y in zip(a.gains, b.parameters.gains)) >= 15 for b in selected)


def neighbours(p: Parameters, gain_step: int, height_step: float, options: SearchOptions):
    for axis in (*PRIMARY, 5, 0, 1):
        for direction in (-1, 1):
            values = list(p.gains)
            values[axis] = max(0, min(200, values[axis] + direction * gain_step))
            if tuple(values) != p.gains:
                yield replace(p, gains=tuple(values))
    for direction in (-1, 1):
        height = round(p.neutral_mm + direction * height_step, 4)
        if options.neutral_min <= height <= options.neutral_max:
            yield replace(p, neutral_mm=height)
    # Joint moves allow coupled axes to escape single-coordinate traps.
    for a, b in ((3, 4), (3, 2), (4, 2), (5, 0), (5, 1)):
        for direction in (-1, 1):
            values = list(p.gains)
            values[a] = max(0, min(200, values[a] + direction * gain_step))
            values[b] = max(0, min(200, values[b] - direction * gain_step))
            if tuple(values) != p.gains:
                yield replace(p, gains=tuple(values))


def search_candidates(evaluator: Evaluator, baseline: Parameters, seeds: list[Parameters],
                      progress: Callable[[str], None] = lambda _s: None) -> dict[str, list[Evaluation]]:
    """Coarse height sweep, multistart local refinement, active-set validation.

    The evaluation budget bounds exploration, not final validation. No candidate
    is returned based only on the screening set. Both limits are absolute worst
    excursion per muscle/frame, never the percentage of affected frames.
    """
    options = evaluator.options
    allowances = {"strict": 0.0, "relaxed5": .05 * evaluator.stroke_mm}
    progress("coarse neutral / gain sweep")
    heights = set(float(x) for x in np.arange(options.neutral_min, options.neutral_max + .01, 20))
    heights.update((options.neutral_min, options.neutral_max))
    if options.neutral_min <= baseline.neutral_mm <= options.neutral_max:
        heights.add(baseline.neutral_mm)
    base_gains = baseline.gains
    candidates = set(seeds)
    for cue in evaluator.prepared:
        for height in sorted(heights):
            if not evaluator.valid_neutral(height):
                continue
            for scale in (1.0, .55):
                values = tuple(int(round(g * scale / 5)) * 5 for g in base_gains)
                candidates.add(Parameters(cue, values, height))
            candidates.add(Parameters(cue, (0, 0, 100, 100, 100, 25), height))
    for p in sorted(candidates, key=lambda p: (p.cue, p.neutral_mm, p.gains)):
        if options.neutral_min <= p.neutral_mm <= options.neutral_max:
            evaluator.evaluate(p)
    if not any(v.neutral_valid for v in evaluator.cache.values()):
        raise ValueError("No valid neutral height in the selected search interval")
    finalists = {key: [] for key in allowances}
    known = {p for p in evaluator.cache}
    # Round budgets leave room for hard frames found after an early shortlist.
    for round_index, budget_fraction in enumerate((.60, .85, 1.0)):
        budget = int(options.evaluations * budget_fraction)
        progress(f"search / all-frame validation round {round_index + 1}")
        for target, allowance in allowances.items():
            pool = [evaluator.evaluate(p) for p in sorted(known, key=lambda p: (p.cue, p.neutral_mm, p.gains))]
            ordered = sorted(pool, key=lambda e: e.rank(allowance), reverse=True)
            starts = []
            # Keep cue diversity before refining winners from the best region.
            for cue in evaluator.prepared:
                found = next((v for v in ordered if v.parameters.cue == cue and v.neutral_valid), None)
                if found is not None:
                    starts.append(found)
            for v in ordered:
                if len(starts) >= max(options.starts, len(evaluator.prepared)):
                    break
                if v.neutral_valid and diverse(v, starts):
                    starts.append(v)
            for start in starts:
                current = start
                for gain_step, height_step in ((25, 20.0), (10, 10.0), (5, 5.0)):
                    for _iteration in range(5):
                        choices = [current]
                        for p in neighbours(current.parameters, gain_step, height_step, options):
                            # Reserve at least half each round's remaining work
                            # for the second target instead of starving it.
                            ceiling = (budget if target == "relaxed5" else
                                       int((budget + int(options.evaluations * (0 if round_index == 0 else (.60 if round_index == 1 else .85)))) / 2))
                            if evaluator.evaluations >= max(ceiling, len(candidates)):
                                break
                            choices.append(evaluator.evaluate(p))
                            known.add(p)
                        best = max(choices, key=lambda e: e.rank(allowance))
                        if best.parameters == current.parameters:
                            break
                        current = best
                known.add(current.parameters)
            ordered = sorted((evaluator.evaluate(p) for p in known),
                             key=lambda e: e.rank(allowance), reverse=True)
            chosen = []
            for cue in evaluator.prepared:
                best_for_cue = next((v for v in ordered if v.parameters.cue == cue
                                     and v.feasible(allowance)), None)
                if best_for_cue is not None:
                    chosen.append(best_for_cue)
            for value in ordered:
                if value.feasible(allowance) and diverse(value, chosen):
                    chosen.append(value)
                if len(chosen) >= max(4, options.alternatives + 2):
                    break
            for value in chosen:
                full = evaluator.evaluate(value.parameters, full=True)
                if full.feasible(allowance) and max(full.amplitudes) > 1.e-6:
                    finalists[target].append(full)
        added = evaluator.enrich()
        if not added and all(finalists.values()) and round_index >= 1:
            break
    # Robust fallback: shrink an actually checked full trace, never export a
    # convenient all-zero pose as an 'optimised ride'. Keep the neutral fixed.
    for target, allowance in allowances.items():
        if finalists[target]:
            continue
        viable = [evaluator.evaluate(p) for p in known if evaluator.valid_neutral(p.neutral_mm)]
        best = max(viable, key=lambda e: e.rank(allowance))
        for scale in (.9, .8, .65, .5, .35, .2, .1, .05):
            gains = tuple(int(g * scale) for g in best.parameters.gains)
            p = replace(best.parameters, gains=gains)
            value = evaluator.evaluate(p, full=True)
            if value.feasible(allowance) and max(value.amplitudes) > 1.e-6:
                finalists[target].append(value)
                break
    # A strict finalist is also admissible in the relaxed category. Keep it
    # when the relaxed search found a weaker local solution; never force errors.
    finalists["relaxed5"].extend(finalists["strict"])
    result = {}
    for target, allowance in allowances.items():
        unique = {v.parameters: v for v in finalists[target]}
        ordered = sorted(unique.values(), key=lambda e: e.rank(allowance), reverse=True)
        selected = ordered[:1]
        # When comparing angular cues, first retain a useful alternative cue,
        # rather than filling every slot with small changes to the winner.
        for value in ordered[1:]:
            if len(selected) >= options.alternatives + 1:
                break
            if all(value.parameters.cue != other.parameters.cue for other in selected):
                selected.append(value)
        for value in ordered[1:]:
            if len(selected) >= options.alternatives + 1:
                break
            if diverse(value, selected):
                selected.append(value)
        result[target] = selected
    return result
