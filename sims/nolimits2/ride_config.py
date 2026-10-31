# ride_config.py ver 4 -- shared parks/calibration and variant schema helpers.
"""NL2 park/calibration data and common variant schema validation.

Variant labels are unique IDs within one platform JSON file. No variants live here.
This module has no Qt dependencies or physical I/O. Windows park paths are
interpreted on the remote simulator PC and should not be checked locally.
"""
from __future__ import annotations

from copy import deepcopy
import math
import re
from pathlib import Path
from core.ride_neutral import validated_ride_neutral_delta

BASE_RIDE_PATH = r"C:/Program Files/NoLimits 2/parks/MDX"

# Preserve the existing park order, file paths and October 2026 height data.
# Calibrations belong to the underlying park and are shared by all variants.
PARKS = {
    "mdxlogo": {"name": "Mdx Logo Coaster", "path": f"{BASE_RIDE_PATH}/MdxLogoCoaster/MdxLogoCoaster.nl2park",
                "normalized_height": {"min_height_m": 3.2, "max_height_m": 32.7, "amplitude": 0.80},
                "legacy_height": {"bottom_height_m": 7.4, "lift_top_height_m": 32.6,
                                  "z_at_bottom": -0.50, "z_at_lift_top": -0.18}},
    "blackbird": {"name": "Blackbird", "path": f"{BASE_RIDE_PATH}/Blackbird/Blackbird.nl2park",
                  "normalized_height": {"min_height_m": 5.9, "max_height_m": 41.0, "amplitude": 0.90}},
    "fenrir": {"name": "Fenrir", "path": f"{BASE_RIDE_PATH}/Fenrir/Fenrir.nl2park",
               "normalized_height": {"min_height_m": 2.7, "max_height_m": 45.6, "amplitude": 0.70}},
    "flyingbeast": {"name": "Flying Beast", "path": f"{BASE_RIDE_PATH}/Flying Beast/Flying Beast.nl2park"},
    "forestmountain": {"name": "Forest Mountain Park", "path": f"{BASE_RIDE_PATH}/Forest Mountain Park/Forest Mountain Park.nl2park",
                       "normalized_height": {"min_height_m": 4.6, "max_height_m": 48.1, "amplitude": 0.90}},
    "grandnational": {"name": "GrandNational", "path": f"{BASE_RIDE_PATH}/GrandNational/GrandNational.nl2park",
                      "normalized_height": {"min_height_m": 2.5, "max_height_m": 21.2, "amplitude": 0.90}},
    "hybris": {"name": "Hybris", "path": f"{BASE_RIDE_PATH}/Hybris/Hybris.nl2park",
               "normalized_height": {"min_height_m": -3.9, "max_height_m": 87.8, "amplitude": 0.90}},
    "scenicrun": {"name": "Scenic Run", "path": f"{BASE_RIDE_PATH}/Scenic Run/Scenic Run.nl2park",
                  "normalized_height": {"min_height_m": 2.8, "max_height_m": 99.8, "amplitude": 0.90}},
    "tiltcoaster": {"name": "Tilt Coaster", "path": f"{BASE_RIDE_PATH}/Tilt Coaster/Tilt Coaster.nl2park"},
    "wilderness": {"name": "Wilderness Park", "path": f"{BASE_RIDE_PATH}/Wilderness Park/Wilderness Park.nl2park",
                   "normalized_height": {"min_height_m": -20.5, "max_height_m": 72.1, "amplitude": 0.90}},
}

# Operator UI only. Full variant labels remain the unique IDs in logs and code.
# Set False for deployment. Duplicate visible names (after curation filtering)
# are a POC startup configuration error; the player always shows full labels.
SHOW_VARIANT_MODIFIERS = True

DEFAULT_VARIANT_LABEL = "Mdx Logo Coaster"

AXES = ("surge", "sway", "heave", "roll", "pitch", "yaw")
CUES = frozenset(("euler", "sine", "gravity"))
HEAVE_MODES = frozenset(("legacy_height", "vertical_g", "normalized_height"))
# A nonstandard variant name is the exact park name plus one or more
# space-prefixed [modifier] groups; modifiers may not contain brackets.
_BRACKET_MODIFIER = re.compile(r"(?: \[[^\[\]\s][^\[\]]*\])+")
VARIANT_FIELDS = frozenset(("label", "park", "angular_cue", "gain_scale_pct",
                             "roll_washout", "pitch_washout", "heave_mode",
                             "normalized_height_amplitude", "neutral_height_delta_mm", "status"))


