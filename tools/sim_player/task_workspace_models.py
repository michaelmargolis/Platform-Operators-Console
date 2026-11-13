"""Small, GUI-free helpers for the task-oriented Sim Player inspector.

Research washout overrides are ephemeral snapshots. No live configuration or
source capture is changed by this module.
"""
from __future__ import annotations
from copy import deepcopy
import math

AXES = ('Surge', 'Sway', 'Heave', 'Roll', 'Pitch', 'Yaw')
AXIS_COLORS = ('#45c8da', '#b18bf4', '#8add9b', '#f5b45f', '#ff4d4d', '#ffe34f')


def read_washout(snapshot):
    if not isinstance(snapshot, dict):
        return {'kind': 'unknown', 'surge_enabled': False,
                'sway_enabled': False, 'surge_tau': None, 'sway_tau': None}
    enabled = list(snapshot.get('enabled', ()))
    tau = list(snapshot.get('tau', ()))
    return {
        'kind': str(snapshot.get('washout_type', 'unknown')).lower(),
        'surge_enabled': bool(enabled[0]) if len(enabled) > 0 else False,
        'sway_enabled': bool(enabled[1]) if len(enabled) > 1 else False,
        'surge_tau': float(tau[0]) if len(tau) > 0 else None,
        'sway_tau': float(tau[1]) if len(tau) > 1 else None,
    }


def with_washout(snapshot, *, surge_enabled, sway_enabled, surge_tau, sway_tau):
    """Return a modified copy of POC global settings, never editing inputs.

    All unrelated axes, gains, washout type, and legacy-duration settings are
    preserved. Existing POC analysis performs all filtering on re-analysis.
    """
    if not isinstance(snapshot, dict):
        raise ValueError('A recorded POC global-settings snapshot is required')
    enabled = snapshot.get('enabled')
    tau = snapshot.get('tau')
    if not isinstance(enabled, (list, tuple)) or len(enabled) != 6:
        raise ValueError('Washout snapshot requires six enable flags')
    if not isinstance(tau, (list, tuple)) or len(tau) != 6:
        raise ValueError('Washout snapshot requires six time constants')
    values = []
    for name, value in (('Surge', surge_tau), ('Sway', sway_tau)):
        try:
            value = float(value)
        except (ValueError, TypeError) as exc:
            raise ValueError(f'{name} washout tau must be numeric') from exc
        if not math.isfinite(value) or not 0.01 <= value <= 60.0:
            raise ValueError(f'{name} washout tau must be between 0.01 and 60 seconds')
        values.append(value)
    updated = deepcopy(snapshot)
    updated['enabled'] = list(enabled)
    updated['tau'] = list(tau)
    updated['enabled'][0] = bool(surge_enabled)
    updated['enabled'][1] = bool(sway_enabled)
    updated['tau'][0], updated['tau'][1] = values
    return updated


def plot_series(recording, key):
    """Return common-lane data without modifying any analysed sample."""
    if recording is None or not recording.frames:
        return ()
    if key == 'all_axes':
        return tuple((name, tuple(frame.processed_axes[i] for frame in recording.frames))
                     for i, name in enumerate(AXES))
    if key == 'profile_gains':
        if not getattr(recording, 'dynamic_profile', None):
            return ()
        multipliers = tuple(frame.analysis.get('dynamic', {}).get('multipliers')
                            for frame in recording.frames)
        if any(v is None or len(v) != 6 for v in multipliers):
            return ()
        return tuple((name, tuple(float(row[i]) for row in multipliers))
                     for i, name in enumerate(AXES))
    if key in ('source_vs_processed_surge', 'source_vs_processed_sway'):
        i = 0 if key.endswith('surge') else 1
        axis = AXES[i]
        dynamic = getattr(recording, 'dynamic_profile', None) is not None
        before = f'Base post-washout {axis}' if dynamic else f'NL2 CSV {axis}'
        after = f'Profile request {axis}' if dynamic else f'Processed {axis}'
        return ((before, tuple(f.source_axes[i] for f in recording.frames)),
                (after, tuple(f.processed_axes[i] for f in recording.frames)))
    if key == 'all_muscles':
        return tuple((f'M{i+1}', tuple(f.muscle_lengths_mm[i] for f in recording.frames))
                     for i in range(6))
    if key.startswith('m') and key[1:].isdigit():
        i = int(key[1:]) - 1
        if i in range(6):
            return ((f'M{i+1}', tuple(f.muscle_lengths_mm[i] for f in recording.frames)),)
    axis = {name.lower(): i for i, name in enumerate(AXES)}.get(key)
    if axis is not None:
        return ((AXES[axis], tuple(f.processed_axes[axis] for f in recording.frames)),)
    if key == 'sync_offset':
        return (('Video offset', tuple(float(recording.video_offset_s) for _ in recording.frames)),)
    return ()


def plot_unit(key):
    """Display scale and label only; underlying replay arrays are unchanged."""
    if key == 'all_muscles' or (key.startswith('m') and key[1:].isdigit()):
        return 1., 'mm'
    if key == 'sync_offset':
        return 1., 's'
    return 100., '%'  # normalised motion or post-washout multipliers


def plot_guides(recording, key):
    if recording is None:
        return ()
    if key == 'all_muscles' or (key.startswith('m') and key[1:].isdigit()):
        return ((float(recording.min_length_mm), 'Min'),
                (float(recording.max_length_mm), 'Max'))
    if key == 'profile_gains':
        return ((1., 'Unity'),)
    return ((0., 'Zero'),) if key != 'sync_offset' else ()


def plot_styles(key, series):
    """One source of truth for trace pens AND colour-keyed legend swatches."""
    result = []
    for i, (name, _values) in enumerate(series):
        if key.startswith('source_vs'):
            axis = 0 if key.endswith('surge') else 1
            color = '#aab8c7' if i == 0 else AXIS_COLORS[axis]
            dashed = i == 0
        elif name in AXES:
            color, dashed = AXIS_COLORS[AXES.index(name)], False
        elif name.startswith('M') and name[1:].isdigit():
            color, dashed = AXIS_COLORS[(int(name[1:])-1) % 6], False
        else:
            color, dashed = AXIS_COLORS[i % 6], False
        result.append((name, color, dashed))
    return tuple(result)
