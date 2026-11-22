# dynamic_export_inventory.py ver 3
"""Qt-free discovery and lightweight comparison of exported dynamic candidates."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
from zipfile import BadZipFile, ZipFile

PACKAGE_SCHEMA = 'poc.nl2.dynamic-candidate-package/1'
SUPPORTED_PLATFORMS = frozenset(('chair_v3', 'platform_v2'))


@dataclass(frozen=True)
class ExportedCandidate:
    package: Path
    platform: str
    park: str
    label: str
    target: str
    source_recordings: int | None
    operational_status: str
    modified_time: float
    content_hashes: tuple[tuple[str, str], ...] = ()
    profile_identity_sha256: str = ''


def _deployment_metadata(archive: ZipFile, payload: dict):
    """Return bound runtime hashes plus a stable digest of profile identity.

    Inventory deliberately remains tolerant: missing/malformed deployment
    metadata does not hide an otherwise recognisable candidate package. It only
    makes duplicate-content comparison unavailable for that package.
    """
    deployment_path = str(payload.get('deployment_path') or '').strip()
    if not deployment_path:
        return (), ''
    try:
        deployment = json.loads(archive.read(deployment_path).decode('utf-8-sig'))
    except (KeyError, UnicodeError, json.JSONDecodeError, ValueError):
        return (), ''
    if not isinstance(deployment, dict):
        return (), ''
    raw_hashes = deployment.get('sha256')
    hashes = []
    if isinstance(raw_hashes, dict):
        for name, value in raw_hashes.items():
            name = str(name or '').strip()
            value = str(value or '').strip().lower()
            if name and value:
                hashes.append((name, value))
    identity = deployment.get('profile_identity')
    identity_sha = ''
    if isinstance(identity, dict):
        encoded = json.dumps(identity, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
        identity_sha = hashlib.sha256(encoded).hexdigest()
    return tuple(sorted(hashes)), identity_sha


def _read_package(package: Path) -> ExportedCandidate:
    package = Path(package)
    with ZipFile(package) as archive:
        payload = json.loads(archive.read('dynamic_candidate.json').decode('utf-8-sig'))
        content_hashes, profile_identity_sha256 = _deployment_metadata(archive, payload)
    if not isinstance(payload, dict):
        raise ValueError('candidate manifest must be an object')
    if payload.get('schema') != PACKAGE_SCHEMA:
        raise ValueError('unsupported candidate package schema')
    candidate = payload.get('candidate')
    if not isinstance(candidate, dict):
        raise ValueError('candidate data missing')
    qualification = payload.get('qualification_summary')
    if not isinstance(qualification, dict):
        qualification = {}

    # This module is an inventory reader, not the POC package validator. Keep
    # discovery deliberately tolerant of qualification-summary evolution so an
    # exported package is not silently hidden merely because optional metadata
    # differs. Export and POC import remain the authoritative integrity gates.
    platform = str(payload.get('platform') or qualification.get('platform') or '').strip()
    park = str(payload.get('park') or candidate.get('park') or qualification.get('park') or '').strip()
    label = str(candidate.get('label') or '').strip()
    target = str(qualification.get('target') or '').strip()
    if platform not in SUPPORTED_PLATFORMS or not park or not label:
        raise ValueError('supported platform, park and candidate label are required')
    recordings = qualification.get('source_recordings')
    if isinstance(recordings, bool) or not isinstance(recordings, int) or recordings < 0:
        recordings = None
    return ExportedCandidate(
        package=package.resolve(),
        platform=platform,
        park=park,
        label=label,
        target=target,
        source_recordings=recordings,
        operational_status=str(payload.get('operational_status') or '').strip(),
        modified_time=package.stat().st_mtime,
        content_hashes=content_hashes,
        profile_identity_sha256=profile_identity_sha256,
    )


def discover_exported_candidates(directory: Path):
    """Return ``(recognised_packages, invalid_zip_count)`` for one export folder."""
    folder = Path(directory)
    if not folder.is_dir():
        return (), 0
    rows = []
    invalid = 0
    for package in sorted(folder.glob('*.zip'), key=lambda value: value.name.casefold()):
        try:
            rows.append(_read_package(package))
        except (OSError, BadZipFile, KeyError, ValueError, UnicodeError, json.JSONDecodeError):
            invalid += 1
    return tuple(rows), invalid


def candidate_identity(row: ExportedCandidate):
    """Case-insensitive export identity used by POC variant-name uniqueness."""
    return row.platform, row.park, row.label.casefold()


def compare_same_name_exports(rows):
    """Compare exports sharing platform + ride + candidate label.

    Return ``(state, summary, details)`` where state is ``identical``,
    ``different`` or ``unavailable``. ZIP bytes, bundle token/path and file
    timestamps are intentionally ignored; the bound profile/reference/
    qualification hashes are the meaningful candidate-content comparison.
    """
    rows = tuple(sorted(rows, key=lambda row: (row.modified_time, row.package.name.casefold())))
    if len(rows) < 2:
        return 'unavailable', 'Only one export has this candidate identity.', ''
    identities = {candidate_identity(row) for row in rows}
    if len(identities) != 1:
        raise ValueError('Exports do not share platform, ride and candidate label')

    comparable = all(row.content_hashes and row.profile_identity_sha256 for row in rows)
    fingerprints = {(row.content_hashes, row.profile_identity_sha256) for row in rows if row.content_hashes}
    metadata = {(row.target, row.source_recordings, row.operational_status) for row in rows}
    if comparable and len(fingerprints) == 1 and len(metadata) == 1:
        state = 'identical'
        summary = f'{len(rows)} exports have identical bound content and comparison metadata.'
    elif comparable:
        state = 'different'
        summary = f'{len(rows)} exports use the same candidate name but are DIFFERENT.'
    else:
        state = 'unavailable'
        summary = f'{len(rows)} exports share this candidate name; full hash comparison is unavailable for at least one package.'

    names = sorted({name for row in rows for name, _ in row.content_hashes}, key=str.casefold)
    lines = [summary, '']
    for index, row in enumerate(rows, 1):
        stamp = datetime.fromtimestamp(row.modified_time).strftime('%Y-%m-%d %H:%M:%S')
        lines.extend([
            f'{index}. {row.package.name}',
            f'   Exported: {stamp}',
            f'   Target: {row.target or "unknown"}',
            f'   Source recordings: {row.source_recordings if row.source_recordings is not None else "unknown"}',
            f'   Status: {row.operational_status or "unknown"}',
        ])
        hash_map = dict(row.content_hashes)
        for name in names:
            value = hash_map.get(name, '')
            lines.append(f'   {name}: {value[:16] if value else "unavailable"}')
        if row.profile_identity_sha256:
            lines.append(f'   Profile identity: {row.profile_identity_sha256[:16]}')
        lines.append('')

    if state == 'different':
        lines.append('Differences:')
        for name in names:
            values = {dict(row.content_hashes).get(name, '') for row in rows}
            if len(values) > 1:
                lines.append(f' - {name} differs')
        identity_values = {row.profile_identity_sha256 for row in rows}
        if len(identity_values) > 1:
            lines.append(' - profile identity/frozen metadata differs')
        for field, getter in (
            ('qualification target', lambda row: row.target),
            ('source recording count', lambda row: row.source_recordings),
            ('operational status', lambda row: row.operational_status),
        ):
            values = {getter(row) for row in rows}
            if len(values) > 1:
                lines.append(f' - {field} differs')
    return state, summary, '\n'.join(lines).rstrip()


def short_candidate_label(label: str, ride_name: str) -> str:
    """Return the candidate-specific part of labels such as Ride [Dynamic A]."""
    label = str(label or '').strip()
    ride_name = str(ride_name or '').strip()
    if ride_name and label.casefold().startswith(ride_name.casefold()):
        suffix = label[len(ride_name):].strip()
        if len(suffix) >= 2 and suffix[0] == '[' and suffix[-1] == ']':
            suffix = suffix[1:-1].strip()
        if suffix:
            return suffix
    return label