def validate_variant_label_format(label, park_key, parks=PARKS):
    """Check stable label syntax without changing its underlying identifier.

    Only optional space-prefixed bracket modifiers may follow the exact park name.
    """
    park_name = parks[park_key]["name"]
    suffix = label[len(park_name):] if label.startswith(park_name) else ""
    if label != park_name and not _BRACKET_MODIFIER.fullmatch(suffix):
        raise ValueError(
            f"Variant {label!r} must be {park_name!r} followed by "
            "one or more space-separated [modifiers]")


def validate(parks=PARKS, variants=None, default=None,
             show_variant_modifiers=None):
    """Fail at startup on ambiguous display names or invalid configuration.

    Full unique variant labels are keys regardless of operator display mode.
    """
    if variants is None:
        # Compatibility for analysis scripts; production uses its own store.
        from sims.nolimits2.variant_store import VariantStore
        variants = VariantStore.load("platform_v2").entries()
    if not isinstance(variants, (tuple, list)):
        raise ValueError("Variants must be an ordered list")
    if show_variant_modifiers is None:
        show_variant_modifiers = SHOW_VARIANT_MODIFIERS
    if type(show_variant_modifiers) is not bool:
        raise ValueError("SHOW_VARIANT_MODIFIERS must be True or False")
    if not isinstance(parks, dict) or not parks:
        raise ValueError("PARKS must be a nonempty dictionary")
    for park_key, park in parks.items():
        if not isinstance(park_key, str) or not park_key.strip():
            raise ValueError("Park key must be a nonempty string")
        if not isinstance(park, dict) or not all(isinstance(park.get(k), str) and park[k].strip()
                                                   for k in ("name", "path")):
            raise ValueError(f"Park {park_key!r} requires a name and path")
        if '[' in park['name'] or ']' in park['name']:
            raise ValueError(f"Park name cannot contain square brackets: {park_key!r}")
        if "normalized_height" in park:
            h = park["normalized_height"]
            try:
                low = float(h["min_height_m"])
                high = float(h["max_height_m"])
                amplitude = float(h["amplitude"])
            except (ValueError, TypeError, KeyError) as exc:
                raise ValueError(f"Bad height calibration for park {park_key!r}") from exc
            if not all(map(math.isfinite, (low, high, amplitude))) or high <= low or not 0 <= amplitude <= 1:
                raise ValueError(f"Invalid normalized-height bounds for {park_key!r}")
        if "legacy_height" in park:
            h = park["legacy_height"]
            try:
                values = tuple(float(h[k]) for k in ("bottom_height_m", "lift_top_height_m",
                                                     "z_at_bottom", "z_at_lift_top"))
            except (ValueError, TypeError, KeyError) as exc:
                raise ValueError(f"Bad legacy-height profile for {park_key!r}") from exc
            if not all(map(math.isfinite, values)) or values[1] <= values[0]:
                raise ValueError(f"Invalid legacy-height calibration for {park_key!r}")
    by_label = {}
    folded_labels = set()
    displayed_names = set()
    for index, item in enumerate(variants):
        if not isinstance(item, dict):
            raise ValueError(f"Variant entry {index} must be a dictionary")
        unexpected = set(item) - VARIANT_FIELDS
        if unexpected:
            raise ValueError(f"Unexpected fields in variant {index}: {sorted(unexpected)}")
        label = item.get("label")
        park_key = item.get("park")
        if not isinstance(label, str) or not label.strip() or label != label.strip():
            raise ValueError(f"Variant {index} has an invalid label")
        if label.casefold() in folded_labels:
            raise ValueError(f"Duplicate operator variant label (case insensitive): {label!r}")
        folded_labels.add(label.casefold())
        if park_key not in parks:
            raise ValueError(f"Variant {label!r} refers to missing park {park_key!r}")
        validate_variant_label_format(label, park_key, parks)
        park_name = parks[park_key]["name"]
        if not show_variant_modifiers:
            visible = park_name.casefold()
            if visible in displayed_names:
                raise ValueError(f"Duplicate displayed experience name {park_name!r} "
                                 "when SHOW_VARIANT_MODIFIERS=False")
            displayed_names.add(visible)
        if item.get("angular_cue", "euler") not in CUES:
            raise ValueError(f"Invalid angular cue in {label!r}")
        mode = item.get("heave_mode")
        if mode is not None and mode not in HEAVE_MODES:
            raise ValueError(f"Invalid heave mode in {label!r}")
        if mode == "normalized_height" and "normalized_height" not in parks[park_key]:
            raise ValueError(f"Normalized height requested without calibration: {label!r}")
        for axis in ("roll_washout", "pitch_washout"):
            if axis in item and item[axis] is not None and type(item[axis]) is not bool:
                raise ValueError(f"{axis} must be None/True/False in {label!r}")
        scales = item.get("gain_scale_pct", {})
        if not isinstance(scales, dict):
            raise ValueError(f"gain_scale_pct must be a dictionary for {label!r}")
        for axis, scale in scales.items():
            if axis not in {name + "_gain" for name in AXES}:
                raise ValueError(f"Invalid gain axis {axis!r} in {label!r}")
            if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not math.isfinite(scale) or not 0 <= scale <= 200:
                raise ValueError(f"Gain must be 0..200% in {label!r} ({axis})")
        if "normalized_height_amplitude" in item:
            try:
                amplitude = float(item["normalized_height_amplitude"])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid amplitude in {label!r}") from exc
            if not math.isfinite(amplitude) or not 0 <= amplitude <= 1 or "normalized_height" not in parks[park_key]:
                raise ValueError(f"Invalid/unavailable amplitude in {label!r}")
        if "neutral_height_delta_mm" in item:
            try:
                validated_ride_neutral_delta(item["neutral_height_delta_mm"])
            except ValueError as exc:
                raise ValueError(f"Invalid ride neutral offset in {label!r}: {exc}") from exc
        if item.get("status", "candidate") not in ("candidate", "preferred", "rejected"):
            raise ValueError(f"Invalid review status in {label!r}")
        by_label[label] = item
    if default is not None and default not in by_label:
        raise ValueError(f"Unknown default variant: {default!r}")
    return by_label


