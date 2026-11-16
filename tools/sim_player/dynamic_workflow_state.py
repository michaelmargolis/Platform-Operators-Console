# dynamic_workflow_state.py ver 1
"""Qt-free state helpers for the Dynamic Ride preparation workspace."""
from __future__ import annotations

PLATFORM_ORDER = {'chair_v3': 0, 'platform_v2': 1}
TARGET_ORDER = {'strict': 0, 'relaxed5': 1}


def candidate_is_qualified(choice) -> bool:
    return str(getattr(choice, 'qualification', '')) == 'nominal_all_captures_pass'


def candidate_sort_key(choice):
    """Put platform, qualified strict, then qualified relaxed candidates first."""
    platform = str(getattr(choice, 'platform', ''))
    target = str(getattr(choice, 'target', ''))
    return (
        PLATFORM_ORDER.get(platform, 99),
        0 if candidate_is_qualified(choice) else 1,
        TARGET_ORDER.get(target, 99),
        str(getattr(choice, 'name', '')),
        str(getattr(choice, 'path', '')),
    )


def recommended_candidate_paths(choices, dismissed=()):
    """Return one qualified preferred engineering candidate per platform.

    Strict is preferred when it qualifies. Relaxed5 is recommended only when
    no non-dismissed strict candidate qualifies for that platform.
    """
    dismissed = {str(item) for item in dismissed}
    grouped = {}
    for choice in choices:
        path = str(getattr(choice, 'path', ''))
        if path in dismissed or not candidate_is_qualified(choice):
            continue
        grouped.setdefault(str(getattr(choice, 'platform', '')), []).append(choice)
    result = {}
    for platform, rows in grouped.items():
        rows.sort(key=candidate_sort_key)
        if rows:
            result[platform] = str(getattr(rows[0], 'path', ''))
    return result


def workflow_stage(*, has_study, generating, has_result, has_candidate, reviewed, prepared):
    """Return the user-facing next workflow stage for the selected candidate."""
    if not has_study:
        return 'source'
    if generating:
        return 'generating'
    if not has_result or not has_candidate:
        return 'generate'
    if not reviewed:
        return 'review'
    if not prepared:
        return 'prepare'
    return 'export'
