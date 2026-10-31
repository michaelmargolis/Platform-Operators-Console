# platform_geometry.py ver 2
"""Shared floor applicability/geometry setup for live and offline backends."""
import math


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