def resolve_variant(entry, parks=PARKS):
    """Return a resolved independent snapshot of one validated JSON entry."""
    entry = deepcopy(entry)
    park = deepcopy(parks[entry["park"]])
    entry.setdefault("angular_cue", "euler")
    entry.setdefault("heave_mode", "normalized_height" if "normalized_height" in park else "legacy_height")
    entry["gain_scale_pct"] = {axis: float(entry.get("gain_scale_pct", {}).get(axis + "_gain", 100))
                               for axis in AXES}
    entry["neutral_height_delta_mm"] = validated_ride_neutral_delta(
        entry.get("neutral_height_delta_mm", 0.0))
    entry.setdefault("roll_washout", None)
    entry.setdefault("pitch_washout", None)
    entry["normalized_height_amplitude"] = float(entry.get(
        "normalized_height_amplitude", park.get("normalized_height", {}).get("amplitude", 0.80)))
    entry["park_info"] = park
    # Review decisions must not change the processing/cache identity.
    entry.pop("status", None)
    return entry


def _legacy_store(platform_key="platform_v2"):
    """Compatibility for older study scripts; no mutable global variant list."""
    from sims.nolimits2.variant_store import VariantStore
    return VariantStore.load(platform_key)


def labels(park_key=None, *, platform_key="platform_v2"):
    return _legacy_store(platform_key).labels(park_key)


def display_label(label, *, platform_key="platform_v2"):
    entry = _legacy_store(platform_key).entry(label)
    return label if SHOW_VARIANT_MODIFIERS else PARKS[entry["park"]]["name"]


def variant(label, *, platform_key="platform_v2"):
    return _legacy_store(platform_key).snapshot(label)


def park_key_for_variant(label, *, platform_key="platform_v2"):
    return _legacy_store(platform_key).entry(label)["park"]


def park_key_for_capture(ride_name):
    """Match capture's explicit ride field, not a guessed filename or variant label."""
    value = str(ride_name).strip().casefold()
    for key, park in PARKS.items():
        if value in (park["name"].casefold(), Path(park["path"]).stem.casefold()):
            return key
    return None


def park_rides():
    """Compatibility with the original recorder: one capture per physical park."""
    return tuple((p["name"], p["path"]) for p in PARKS.values())


def angular_washout_overrides(label, *, platform_key="platform_v2"):
    # Historical raw replay is deliberately not resolved through current
    # curation. An explicit variant snapshot supplies its own washout policy.
    return None, None


def standard_label_for_park(park_key, *, platform_key="platform_v2"):
    return _legacy_store(platform_key).default_label(park_key)


def variant_index_for_park_index(index, *, platform_key="platform_v2"):
    """Legacy utility only; the recorder now has an independent park catalog."""
    park_keys = tuple(PARKS)
    if not 0 <= index < len(park_keys):
        raise IndexError(index)
    store = _legacy_store(platform_key)
    label = store.default_label(park_keys[index])
    if label is None:
        raise ValueError(f"No available variant for park {park_keys[index]!r}")
    return store.visible_labels().index(label)
