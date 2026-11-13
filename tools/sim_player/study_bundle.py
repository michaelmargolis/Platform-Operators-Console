# study_bundle.py ver 1
"""Read-only, self-describing optimiser studies for normal Sim Player opening.

The CSV keeps ORIGINAL telemetry columns. The sidecar specifies the processing
experiment; derived opt_* columns are audit data, not another motion input.
This prevents gain/cue/washout processing twice and preserves manual tuning.
"""
from __future__ import annotations

from copy import deepcopy
import csv
import hashlib
from importlib import import_module
import json
import math
from pathlib import Path

from config.platforms import PLATFORMS
from core.ride_neutral import validated_ride_neutral_delta
from core.telemetry_config import TelemetryConfig
from sims.nolimits2 import ride_config
from .tuning import validate_gain_percent

SCHEMA = "poc_optimizer_candidate_v1"
ROOT = Path(__file__).resolve().parents[2]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def bundle_path(csv_path: Path) -> Path:
    path = Path(csv_path)
    return path.with_name(path.stem + "_optimizer.json")


def model_fingerprint(platform: str) -> dict[str, str]:
    """Include inherited Chair geometry, not only its small V3 wrapper file."""
    module = import_module(PLATFORMS[platform]["sop_config_module"])
    modules = {cls.__module__ for cls in module.PlatformConfig.__mro__
               if cls.__module__ != "builtins"}
    modules.update(("config.platforms", "kinematics.kinematics_V3",
                    "core.platform_controller", "core.platform_geometry",
                    "core.ride_neutral", "core.telemetry_config",
                    "sims.nolimits2.angular_cues", "sims.nolimits2.config",
                    "sims.nolimits2.ride_config",
                    "tools.analyze_nl2_csv", "tools.sim_player.variant_presets",
                    "tools.sim_player.recording", "tools.sim_player.tuning"))
    return {name: sha256_file(Path(import_module(name).__file__))
            for name in sorted(modules)}


def checked_global_settings(value: dict) -> dict:
    """Reject corrupt archives rather than silently normalising their values."""
    if not isinstance(value, dict) or set(value) != set(TelemetryConfig.DEFAULTS):
        raise ValueError("Incomplete archived POC global settings")
    for key in ("gains", "enabled", "tau", "legacy_duration"):
        if not isinstance(value[key], (list, tuple)) or len(value[key]) != 6:
            raise ValueError(f"Archived {key} must contain six values")
    if any(type(v) is not bool for v in value["enabled"]):
        raise ValueError("Archived filter enabled flags must be boolean")
    numbers = [value["master_gain"], value["washout_gain"], *value["gains"],
               *value["tau"], *value["legacy_duration"]]
    if any(type(v) not in (float, int) or not math.isfinite(v) for v in numbers):
        raise ValueError("Nonfinite/non-numeric archived global settings")
    config = TelemetryConfig(ROOT / "sims/nolimits2/telemetry_config.ini")
    config.apply_values(value)
    canonical = config.snapshot()
    if canonical != value:
        raise ValueError("Archived global settings are outside POC's supported ranges")
    return deepcopy(canonical)


def read_candidate_bundle(csv_path: Path) -> dict | None:
    """Return validated defaults, or None for an ordinary raw recording.

    Model changes are reported, not hidden: the player recalculates with the
    installed geometry and must not imply old optimisation metrics still hold.
    Missing/modified CSV companions are errors, never silently a raw replay.
    """
    csv_path = Path(csv_path).resolve()
    sidecar = bundle_path(csv_path)
    if not sidecar.is_file():
        if csv_path.is_file():
            with csv_path.open(newline="", encoding="utf-8-sig") as stream:
                fields = next(csv.reader(stream), [])
            if "opt_schema" in fields:
                raise ValueError(f"Missing {sidecar.name}; keep it beside the optimiser CSV")
        return None
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8-sig"),
                          parse_constant=lambda v: (_ for _ in ()).throw(ValueError(v)))
        if not isinstance(data, dict) or data.get("schema") != SCHEMA:
            raise ValueError("Unsupported optimiser study schema")
        if data["csv_name"] != csv_path.name:
            raise ValueError("Optimiser sidecar names a different CSV")
        if sha256_file(csv_path) != data["csv_sha256"]:
            raise ValueError("Optimiser CSV has changed since validation; regenerate the study")
        platform = data["platform_key"]
        if platform not in PLATFORMS:
            raise ValueError(f"Unknown optimiser platform: {platform!r}")
        gains = validate_gain_percent(data["relative_gains_percent"])
        snapshot = data["variant_snapshot"]
        if not isinstance(snapshot, dict):
            raise ValueError("Study variant snapshot must be an object")
        if snapshot["park"] not in ride_config.PARKS:
            raise ValueError("Unknown optimiser park")
        if snapshot.get("platform_key") != platform:
            raise ValueError("Study and variant platforms disagree")
        ride_config.validate_variant_label_format(snapshot["label"], snapshot["park"])
        if data["angular_cue"] not in ride_config.CUES or snapshot["angular_cue"] != data["angular_cue"]:
            raise ValueError("Study angular cues disagree")
        if tuple(snapshot["gain_scale_pct"][a] for a in ride_config.AXES) != gains:
            raise ValueError("Study gains disagree")
        if snapshot["heave_mode"] not in ride_config.HEAVE_MODES:
            raise ValueError("Unsupported study heave mode")
        validated_ride_neutral_delta(snapshot["neutral_height_delta_mm"])
        data["poc_global_settings_snapshot"] = checked_global_settings(data["poc_global_settings_snapshot"])
        previous = data["model_fingerprint"]
        if not isinstance(previous, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in previous.items()):
            raise ValueError("Study model fingerprint must be an object of hashes")
        current = model_fingerprint(platform)
        data["_model_changed"] = sorted(k for k in set(previous) | set(current)
                                        if previous.get(k) != current.get(k))
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid optimiser companion {sidecar.name}: {exc}") from exc
    return data
