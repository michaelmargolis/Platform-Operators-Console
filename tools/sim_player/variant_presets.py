# variant_presets.py ver 2
"""Pure helpers for loading shared NL2 ride variants in offline sim_player.

Shared presets are resolved from the selected platform JSON; raw captures remain immutable.  An explicit
variant is required to replace the capture's recorded heave with calibrated
park heave. Defaults preserve v17 playback behaviour.
"""
from __future__ import annotations

import math

from sims.nolimits2 import ride_config

HEAVE_RECORDED = "recorded"


def _store(store=None, platform_key="platform_v2"):
    if store is not None:
        return store
    from sims.nolimits2.variant_store import VariantStore
    return VariantStore.load(platform_key)


def choices_for_capture(ride_name, store=None, *, platform_key="platform_v2"):
    """Player shows non-rejected candidates as well as a preferred variant."""
    park_key = ride_config.park_key_for_capture(ride_name)
    return _store(store, platform_key).player_labels(park_key) if park_key is not None else ()


def default_label_for_capture(ride_name, store=None, *, platform_key="platform_v2"):
    park_key = ride_config.park_key_for_capture(ride_name)
    return _store(store, platform_key).default_label(park_key) if park_key is not None else None


def raw_label_for_capture(ride_name):
    park_key = ride_config.park_key_for_capture(ride_name)
    return f"{ride_config.PARKS[park_key]['name'] if park_key else ride_name} raw"


def dropdown_choices_for_capture(ride_name, store=None, *, platform_key="platform_v2"):
    catalog = _store(store, platform_key)
    named = choices_for_capture(ride_name, catalog)
    standard = default_label_for_capture(ride_name, catalog)
    result = []
    if standard in named:
        result.append((standard, standard))
    result.append((raw_label_for_capture(ride_name), None))
    result.extend((label, label) for label in named if label != standard)
    return tuple(result)


def snapshot_for_capture(ride_name, variant_label, store=None, *, platform_key="platform_v2"):
    if variant_label is None:
        return None
    park_key = ride_config.park_key_for_capture(ride_name)
    if park_key is None:
        raise ValueError(f"Capture's park {ride_name!r} is not defined in ride_config.py")
    catalog = _store(store, platform_key)
    if catalog.entry(variant_label)['park'] != park_key:
        raise ValueError(f"Variant {variant_label!r} is not for recorded park {ride_name!r}")
    if variant_label not in catalog.player_labels(park_key):
        raise ValueError(f"Variant {variant_label!r} is rejected on this platform")
    return catalog.snapshot(variant_label)


def gain_tuple(snapshot):
    if snapshot is None:
        return (100.0,) * 6
    return tuple(snapshot["gain_scale_pct"][axis] for axis in ride_config.AXES)


def apply_heave(rows, snapshot, *, intensity_factor=0.4, initial_lift_m=32.0):
    """Replace recorded Z with the chosen live NL2 height cue, without mutation.

    The result must be fed to analyze(..., mode='Recorded heave'), so its
    normalized-heave option cannot reinterpret captured height extrema.
    The returned rows contain *raw normalized* inputs; operator intensity and
    saved POC gain/washout remain the shared analyzer's responsibility.

    The v176 shared analyser accepts an effective variant snapshot and applies
    the production temporary classical-Z washout for Vertical-G variants.
    """
    if snapshot is None:
        return rows
    mode = snapshot["heave_mode"]
    park = snapshot["park_info"]
    if mode == "normalized_height":
        profile = park.get("normalized_height")
        if not profile:
            raise ValueError("Normalized height variant has no calibrated park height range")
        low = float(profile["min_height_m"])
        high = float(profile["max_height_m"])
        amplitude = float(snapshot["normalized_height_amplitude"])
        if not (high > low):
            raise ValueError("Invalid calibrated height bounds")
    lift_height = float(initial_lift_m)
    modified = []
    for row in rows:
        elapsed, height, values = row[:3]
        height = float(height)
        if not math.isfinite(height):
            raise ValueError("Invalid captured height")
        if mode == "normalized_height":
            fraction = max(0.0, min(1.0, (height - low) / (high - low)))
            heave = max(-1.0, min(1.0, amplitude * (1.0 - 2.0*fraction)))
        elif mode == "vertical_g":
            heave = row[3] if len(row) >= 4 else None
            if heave is None or not math.isfinite(float(heave)):
                raise ValueError("Vertical G variant requires valid recorded vertical_g_heave")
            heave = float(heave)
        elif mode == "legacy_height":
            profile = park.get("legacy_height")
            if profile:
                fraction = (height - float(profile["bottom_height_m"])) / (
                    float(profile["lift_top_height_m"]) - float(profile["bottom_height_m"]))
                fraction = max(0.0, min(1.0, fraction))
                heave = float(profile["z_at_bottom"]) + fraction * (
                    float(profile["z_at_lift_top"]) - float(profile["z_at_bottom"]))
            else:
                lift_height = max(lift_height, height)
                heave = max(-1.0, min(1.0, (2.0 * height / lift_height - 1.0) * float(intensity_factor)))
        else:
            raise ValueError(f"Unknown heave mode: {mode!r}")
        axes = list(values)
        axes[2] = heave
        modified.append((elapsed, row[1], tuple(axes), *row[3:]))
    return modified


