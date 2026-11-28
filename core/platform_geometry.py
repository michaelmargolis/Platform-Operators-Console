# platform_geometry.py ver 4
"""Shared floor applicability/geometry setup for live and offline backends."""
from functools import lru_cache
import hashlib
from importlib import import_module
import json
import math

from config.platforms import PLATFORMS
from core.motion_convention import MOTION_CONVENTION
from kinematics.kinematics_V3 import Kinematics, PlatformParams

# Conservative commissioning adjustment range; physical geometry itself is fixed.
MIN_OPERATING_FLOOR_CLEARANCE_MM = 5.0
MAX_OPERATING_FLOOR_CLEARANCE_MM = 75.0

def validate_operating_floor_clearance(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("minimum operating floor clearance must be numeric")
    value = float(value)
    if not math.isfinite(value) or not MIN_OPERATING_FLOOR_CLEARANCE_MM <= value <= MAX_OPERATING_FLOOR_CLEARANCE_MM:
        raise ValueError(f"floor clearance must be {MIN_OPERATING_FLOOR_CLEARANCE_MM:g}..{MAX_OPERATING_FLOOR_CLEARANCE_MM:g} mm")
    return value


def clearance_settings(cfg):
    """Zero or omitted offset: floor contact is physically excluded by design.

    MDX explicitly declares a positive offset; its extension-reserve neutral
    calculation is retained unchanged. Do not infer hulls from another platform.
    """
    offset = float(getattr(cfg, 'PLATFORM_CLEARANCE_OFFSET', 0.0))
    if not math.isfinite(offset) or offset < 0:
        raise ValueError('PLATFORM_CLEARANCE_OFFSET must be finite and non-negative')
    if offset == 0.0:
        cfg.MUSCLE_MAX_ACTIVE_LENGTH = cfg.MUSCLE_MAX_LENGTH
        cfg.MAX_ACTIVE_ACTUATOR_LENGTH = cfg.MAX_ACTUATOR_LENGTH
        return offset, None
    return offset, getattr(cfg, 'MUSCLE_EXTENSION_RESERVE', None)


def _geometry_id(cfg):
    geometry = {
        "convention": MOTION_CONVENTION,
        "upper": cfg.base_coords,
        "lower": cfg.platform_coords,
        "neutral_offset": getattr(cfg, "NEUTRAL_HEIGHT_OFFSET_MM", 0.0),
        "clearance_offset": getattr(cfg, "PLATFORM_CLEARANCE_OFFSET", 0.0),
        "extension_reserve": getattr(cfg, "MUSCLE_EXTENSION_RESERVE", None),
        "axis_limits": cfg.LIMITS_1DOF_TRANFORM,
        "muscle_limits": (cfg.MUSCLE_MIN_LENGTH, cfg.MUSCLE_MAX_LENGTH),
        "fixed_hardware": cfg.FIXED_HARDWARE_LENGTH,
        "workspace": getattr(cfg, "WORKSPACE_LIMIT_MODE", "off"),
        "workspace_iterations": getattr(cfg, "WORKSPACE_LIMIT_ITERATIONS", 6),
        "floor_z": getattr(cfg, "FLOOR_Z", None),
        "underside_z": getattr(cfg, "PLATFORM_UNDERSIDE_Z", None),
        "hull": getattr(cfg, "PLATFORM_UNDERSIDE_HULL_XY", ()),
        "floor_min": getattr(cfg, "MIN_FLOOR_CLEARANCE", None),
        "hull_margin": getattr(cfg, "FLOOR_HULL_APPROX_MARGIN", 0.0),
    }
    encoded = json.dumps(geometry, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@lru_cache(maxsize=None)
def geometry_id_for_platform(platform_key):
    """Fingerprint the shared reference geometry using its inputs, in pair order."""
    definition = PLATFORMS[platform_key]
    return _geometry_id(import_module(definition["sop_config_module"]).PlatformConfig())


def configure_platform_geometry(backend, config_module, *, festo_valve0_side=None):
    """Load the verified rider-frame geometry in this release's Festo order."""
    definition = next((item for item in PLATFORMS.values()
                       if item["sop_config_module"] == config_module), None)
    if definition is None:
        raise ValueError(f"Unsupported platform geometry {config_module!r}")
    approved_side = definition["festo_valve0_side"]
    if festo_valve0_side is not None and festo_valve0_side != approved_side:
        raise ValueError(f"{definition['name']} requires rider-{approved_side} Festo valve 0 in this release")

    cfg = import_module(config_module).PlatformConfig()
    sign = 1 if approved_side == "left" else -1
    for name, points in (("upper", cfg.base_coords), ("lower", cfg.platform_coords)):
        if len(points) != 6:
            raise ValueError(f"Expected six {name} attachment points for {definition['name']}")
        for index, point in enumerate(points):
            if len(point) != 3 or not all(math.isfinite(value) for value in point):
                raise ValueError(f"Invalid {name} attachment point {index}")
            expected_sign = sign if index < 3 else -sign
            if point[1] * expected_sign <= 0:
                raise ValueError(f"{name} attachment point {index} disagrees with the approved valve order")

    backend.cfg = cfg
    backend.festo_valve0_side = approved_side
    backend.channel_to_pair = tuple(range(6))
    backend.geometry_id = _geometry_id(cfg)
    backend.k = Kinematics()
    params = PlatformParams(cfg.MUSCLE_MIN_LENGTH, cfg.MUSCLE_MAX_LENGTH,
                            cfg.FIXED_HARDWARE_LENGTH, cfg.LIMITS_1DOF_TRANFORM)
    offset, reserve = clearance_settings(cfg)
    backend.k.set_geometry(cfg.base_coords, cfg.platform_coords, params, offset, reserve,
                           neutral_height_offset_mm=getattr(cfg, "NEUTRAL_HEIGHT_OFFSET_MM", 0.0))
    configure_workspace(backend)


def configure_workspace(backend):
    cfg = backend.cfg
    offset, _ = clearance_settings(cfg)
    mode = str(getattr(cfg, 'WORKSPACE_LIMIT_MODE', 'off')).strip().lower()
    if mode not in ('off', 'reserve', 'geometry'):
        raise RuntimeError(f"Invalid WORKSPACE_LIMIT_MODE {mode!r}; expected geometry, reserve or off")
    backend.workspace_limit_mode = mode
    backend.workspace_limit_iterations = max(1, int(getattr(cfg, 'WORKSPACE_LIMIT_ITERATIONS', 6)))
    backend.workspace_limit_count = 0
    backend.workspace_limited = False
    backend.workspace_scale = 1.0
    backend.workspace_limit_reason = None
    backend.workspace_floor_clearance = None
    backend.workspace_actuator_min = None
    backend.workspace_actuator_max = None
    backend.last_applied_real_transform = [0.0] * 6
    # This runtime-only bias changes ride poses, never geometry or parking.
    backend.ride_neutral_height_delta_mm = 0.0
    backend._workspace_hull_xy = ()
    backend._workspace_floor_z = None
    backend._workspace_underside_z = None
    backend._workspace_min_floor_clearance = None
    backend._workspace_hull_margin = 0.0
    if mode != 'geometry' or offset == 0:
        return
    required = ('FLOOR_Z', 'PLATFORM_UNDERSIDE_Z', 'MIN_FLOOR_CLEARANCE', 'PLATFORM_UNDERSIDE_HULL_XY')
    missing = [name for name in required if not hasattr(cfg, name)]
    if missing:
        raise RuntimeError('Geometry workspace limiting requires config values: ' + ', '.join(missing))
    backend._workspace_floor_z = float(cfg.FLOOR_Z)
    backend._workspace_underside_z = float(cfg.PLATFORM_UNDERSIDE_Z)
    backend._workspace_min_floor_clearance = float(cfg.MIN_FLOOR_CLEARANCE)
    backend._workspace_hull_margin = max(0.0, float(getattr(cfg, 'FLOOR_HULL_APPROX_MARGIN', 0.0)))
    backend._workspace_hull_xy = tuple((float(p[0]), float(p[1])) for p in cfg.PLATFORM_UNDERSIDE_HULL_XY)
    if len(backend._workspace_hull_xy) < 3:
        raise RuntimeError('PLATFORM_UNDERSIDE_HULL_XY must contain at least three points')
    values = (backend._workspace_floor_z, backend._workspace_underside_z,
              backend._workspace_min_floor_clearance, backend._workspace_hull_margin,
              *(v for p in backend._workspace_hull_xy for v in p))
    if not all(math.isfinite(v) for v in values):
        raise ValueError('Floor/hull coordinates must be finite')
