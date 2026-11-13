# candidate_review.py ver 1
"""Qt-free optimiser-result discovery and A/B working-copy state.

The optimiser's index.csv is the authoritative inventory.  Candidates are
presented irrespective of whether the target was strict or relaxed; these are
only *starting points* for subjective tuning.  All review slots use the SAME
source-recording digest, so A/B comparison never mixes different rides/videos.
"""
from __future__ import annotations

import csv
from copy import deepcopy
from dataclasses import dataclass, field
import json
import math
import ntpath
from pathlib import Path, PureWindowsPath
from typing import Any

from config.platforms import PLATFORMS
from sims.nolimits2 import ride_config
from .study_bundle import bundle_path, read_candidate_bundle
from .tuning import validate_gain_percent

REVIEW_SCHEMA = "sim_player_candidate_review_v1"


@dataclass(frozen=True)
class CaptureChoice:
    source_id: str
    csv_path: Path
    original_name: str


@dataclass(frozen=True)
class OptimizerChoice:
    platform: str
    park: str
    target: str
    rank: int
    label: str
    cue: str
    neutral_mm: float
    gains: tuple[float, ...]
    maximum_excess_mm: float
    captures: tuple[CaptureChoice, ...]

    @property
    def name(self) -> str:
        group = "Strict" if self.target == "strict" else "5%" if self.target == "relaxed5" else self.target
        return f"{group} {self.rank:02d} — {self.cue}, neutral Δ {self.neutral_mm:+g} mm"

    def capture_for(self, source_id: str) -> CaptureChoice:
        return next(c for c in self.captures if c.source_id == source_id)


@dataclass(frozen=True)
class OptimizerRun:
    root: Path
    candidates: tuple[OptimizerChoice, ...]
    issues: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return self.root.name


def discover_run_dirs(parent: Path) -> tuple[Path, ...]:
    """Accept a run itself or a parent holding run_* directories; no recursive crawl."""
    parent = Path(parent).expanduser().resolve()
    if not parent.is_dir():
        return ()
    result = []
    if (parent / "index.csv").is_file():
        result.append(parent)
    for child in parent.iterdir():
        if child.is_dir() and child.name.startswith("run_") and (child / "index.csv").is_file():
            result.append(child)
    return tuple(sorted(result, key=lambda p: p.name, reverse=True))


def _safe_candidate_path(root: Path, raw: str) -> Path:
    # index.csv uses native Windows backslashes when produced on the POC PC.
    # Accept either separator while rejecting drive-qualified/UNC/traversal
    # paths even when tests are executed on Linux.
    windows_path = PureWindowsPath(raw)
    relative = Path(raw.replace("\\", "/"))
    if (not raw or windows_path.drive or windows_path.is_absolute() or
            relative.is_absolute() or any(p == ".." for p in relative.parts)):
        raise ValueError("Candidate CSV path must be relative to this optimiser run")
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Candidate CSV escapes optimiser run directory")
    if not path.is_file() or not bundle_path(path).is_file():
        raise ValueError(f"Missing candidate CSV or _optimizer.json: {raw}")
    return path


def _read_capture_identity(path: Path) -> tuple[str, str]:
    sidecar = bundle_path(path)
    if sidecar.stat().st_size > 1024 * 1024:
        raise ValueError("Optimiser sidecar unexpectedly large")
    data = json.loads(sidecar.read_text(encoding="utf-8-sig"))
    digest = data.get("source_sha256", "")
    if (data.get("schema") != "poc_optimizer_candidate_v1"
            or not isinstance(digest, str) or len(digest) != 64
            or any(c not in "0123456789abcdefABCDEF" for c in digest)):
        raise ValueError("Candidate has invalid source capture identity")
    original_name = ntpath.basename(str(data.get("source_csv", ""))) or path.name
    return digest.lower(), original_name


