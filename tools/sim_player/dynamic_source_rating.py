# dynamic_source_rating.py ver 1
"""Lightweight suitability hints for completed dynamic-source recordings.

The hint is deliberately conservative and uses only evidence already produced by
Timing Study. It is not a physical qualification or a dynamic-profile approval.
"""
from __future__ import annotations

from dataclasses import dataclass
import csv
import json
from pathlib import Path
from typing import Dict, Iterable, Optional


@dataclass(frozen=True)
class SuitabilityHint:
    rating: str
    summary: str
    details: tuple[str, ...] = ()

    def tooltip(self) -> str:
        lines = [f"{self.rating} - {self.summary}"]
        lines.extend(self.details)
        lines.append("Engineering hint only; this is not physical validation or deployment approval.")
        return "\n".join(lines)


def _as_int(value, default=0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _as_float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value) -> bool:
    return str(value).strip().casefold() in {"1", "true", "yes", "y"}


def _capture_name_from_trial(value: str) -> str:
    name = str(value or "").replace("\\", "/").rsplit("/", 1)[-1]
    suffix = "_trial.json"
    return name[:-len(suffix)] + ".csv" if name.casefold().endswith(suffix) else ""


def _find_session_dir(recording_path: Path) -> Optional[Path]:
    path = Path(recording_path).resolve()
    for parent in (path.parent, *path.parents):
        if parent.name.startswith("session_"):
            return parent
        # Stop once we climb out of the normal captures/dynamic_source tree.
        if parent.name == "dynamic_source":
            break
    return None


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))
    except (OSError, csv.Error):
        return []


def _run_details(row: dict[str, str]) -> tuple[str, ...]:
    details = []
    repeat = _as_int(row.get("repeat"), 0)
    duration = _as_float(row.get("motion_to_return_s"))
    max_gap = _as_float(row.get("max_unique_gap_ms"))
    gap_threshold_s = _as_float(row.get("gap_threshold_s"))
    path_m = _as_float(row.get("path_distance_m"))
    skipped = _as_int(row.get("skipped_poll_ticks"), 0)
    timestamp_faults = (
        _as_int(row.get("equal_timestamp_observations"), 0)
        + _as_int(row.get("backwards_timestamp_observations"), 0)
    )

    first = []
    if repeat:
        first.append(f"repeat {repeat}")
    if duration is not None:
        first.append(f"duration {duration:.3f} s")
    if path_m is not None:
        first.append(f"path {path_m:.2f} m")
    if first:
        details.append("Recording: " + "; ".join(first) + ".")

    second = []
    if max_gap is not None:
        if gap_threshold_s is not None:
            second.append(f"max gap {max_gap:.1f} ms (study threshold {gap_threshold_s * 1000:.0f} ms)")
        else:
            second.append(f"max gap {max_gap:.1f} ms")
    second.append(f"timestamp faults {timestamp_faults}")
    second.append(f"skipped polls {skipped}")
    if second:
        details.append("Capture integrity: " + "; ".join(second) + ".")
    return tuple(details)


def _hard_run_reasons(row: dict[str, str]) -> list[str]:
    reasons = []
    status = str(row.get("status") or "").strip().casefold()
    if status and status != "complete":
        reasons.append(f"status is {status}")
    if not _as_bool(row.get("eligible")):
        reasons.append("Timing Study marked the recording ineligible")
    backwards = _as_int(row.get("backwards_timestamp_observations"), 0)
    equal = _as_int(row.get("equal_timestamp_observations"), 0)
    if backwards:
        reasons.append(f"{backwards} backwards timestamp observation(s)")
    if equal:
        reasons.append(f"{equal} repeated/equal timestamp observation(s)")
    error = str(row.get("error") or "").strip()
    if error:
        reasons.append(f"capture error: {error}")
    return reasons


def _soft_run_reasons(row: dict[str, str]) -> list[str]:
    reasons = []
    gaps = _as_int(row.get("gaps_above_threshold"), 0)
    if gaps:
        threshold_s = _as_float(row.get("gap_threshold_s"))
        if threshold_s is None:
            reasons.append(f"{gaps} acquisition gap(s) above the study threshold")
        else:
            reasons.append(f"{gaps} acquisition gap(s) above {threshold_s * 1000:.0f} ms")
    # Duplicate protocol replies are de-duplicated by the capture layer and do
    # not by themselves make the resulting telemetry less suitable.
    skipped = _as_int(row.get("skipped_poll_ticks"), 0)
    if skipped:
        reasons.append(f"{skipped} skipped poll tick(s)")
    issues = str(row.get("issues") or "").strip()
    if issues:
        reasons.append(issues)
    return reasons