def python_variant_entry(label, *, park, angular_cue, gains, heave_mode=None,
                         roll_washout=None, pitch_washout=None,
                         normalized_height_amplitude=None, neutral_height_delta_mm=0.0):
    """Produce copy/paste text, never directly edit live ride_config.py."""
    from tools.sim_player.tuning import validate_gain_percent
    result = {"label": str(label), "park": str(park)}
    if angular_cue != "euler":
        result["angular_cue"] = angular_cue
    scaled = {axis + "_gain": int(value) for axis, value in zip(ride_config.AXES,
                           validate_gain_percent(gains)) if value != 100}
    if scaled:
        result["gain_scale_pct"] = scaled
    if heave_mode is not None:
        result["heave_mode"] = heave_mode
    if roll_washout is not None:
        result["roll_washout"] = roll_washout
    if pitch_washout is not None:
        result["pitch_washout"] = pitch_washout
    if normalized_height_amplitude is not None:
        amplitude = float(normalized_height_amplitude)
        if not math.isfinite(amplitude) or not 0 <= amplitude <= 1:
            raise ValueError("Normalized height amplitude must be within 0..1")
        result["normalized_height_amplitude"] = amplitude
    from core.ride_neutral import validated_ride_neutral_delta
    delta = validated_ride_neutral_delta(neutral_height_delta_mm)
    if delta:
        result["neutral_height_delta_mm"] = delta
    return repr(result) + ","


def candidate_entry(label, recording):
    """Explicit live-heave snapshot required; raw replay is not a live preset."""
    snapshot = recording.variant_snapshot
    if snapshot is None:
        raise ValueError("Select a named ride configuration before saving a live variant")
    from .tuning import validate_gain_percent
    gains = validate_gain_percent(recording.gain_percent)
    entry = {'label': label, 'park': snapshot['park'], 'status': 'candidate',
             'angular_cue': recording.cue_mode, 'heave_mode': snapshot['heave_mode']}
    entry['gain_scale_pct'] = {axis + '_gain': value for axis, value in zip(ride_config.AXES, gains)
                               if value != 100}
    for key in ('roll_washout', 'pitch_washout'):
        if snapshot.get(key) is not None:
            entry[key] = snapshot[key]
    delta = float(snapshot.get('neutral_height_delta_mm', 0.0))
    if delta:
        entry['neutral_height_delta_mm'] = delta
    amplitude = snapshot.get('normalized_height_amplitude')
    default = ride_config.PARKS[entry['park']].get('normalized_height', {}).get('amplitude')
    if amplitude is not None and default is not None and float(amplitude) != float(default):
        entry['normalized_height_amplitude'] = float(amplitude)
    # Validate now, before the dialog can commit to disk; store also validates.
    ride_config.validate(variants=[entry], show_variant_modifiers=True)
    return entry
