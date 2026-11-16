#promote.py ver 2
"""Compatibility helper for qualified Analyzer bundles; no live hardware.

Usage from POC root:
 py -m tools.nl2_dynamic_profiles.promote PATH_TO_STRICT_CANDIDATE --label "Blackbird [Dynamic A]"
The Analyzer workflow exports the bundle; POC imports it into its own catalogue.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid

from core.dynamic_profiles import (DynamicDeployment, DynamicProfileError,
                                   SCHEMA, _check_frozen_settings, sha256_file)
from core.telemetry_config import TelemetryConfig
from sims.nolimits2.variant_store import VariantStore
from sims.nolimits2 import ride_config
from tools.nl2_sync.profile import DynamicProfile, load_reference, digest_json

ROOT = Path(__file__).resolve().parents[2]


def _candidate_entry(profile, label, manifest_rel):
    frozen = profile.metadata["processing"]["base_variant"]
    p = profile.identity["park"]
    entry = {"label": label, "park": p, "status": "candidate",
             "angular_cue": frozen["angular_cue"], "heave_mode": frozen["heave_mode"],
             "gain_scale_pct": {axis+"_gain": float(frozen["gain_scale_pct"][axis])
                                for axis in ride_config.AXES},
             "roll_washout": frozen["roll_washout"],
             "pitch_washout": frozen["pitch_washout"],
             "neutral_height_delta_mm": frozen["neutral_height_delta_mm"],
             "dynamic_profile": manifest_rel}
    # Only parks with normalized-height calibration may carry this field.
    if "normalized_height" in ride_config.PARKS[p]:
        entry["normalized_height_amplitude"] = frozen["normalized_height_amplitude"]
    return entry


def promote(profile_directory, label, *, root=ROOT, catalog_dir=None, target_id=None):
    """Legacy API for offline tests: use the same qualified export/import logic.

    The Ride Analyzer UI is the documented preparation path. This callable
    remains for compatibility with research tests; it cannot bypass a valid
    qualification report or the destination catalogue's atomic merge.
    """
    from tools.nl2_dynamic_profiles.package import prepare_bundle
    from core.dynamic_import import import_candidate
    with tempfile.TemporaryDirectory(prefix='dynamic-promotion-') as temporary:
        package, relative, summary = prepare_bundle(
            profile_directory, label, export_dir=temporary, token=target_id)
        from tools.nl2_sync.profile import DynamicProfile
        profile = DynamicProfile.load(Path(profile_directory) / 'profile.json')
        installed_label, installed_relative = import_candidate(
            package, platform_key=profile.identity['platform'], root=root,
            catalog_dir=catalog_dir)
        return installed_relative


def main(argv=None):
    p = argparse.ArgumentParser(description="Verify + promote strictly qualified profile as new POC candidate (offline)")
    p.add_argument("profile_dir", type=Path, help="Directory containing profile.json and reference.npz")
    p.add_argument("--label", required=True, help="New exact unique ride variant label, e.g. 'Blackbird [Dynamic A]'")
    p.add_argument("--catalog-dir", type=Path, default=None, help="Optional alternate variant catalogue directory for offline tests")
    a = p.parse_args(argv)
    rel = promote(a.profile_dir, a.label, catalog_dir=a.catalog_dir)
    print("Promoted new candidate", a.label, "to", rel)
    print("Restart POC to load the newly verified catalogue; physical commissioning required before preferred.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
