# test_create_canonical_rides_v230.py ver 1
"""Qt/OBS/NL2-free checks for the canonical ride capture utility."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest

PATH = Path(__file__).resolve().parents[1] / "tools" / "create_canonical_rides.py"
spec = importlib.util.spec_from_file_location("create_canonical_rides", PATH)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def rides():
    return (
        module.RideSpec("blackbird", "Blackbird", "b.nl2park", 0, ("chair_v3", "platform_v2")),
        module.RideSpec("fenrir", "Fenrir", "f.nl2park", 1, ("chair_v3",)),
        module.RideSpec("tiltcoaster", "Tilt Coaster", "t.nl2park", 2, ("platform_v2",)),
    )


def test_unique_park_union_is_one_physical_ride_per_platform_catalog_union():
    entries = {
        "chair_v3": ({"park": "blackbird"}, {"park": "blackbird"}, {"park": "fenrir"}),
        "platform_v2": ({"park": "blackbird"}, {"park": "tiltcoaster"}),
    }
    assert module.unique_park_keys(entries, ("fenrir", "blackbird", "tiltcoaster", "unused")) == (
        "fenrir", "blackbird", "tiltcoaster")


def test_ride_selection_accepts_key_name_all_and_deduplicates():
    rows = rides()
    assert module.selected_park_keys(None, rows) == ("blackbird", "fenrir", "tiltcoaster")
    assert module.selected_park_keys("all", rows) == ("blackbird", "fenrir", "tiltcoaster")
    assert module.selected_park_keys("Fenrir,blackbird,Fenrir", rows) == ("fenrir", "blackbird")
    with pytest.raises(ValueError, match="Unrecognized ride"):
        module.selected_park_keys("unknown", rows)


def test_video_anchor_uses_existing_obs_to_telemetry_monotonic_estimate():
    manifest = {"approx_csv_zero_after_obs_ack_s": 1.375}
    assert module.video_anchor_from_manifest(manifest) == [[0.0, 1.375]]


def test_video_anchor_can_reconstruct_from_event_markers():
    manifest = {"events": {
        "obs_start_ack": {"monotonic_s": 100.25},
        "capture_elapsed_zero": {"monotonic_s": 101.80},
    }}
    assert module.video_anchor_from_manifest(manifest) == [[0.0, 1.55]]


def test_video_anchor_rejects_missing_or_implausible_clock():
    with pytest.raises(RuntimeError, match="no usable"):
        module.video_anchor_from_manifest({})
    with pytest.raises(RuntimeError, match="Implausible"):
        module.video_anchor_from_manifest({"approx_csv_zero_after_obs_ack_s": 1000})


def test_vr_state_bit_0x08_is_detected_only_when_set():
    assert module.vr_active_from_state(0x08)
    assert module.vr_active_from_state(0x0C)
    assert not module.vr_active_from_state(0x04)
    assert not module.vr_active_from_state(None)


def test_defaults_keep_canonical_capture_simple_and_non_vr():
    parser = module.build_parser()
    args = parser.parse_args([])
    assert args.caption_delay == 5.0
    assert args.obs_lead == 0.75
    assert args.allow_vr is False
    assert args.replace is False
    assert str(args.output_dir).replace("\\", "/").endswith("captures/canonical_source")