def _report_hint(recording_path: Path, session_dir: Path) -> Optional[SuitabilityHint]:
    runs = _read_csv(session_dir / "report" / "runs.csv")
    if not runs:
        return None
    by_capture: Dict[str, dict[str, str]] = {}
    for row in runs:
        capture = _capture_name_from_trial(row.get("trial", ""))
        if capture:
            by_capture[capture.casefold()] = row
    row = by_capture.get(recording_path.name.casefold())
    if row is None:
        return None

    hard = _hard_run_reasons(row)
    details = list(_run_details(row))
    if hard:
        details.insert(0, "Reason: " + "; ".join(hard) + ".")
        return SuitabilityHint("Avoid", "hard capture/eligibility issue detected", tuple(details))

    soft = _soft_run_reasons(row)

    suggested = False
    comparable_note = ""
    groups = _read_csv(session_dir / "report" / "groups.csv")
    recording_stem = recording_path.stem.casefold()
    for group in groups:
        reference = str(group.get("reference") or "").strip().casefold()
        if reference != recording_stem:
            continue
        count = _as_int(group.get("complete_measured_runs"), 0)
        note = str(group.get("note") or "").strip()
        if count >= 2:
            suggested = True
        elif note:
            comparable_note = note
        break

    if comparable_note:
        details.insert(0, "Study grouping note: " + comparable_note + ".")

    if soft:
        details.insert(0, "Reason: " + "; ".join(soft) + ".")
        return SuitabilityHint("Caution", "recording is usable but has a review point", tuple(details))

    if suggested:
        details.insert(0, "Timing Study selected this clean recording as the representative source for its comparable group.")
        return SuitabilityHint("Suggested", "clean representative source selected by the study analysis", tuple(details))

    return SuitabilityHint("Good", "clean eligible recording; not the study's representative choice", tuple(details))


def _trial_hint(recording_path: Path) -> Optional[SuitabilityHint]:
    trial = recording_path.with_name(recording_path.stem + "_trial.json")
    if not trial.exists():
        return None
    try:
        data = json.loads(trial.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    status = str(data.get("status") or "").strip().casefold()
    error = str(data.get("error") or "").strip()
    issues = [str(item).strip() for item in (data.get("quality_issues") or []) if str(item).strip()]
    overflow = _as_int(data.get("transport_overflow"), 0)
    repeat = _as_int(data.get("repeat"), 0)

    details = []
    if repeat:
        details.append(f"Recording: repeat {repeat}.")
    details.append("Full comparative study report is not available; this rating uses the recording trial metadata only.")

    hard = []
    if status and status != "complete":
        hard.append(f"status is {status}")
    if error:
        hard.append(f"capture error: {error}")
    if overflow:
        hard.append(f"transport overflow count {overflow}")
    if hard:
        details.insert(0, "Reason: " + "; ".join(hard) + ".")
        return SuitabilityHint("Avoid", "basic trial metadata shows a hard capture issue", tuple(details))
    if issues:
        details.insert(0, "Reason: " + "; ".join(issues) + ".")
        return SuitabilityHint("Caution", "basic trial metadata contains a quality warning", tuple(details))
    if status == "complete":
        return SuitabilityHint("Good", "basic trial metadata is clean", tuple(details))
    return None


def suitability_hint(recording_path: Path) -> SuitabilityHint:
    """Return a non-blocking suitability hint for one primary capture CSV."""
    path = Path(recording_path)
    session = _find_session_dir(path)
    if session is not None:
        hint = _report_hint(path, session)
        if hint is not None:
            return hint
    hint = _trial_hint(path)
    if hint is not None:
        return hint
    return SuitabilityHint(
        "Unrated",
        "no Timing Study report or trial metadata was found for this recording",
        ("The recording can still be opened and reviewed normally.",),
    )


def suitability_tooltip(recording_path: Path) -> str:
    return suitability_hint(recording_path).tooltip()
