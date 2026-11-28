#dynamic_qualification.py ver 2
"""Versioned, portable engineering qualification for dynamic ride imports.

The qualification is evidence of offline *recorded* kinematic validation, not
physical approval or a cryptographic signature. Its binding to exact bundle
contents is checked by the deployment SHA256 manifest.
"""
from __future__ import annotations

import importlib
import json
import math
from pathlib import Path

from config.platforms import PLATFORMS

QUALIFICATION_SCHEMA = 'poc.nl2.dynamic-qualification/1'
MAX_ALLOWED_EXCESS_FRACTION = 0.05
SUPPORTED_PLATFORMS = frozenset(('chair_v3', 'platform_v2'))
EPS = 1e-5


class QualificationError(ValueError):
    pass


def _finite_number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise QualificationError(f'{name} must be finite')
    return float(value)


def platform_geometry_envelope(platform):
    """Use physical platform configuration, not study-derived limits."""
    if platform not in SUPPORTED_PLATFORMS:
        raise QualificationError('Dynamic profile has unsupported platform')
    config = importlib.import_module(PLATFORMS[platform]['sop_config_module']).PlatformConfig()
    low = _finite_number(config.MUSCLE_MIN_LENGTH, 'minimum muscle length')
    high = _finite_number(config.MUSCLE_MAX_LENGTH, 'maximum muscle length')
    if high <= low:
        raise QualificationError('Invalid muscle range for platform')
    minimum_floor = (float(config.MIN_FLOOR_CLEARANCE) if platform == 'platform_v2' else None)
    return {'low': low, 'high': high, 'stroke': high - low, 'floor_clearance_mm': minimum_floor}


def verify_evidence(profile, evidence: dict, *, geometry=None):
    """Independently validate source validation.json metrics against a <=5% envelope.

    The source validator already checked actual requested poses and applied
    clamps. We verify its entire nominal/fallback result, including the heldout
    recording, rather than trusting a single 'qualified' flag or an HTML page.
    """
    if not isinstance(evidence, dict):
        raise QualificationError('Missing qualification evidence')
    identity = profile.identity
    platform = identity['platform']
    geometry = geometry or platform_geometry_envelope(platform)
    target = profile.metadata.get('target')
    if target not in ('strict', 'relaxed5'):
        raise QualificationError('Only strict or 5% nominally qualified profiles may be prepared')
    validation = profile.metadata.get('validation')
    if (profile.metadata.get('qualification') != 'nominal_all_captures_pass'
            or not isinstance(validation, dict)
            or any(validation.get(k) is not True for k in
                   ('nominal_qualified', 'heldout_pass', 'production_parity', 'fallback_all_captures_pass'))):
        raise QualificationError('Profile does not carry complete nominal/heldout/fallback validation')
    runs = evidence.get('nominal')
    fallback = evidence.get('static_fallback')
    if (not isinstance(runs, list) or len(runs) < 2 or
            not isinstance(fallback, list) or len(fallback) != len(runs)):
        raise QualificationError('Require >=2 matching nominal and fallback recordings')
    seen = set()
    roles = set()
    max_excess = 0.0
    max_step = 0.0
    worst_floor = None
    for role, items in (('nominal', runs), ('fallback', fallback)):
        for row in items:
            if not isinstance(row, dict) or row.get('qualified') is not True:
                raise QualificationError(f'{role} validation failed')
            if any(int(row.get(k, 0)) != 0 for k in (
                    'floor_violation_ticks', 'output_range_violation_ticks', 'nonfinite_ticks')):
                raise QualificationError(f'{role} reports a physical or nonfinite violation')
            if role == 'nominal' and (row.get('production_parity') is not True
                                      or int(row.get('sync_lost_ticks', 0)) != 0):
                raise QualificationError('Nominal production parity/synchronisation failed')
            low = _finite_number(row.get('minimum_output_length_mm'), 'minimum output length')
            high = _finite_number(row.get('maximum_output_length_mm'), 'maximum output length')
            if low < geometry['low'] - EPS or high > geometry['high'] + EPS:
                raise QualificationError('Final output outside configured platform muscle range')
            allowance = _finite_number(row.get('allowance_mm'), 'allowance')
            excess = _finite_number(row.get('maximum_requested_excess_mm'), 'excess')
            permitted = 0. if (role == 'fallback' or target == 'strict') else MAX_ALLOWED_EXCESS_FRACTION * geometry['stroke']
            if allowance > permitted + EPS or excess > permitted + EPS:
                raise QualificationError('Requested muscle range exceeds <=5% envelope')
            if role == 'nominal':
                capture = str(row.get('capture', ''))
                if not capture or capture in seen:
                    raise QualificationError('Duplicate/absent nominal recording identity')
                seen.add(capture)
                roles.add(row.get('role'))
                max_excess = max(max_excess, excess)
                max_step = max(max_step, _finite_number(row.get('max_output_step_mm'), 'step'))
            if platform == 'platform_v2':
                recorded_floor = _finite_number(row.get('minimum_requested_floor_mm'), 'minimum requested floor')
                recorded_limit = _finite_number(row.get('floor_required_mm'), 'floor requirement')
                if recorded_floor + EPS < recorded_limit:
                    raise QualificationError('Ground-loading platform floor-clearance violation')
                worst_floor = min(worst_floor, recorded_floor) if worst_floor is not None else recorded_floor
    if not {'fit', 'heldout'}.issubset(roles):
        raise QualificationError('Qualification requires an independent heldout recording')
    if {str(r.get('capture')) for r in fallback} != seen:
        raise QualificationError('Fallback does not cover the same source recordings')
    return {
        'platform': platform, 'park': identity['park'], 'target': target,
        'source_recordings': len(runs), 'heldout_recordings': sum(r.get('role') == 'heldout' for r in runs),
        'allowed_excess_fraction': MAX_ALLOWED_EXCESS_FRACTION,
        'maximum_requested_excess_mm': max_excess,
        'max_output_step_mm': max_step,
        'minimum_recorded_floor_clearance_mm': worst_floor,
        'recorded_floor_requirement_mm': (float(runs[0]['floor_required_mm'])
                                          if platform == 'platform_v2' else None),
        'platform_muscle_bounds_mm': [geometry['low'], geometry['high']],
        'nominal_and_fallback_pass': True,
        'status': 'offline_qualified_not_physically_commissioned',
    }


