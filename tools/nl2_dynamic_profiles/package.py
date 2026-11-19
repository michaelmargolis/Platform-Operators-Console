#package.py ver 2
"""Ride Analyzer candidate-package exporter (Qt-free; never changes POC catalogue).

The exported ZIP is the documented handoff to POC. It contains a complete
runtime bundle, a bound Analyzer qualification report, and a candidate entry.
POC's importer atomically merges the entry into its own platform catalogue.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
import uuid
from zipfile import ZipFile, ZIP_DEFLATED

from core.dynamic_profiles import SCHEMA, sha256_file
from core.dynamic_qualification import make_qualification, read_and_validate_qualification
from sims.nolimits2 import ride_config
from tools.nl2_sync.profile import DynamicProfile, load_reference, digest_json
from tools.nl2_dynamic_profiles.promote import _candidate_entry

PACKAGE_SCHEMA = 'poc.nl2.dynamic-candidate-package/1'


def prepare_bundle(profile_directory, label, *, export_dir=None, token=None):
    """Export a *new* immutable ZIP; no runtime bundle/catalogue is installed.

    Return (path_to_zip, repository_relative_deployment_manifest, summary).
    The caller can show/copy path_to_zip.parent in the Analyzer UX.
    """
    candidate = Path(profile_directory).expanduser().resolve()
    if not isinstance(label, str) or not label.strip():
        raise ValueError('A non-empty candidate label is required')
    profile_path = candidate / 'profile.json'
    reference_path = candidate / 'reference.npz'
    validation_path = candidate / 'validation.json'
    if any(not f.is_file() or f.is_symlink() for f in
           (profile_path, reference_path, validation_path)):
        raise ValueError('Select a complete profile/reference/validation research directory')
    profile = DynamicProfile.load(profile_path)
    load_reference(profile_path, profile)
    platform, park = profile.identity['platform'], profile.identity['park']
    if park not in ride_config.PARKS:
        raise ValueError('Dynamic profile refers to an unknown configured ride: ' + str(park))
    ride_config.validate_variant_label_format(label, park)
    report = make_qualification(profile, validation_path)
    summary = read_and_validate_qualification(profile, report)
    token = token or uuid.uuid4().hex[:12]
    if not re.fullmatch(r'[0-9a-f]{8,32}', token):
        raise ValueError('Invalid bundle identifier')
    relative = f'ride_profiles/{platform}/{park}/{token}/deployment.json'
    entry = _candidate_entry(profile, label, relative)
    ride_config.validate(variants=[entry], show_variant_modifiers=True)
    export_dir = Path(export_dir or (Path.cwd() / 'exports' / 'dynamic_candidates')).resolve()
    export_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r'[^A-Za-z0-9_-]+', '_', label).strip('_')[:65]
    package_path = export_dir / f'{slug}_{platform}_{token}.zip'
    if package_path.exists():
        raise FileExistsError('Candidate export already exists: ' + str(package_path))
    rel_dir = Path('sims/nolimits2/ride_profiles') / platform / park / token
    with tempfile.TemporaryDirectory(prefix='.dynamic-export-', dir=export_dir) as td:
        stage = Path(td)
        bundle = stage / rel_dir
        bundle.mkdir(parents=True)
        shutil.copyfile(profile_path, bundle / 'profile.json')
        shutil.copyfile(reference_path, bundle / 'reference.npz')
        (bundle / 'qualification.json').write_text(
            json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        names = ['profile.json', 'reference.npz', 'qualification.json']
        hashes = {n: sha256_file(bundle / n) for n in names}
        deployment = {
            'schema': SCHEMA, 'platform': platform, 'park': park,
            'file_names': names, 'sha256': hashes,
            'profile_identity': profile.identity,
            'profile_content_sha256': digest_json(profile.to_dict()),
        }
        (bundle / 'deployment.json').write_text(
            json.dumps(deployment, sort_keys=True, indent=2) + '\n', encoding='utf-8')
        manifest = {
            'schema': PACKAGE_SCHEMA, 'candidate': entry,
            'platform': platform, 'park': park,
            'deployment_path': (rel_dir / 'deployment.json').as_posix(),
            'deployment_sha256': sha256_file(bundle / 'deployment.json'),
            'qualification_summary': summary,
            'operational_status': 'uncommissioned_candidate',
        }
        (stage / 'dynamic_candidate.json').write_text(
            json.dumps(manifest, sort_keys=True, indent=2, allow_nan=False) + '\n', encoding='utf-8')
        # ZIP is already repository-root-ready, but imported via the POC dialog
        # so that the Pi's working catalogues are merged, never overwritten.
        with ZipFile(package_path, 'x', compression=ZIP_DEFLATED, compresslevel=6) as z:
            for p in sorted(stage.rglob('*')):
                if p.is_file():
                    z.write(p, arcname=p.relative_to(stage).as_posix())
    return package_path, relative, summary
