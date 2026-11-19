#dynamic_import.py ver 3
"""Atomic import of an Analyzer-prepared dynamic candidate onto POC.

No Qt or hardware. Intended to be called from POC Profile tab only while
parked/deactivated. Never merges or replaces a whole platform catalogue.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
from zipfile import ZipFile, BadZipFile

from core.dynamic_profiles import DynamicDeployment, DynamicProfileError, ROOT, sha256_file
from core.telemetry_config import TelemetryConfig
from sims.nolimits2.variant_store import VariantStore
from sims.nolimits2 import ride_config

PACKAGE_SCHEMA = 'poc.nl2.dynamic-candidate-package/1'
MAX_PACKAGE_BYTES = 80_000_000
MAX_SINGLE_FILE_BYTES = 50_000_000


def _read_candidate_zip(archive: Path):
    """Validate archive manifest and structure without writing anything."""
    try:
        with ZipFile(archive) as z:
            infos = z.infolist()
            if len(infos) != 5 or any(i.is_dir() for i in infos):
                raise DynamicProfileError('Expected one candidate manifest and four runtime files')
            names = [i.filename for i in infos]
            if len(names) != len(set(names)) or any(i.file_size > MAX_SINGLE_FILE_BYTES for i in infos):
                raise DynamicProfileError('Duplicate or oversized dynamic-package entry')
            if sum(i.file_size for i in infos) > MAX_PACKAGE_BYTES:
                raise DynamicProfileError('Oversized dynamic candidate package')
            for i in infos:
                if (i.filename.startswith('/') or '\\' in i.filename or
                        any(part in ('', '.', '..') for part in i.filename.split('/')) or
                        stat.S_ISLNK((i.external_attr >> 16) & 0xFFFF)):
                    raise DynamicProfileError('Unsafe path or symlink in dynamic candidate ZIP')
            manifest_raw = z.read('dynamic_candidate.json')
            if len(manifest_raw) > 500_000:
                raise DynamicProfileError('Oversized candidate manifest')
            payload = json.loads(manifest_raw.decode('utf-8-sig'),
                                 parse_constant=lambda val: (_ for _ in ()).throw(ValueError(val)))
            if not isinstance(payload, dict) or payload.get('schema') != PACKAGE_SCHEMA:
                raise DynamicProfileError('Wrong dynamic candidate package schema')
            entry = payload.get('candidate')
            if not isinstance(entry, dict) or entry.get('status') != 'candidate':
                raise DynamicProfileError('Package must contain a candidate, not a preferred variant')
            platform, park = payload.get('platform'), payload.get('park')
            if entry.get('park') != park or platform not in ('chair_v3', 'platform_v2'):
                raise DynamicProfileError('Invalid package park/platform')
            rel = payload.get('deployment_path')
            from core.dynamic_profiles import validate_bundle_relpath
            path = validate_bundle_relpath(rel.removeprefix('sims/nolimits2/')
                                           if isinstance(rel, str) else '', platform)
            if entry.get('dynamic_profile') != path.as_posix() or path.parts[2] != park:
                raise DynamicProfileError('Candidate/runtime bundle path mismatch')
            folder = 'sims/nolimits2/' + '/'.join(path.parts[:-1]) + '/'
            expected = {'dynamic_candidate.json'} | {
                folder + f for f in ('profile.json', 'reference.npz', 'qualification.json', 'deployment.json')}
            if set(names) != expected:
                raise DynamicProfileError('Unexpected or missing runtime package files')
            if sha256_file_bytes(z.read(folder + 'deployment.json')) != payload.get('deployment_sha256'):
                raise DynamicProfileError('Package deployment manifest checksum mismatch')
            ride_config.validate(variants=[entry], show_variant_modifiers=True)
            return payload, folder
    except (BadZipFile, OSError, ValueError, KeyError, UnicodeError) as exc:
        raise DynamicProfileError(f'Invalid dynamic candidate ZIP: {exc}') from exc


def default_import_directory(root: Path = ROOT) -> Path:
    """Return the Analyzer export directory used by the POC import dialog."""
    return Path(root).resolve() / 'exports' / 'dynamic_candidates'




@dataclass(frozen=True)
class CandidatePackageInfo:
    """Validated import-package metadata safe to display in the POC chooser."""

    path: Path
    label: str
    platform: str
    park: str
    deployment_path: str


def discover_candidate_packages(directory, *, platform_key: str, catalog_dir=None):
    """Return compatible, not-yet-imported packages from one directory.

    Package filenames are deliberately ignored for compatibility decisions.
    Each ZIP is validated through the same manifest/structure parser used by
    :func:`import_candidate`. Packages for another physical platform and
    packages whose candidate label *or deployment path* is already present in
    this platform catalogue are hidden from the normal chooser. Invalid ZIPs
    are also hidden, with counts returned for the UI summary.

    Return ``(packages, counts)`` where packages is a tuple of
    :class:`CandidatePackageInfo` and counts contains total/hidden categories.
    The function is read-only; it never changes the catalogue or filesystem.
    """
    directory = Path(directory).expanduser().resolve()
    counts = {
        'total': 0, 'available': 0, 'already_imported': 0,
        'other_platform': 0, 'invalid': 0,
    }
    if not directory.is_dir():
        return (), counts

    store = VariantStore.load(platform_key, directory=catalog_dir)
    existing_labels = set(store.labels())
    existing_deployments = {
        str(entry.get('dynamic_profile', ''))
        for entry in store.entries() if entry.get('dynamic_profile')
    }
    packages = []
    for archive in sorted(directory.glob('*.zip'), key=lambda path: path.name.casefold()):
        if not archive.is_file():
            continue
        counts['total'] += 1
        try:
            payload, _folder = _read_candidate_zip(archive)
        except DynamicProfileError:
            counts['invalid'] += 1
            continue
        if payload.get('platform') != platform_key:
            counts['other_platform'] += 1
            continue
        entry = payload['candidate']
        if (entry.get('label') in existing_labels or
                entry.get('dynamic_profile') in existing_deployments):
            counts['already_imported'] += 1
            continue
        packages.append(CandidatePackageInfo(
            path=archive,
            label=str(entry['label']),
            platform=str(payload['platform']),
            park=str(payload['park']),
            deployment_path=str(entry['dynamic_profile']),
        ))
    packages.sort(key=lambda item: (item.park.casefold(), item.label.casefold(), item.path.name.casefold()))
    counts['available'] = len(packages)
    return tuple(packages), counts


def sha256_file_bytes(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def import_candidate(archive, *, platform_key: str, root: Path = ROOT, catalog_dir=None):
    """Validate, copy bundle, revalidate, then atomically append to catalogue.

    Return (label, manifest_relative_path). A failure never changes the
    catalogue or leaves a selectable partial import. POC must be stopped or
    parked/deactivated; the calling UI enforces the operational condition.
    """
    archive = Path(archive).expanduser().resolve()
    root = Path(root).resolve()
    payload, folder = _read_candidate_zip(archive)
    if payload['platform'] != platform_key:
        raise DynamicProfileError('Candidate belongs to another physical platform')
    entry = payload['candidate']
    store = VariantStore.load(platform_key, directory=catalog_dir)
    existing_deployments = {
        str(row.get('dynamic_profile', ''))
        for row in store.entries() if row.get('dynamic_profile')
    }
    if (entry['label'] in store.labels() or
            entry.get('dynamic_profile') in existing_deployments):
        raise DynamicProfileError('Candidate already exists/is imported; no catalogue overwrite allowed')
    bundle_rel = Path(folder)
    dest = root.joinpath(*bundle_rel.parts)
    if dest.exists():
        raise DynamicProfileError('The dynamic bundle directory already exists')
    if not dest.resolve(strict=False).is_relative_to((root/'sims'/'nolimits2'/'ride_profiles').resolve()):
        raise DynamicProfileError('Unsafe dynamic bundle destination')
    # Stage on the POC repository filesystem. Raspberry Pi installations may
    # mount /tmp as tmpfs, so staging there and Path.rename() into Runtime can
    # fail with EXDEV / errno 18 (invalid cross-device link). Keeping the
    # temporary tree under root preserves the intended atomic directory rename.
    with tempfile.TemporaryDirectory(prefix='.poc-dyn-import-', dir=root) as tmp:
        staged_root = Path(tmp)
        staged = staged_root / bundle_rel
        staged.mkdir(parents=True)
        with ZipFile(archive) as z:
            for filename in ('profile.json', 'reference.npz', 'qualification.json', 'deployment.json'):
                with (staged / filename).open('xb') as fp:
                    fp.write(z.read(folder + filename))
        expected = payload['qualification_summary']
        deployment = DynamicDeployment.load(
            label=entry['label'], relative=entry['dynamic_profile'],
            platform=platform_key, park=payload['park'],
            live_variant=ride_config.resolve_variant(entry),
            global_settings=TelemetryConfig(root/'sims/nolimits2/telemetry_config.ini').snapshot(),
            root=staged_root)
        from core.dynamic_qualification import load_qualification
        actual = load_qualification(staged/'qualification.json', deployment.profile)
        if expected != actual:
            raise DynamicProfileError('Package qualification summary differs from validated report')
        dest.parent.mkdir(parents=True, exist_ok=True)
        staged.rename(dest)
        try:
            store.add_candidate(entry)  # Single locked and atomic catalogue write
        except Exception:
            shutil.rmtree(dest, ignore_errors=True)
            raise
    return entry['label'], entry['dynamic_profile']