def load_run_index(root: Path) -> OptimizerRun:
    """Load every indexed candidate, keeping both strict AND both relaxed ranks."""
    root = Path(root).resolve()
    index = root / "index.csv"
    if not index.is_file():
        raise ValueError(f"No optimiser index.csv in {root}")
    groups: dict[tuple, list[CaptureChoice]] = {}
    info = {}
    issues = []
    with index.open(encoding="utf-8-sig", newline="") as stream:
        rows = csv.DictReader(stream)
        required = {"park", "platform", "target", "rank", "label", "csv", "cue", "neutral_delta_mm", "maximum_excess_mm"}
        if not required.issubset(rows.fieldnames or []):
            raise ValueError("The optimiser index is missing required v192 columns")
        for lineno, row in enumerate(rows, 2):
            try:
                platform, park = row["platform"], row["park"]
                if platform not in PLATFORMS or park not in ride_config.PARKS:
                    raise ValueError("Unknown platform or park")
                rank = int(row["rank"])
                if rank < 1:
                    raise ValueError("Invalid candidate rank")
                key = (platform, park, row["target"], rank, row["label"])
                gains = validate_gain_percent(tuple(row[a + "_gain_pct"] for a in ride_config.AXES))
                neutral = float(row["neutral_delta_mm"])
                excess = float(row["maximum_excess_mm"])
                if not (math.isfinite(neutral) and math.isfinite(excess)):
                    raise ValueError("Non-finite optimiser metrics")
                path = _safe_candidate_path(root, row["csv"])
                digest, original_name = _read_capture_identity(path)
                candidate_data = (row["cue"], neutral, gains)
                if key in info and info[key] != candidate_data:
                    raise ValueError("Candidate settings differ between its captures")
                if any(c.source_id == digest for c in groups.get(key, ())):
                    raise ValueError("Duplicate capture for a candidate")
                info[key] = candidate_data
                groups.setdefault(key, []).append(CaptureChoice(digest, path, original_name))
            except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError) as exc:
                issues.append(f"index.csv line {lineno}: {exc}")
    choices = []
    for key, captures in groups.items():
        platform, park, target, rank, label = key
        cue, neutral, gains = info[key]
        # Metrics may vary per capture; show worst available magnitude.
        worst = 0.0
        for c in captures:
            try:
                data = json.loads(bundle_path(c.csv_path).read_text(encoding="utf-8-sig"))
                worst = max(worst, float(data["validation"]["maximum_excess_mm"]))
            except (OSError, KeyError, TypeError, ValueError):
                pass  # The complete bundle is validated again before actual opening.
        choices.append(OptimizerChoice(platform, park, target, rank, label, cue, neutral,
                                       gains, worst, tuple(captures)))
    choices.sort(key=lambda c: (c.platform, c.park, c.target != "strict", c.target, c.rank, c.label))
    return OptimizerRun(root, tuple(choices), tuple(issues))


def shared_captures(first: OptimizerChoice, second: OptimizerChoice | None) -> tuple[CaptureChoice, ...]:
    if second is not None and (first.park, first.platform) != (second.park, second.platform):
        raise ValueError("A and B must belong to the same park and platform")
    eligible = {c.source_id for c in second.captures} if second else None
    return tuple(c for c in first.captures if eligible is None or c.source_id in eligible)


def checked_selection(first: OptimizerChoice, second: OptimizerChoice | None, source_id: str) -> tuple[tuple[Path, dict], ...]:
    """Full v192 validation: CSV checksums, sidecars, platform and source identity."""
    if not any(c.source_id == source_id for c in shared_captures(first, second)):
        raise ValueError("Candidates do not share this capture")
    items = []
    for choice in (first, second):
        if choice is None:
            continue
        csv_path = choice.capture_for(source_id).csv_path
        bundle = read_candidate_bundle(csv_path)
        if bundle is None or bundle.get("source_sha256", "").lower() != source_id:
            raise ValueError("Candidate's source capture differs from the selected recording")
        if bundle["platform_key"] != first.platform or bundle["variant_snapshot"]["park"] != first.park:
            raise ValueError("Candidate platform/park is inconsistent with the index")
        if (bundle["target"] != choice.target or bundle["variant_snapshot"]["label"] != choice.label
                or bundle["angular_cue"] != choice.cue
                or validate_gain_percent(bundle["relative_gains_percent"]) != choice.gains
                or float(bundle["variant_snapshot"]["neutral_height_delta_mm"]) != choice.neutral_mm):
            raise ValueError("Optimiser index and candidate sidecar describe different configurations")
        items.append((csv_path, bundle))
    return tuple(items)