def read_and_validate_qualification(profile, payload: dict):
    if not isinstance(payload, dict) or payload.get('schema') != QUALIFICATION_SCHEMA:
        raise QualificationError('Missing or unsupported dynamic qualification report')
    if payload.get('profile_identity') != profile.identity:
        raise QualificationError('Qualification report does not match profile identity')
    if payload.get('creator') != 'MDX Ride Analyzer':
        raise QualificationError('Expected Analyzer-generated qualification report')
    source = payload.get('evidence')
    result = verify_evidence(profile, source)
    if payload.get('summary') != result:
        raise QualificationError('Qualification report summary is inconsistent with evidence')
    return result


def load_qualification(path: Path, profile):
    try:
        if path.stat().st_size > 5_000_000:
            raise QualificationError('Qualification report exceeds size limit')
        data = json.loads(path.read_text(encoding='utf-8'), parse_constant=lambda val: (_ for _ in ()).throw(ValueError(val)))
    except (OSError, ValueError, UnicodeError) as exc:
        raise QualificationError(f'Invalid qualification file: {exc}') from exc
    return read_and_validate_qualification(profile, data)


def make_qualification(profile, validation_path):
    from core.dynamic_profiles import require_canonical_profile

    require_canonical_profile(profile)
    path = Path(validation_path)
    try:
        evidence = json.loads(path.read_text(encoding='utf-8'), parse_constant=lambda val: (_ for _ in ()).throw(ValueError(val)))
    except (OSError, ValueError, UnicodeError) as exc:
        raise QualificationError(f'Cannot read research validation report: {exc}') from exc
    summary = verify_evidence(profile, evidence)
    return {'schema': QUALIFICATION_SCHEMA, 'creator': 'MDX Ride Analyzer',
            'profile_identity': profile.identity, 'summary': summary,
            'evidence': evidence}