@dataclass
class WorkingSlot:
    choice: OptimizerChoice
    csv_path: Path
    source_bundle: dict
    mode: str
    gains: tuple[float, ...]
    snapshot: dict
    globals: dict
    original_recording: Any = None
    recording: Any = None
    saved_label: str | None = None
    pending_override: tuple | None = None

    @classmethod
    def from_bundle(cls, choice, csv_path, bundle):
        return cls(choice, csv_path, bundle, bundle["angular_cue"],
                   validate_gain_percent(bundle["relative_gains_percent"]),
                   deepcopy(bundle["variant_snapshot"]),
                   deepcopy(bundle["poc_global_settings_snapshot"]))

    def incorporate(self, recording):
        previous = self.recording
        if self.saved_label and previous is not None:
            if (previous.cue_mode != recording.cue_mode
                    or previous.gain_percent != recording.gain_percent
                    or previous.variant_snapshot != recording.variant_snapshot):
                self.saved_label = None  # Subsequent manual edits require a new save.
        self.recording = recording
        self.mode = recording.cue_mode
        self.gains = recording.gain_percent
        self.snapshot = deepcopy(recording.variant_snapshot)
        self.globals = deepcopy(recording.global_settings_snapshot)
        if self.original_recording is None:
            self.original_recording = recording

    @property
    def modified(self) -> bool:
        original = self.source_bundle
        return (self.mode != original["angular_cue"]
                or tuple(self.gains) != tuple(original["relative_gains_percent"])
                or self.snapshot != original["variant_snapshot"]
                or self.globals != original["poc_global_settings_snapshot"])

    def restore_original(self):
        self.mode = self.source_bundle["angular_cue"]
        self.gains = validate_gain_percent(self.source_bundle["relative_gains_percent"])
        self.snapshot = deepcopy(self.source_bundle["variant_snapshot"])
        self.globals = deepcopy(self.source_bundle["poc_global_settings_snapshot"])
        self.recording = self.original_recording
        self.pending_override = None
        self.saved_label = None


@dataclass
class ReviewSession:
    platform: str
    park: str
    run_root: Path
    source_id: str
    slots: dict[str, WorkingSlot] = field(default_factory=dict)
    active: str = "A"

    def to_dict(self) -> dict:
        return {"schema": REVIEW_SCHEMA, "platform": self.platform, "park": self.park,
                "run_root": str(self.run_root), "source_sha256": self.source_id,
                "active": self.active, "slots": {
                    key: {"candidate_csv": str(slot.csv_path),
                          "cue": slot.pending_override[0] if slot.pending_override else slot.mode,
                          "gains": list(slot.pending_override[1] if slot.pending_override else slot.gains),
                          "variant_snapshot": slot.pending_override[2] if slot.pending_override else slot.snapshot,
                          "global_settings": slot.pending_override[3] if slot.pending_override else slot.globals}
                    for key, slot in self.slots.items()}}


def prepare_session(first, second, source_id, run_root) -> ReviewSession:
    validated = checked_selection(first, second, source_id)
    session = ReviewSession(first.platform, first.park, Path(run_root).resolve(), source_id)
    for key, choice, item in zip(("A", "B"), (c for c in (first, second) if c is not None), validated):
        csv_path, bundle = item
        session.slots[key] = WorkingSlot.from_bundle(choice, csv_path, bundle)
    return session


def reopen_session(path: Path) -> ReviewSession:
    """Resume an A/B study, rechecking original CSV/sidecars before trusting edits."""
    raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(raw, dict) or raw.get("schema") != REVIEW_SCHEMA:
        raise ValueError("Not a Sim Player candidate-review session")
    run = load_run_index(raw["run_root"])
    selections = []
    for key in ("A", "B"):
        slot = raw["slots"].get(key)
        if slot is None:
            continue
        saved_path = Path(slot["candidate_csv"]).resolve()
        matching = [c for c in run.candidates if c.platform == raw["platform"]
                    and c.park == raw["park"]
                    and any(k.csv_path == saved_path for k in c.captures)]
        if len(matching) != 1:
            raise ValueError(f"Saved {key} optimiser candidate is missing or ambiguous")
        selections.append((key, matching[0], slot))
    if not selections or selections[0][0] != "A":
        raise ValueError("A review session needs candidate A")
    selected = {key: choice for key, choice, _ in selections}
    session = prepare_session(selected["A"], selected.get("B"), raw["source_sha256"], run.root)
    for key, _, saved in selections:
        item = session.slots[key]
        mode = saved["cue"]
        if mode not in ride_config.CUES:
            raise ValueError("Unsupported saved angular cue")
        gains = validate_gain_percent(saved["gains"])
        snapshot = saved["variant_snapshot"]
        if not isinstance(snapshot, dict) or snapshot.get("park") != session.park or snapshot.get("platform_key") != session.platform:
            raise ValueError("Saved study snapshot is for another park or platform")
        from .study_bundle import checked_global_settings
        globals_snapshot = checked_global_settings(saved["global_settings"])
        item.pending_override = (mode, gains, deepcopy(snapshot), globals_snapshot)
    session.active = raw.get("active", "A") if raw.get("active") in session.slots else "A"
    return session
