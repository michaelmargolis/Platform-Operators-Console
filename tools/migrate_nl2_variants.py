# migrate_nl2_variants.py ver 1
"""One-time import of literal v176/v177 RIDE_VARIANTS; never execute old Python.

Run before first launch of v178 if ride_config.py was locally customized:
  py tools/migrate_nl2_variants.py path/to/old_ride_config.py --platform platform_v2 --platform chair_v3

Back up/merge local PARKS and calibration edits separately. Existing JSON files
are never overwritten. This tool does not connect to a simulator or hardware.
"""
from __future__ import annotations

import argparse
import ast
from copy import deepcopy
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.platforms import PLATFORMS
from sims.nolimits2.variant_store import VariantStore, validate_document, working_directory


def read_legacy_variants(path):
    """Accept only the old literal list/tuple, without imports or execution."""
    tree = ast.parse(Path(path).read_text(encoding='utf-8-sig'), filename=str(path))
    assignments = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'RIDE_VARIANTS'
                                               for t in node.targets):
            assignments.append(node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == 'RIDE_VARIANTS':
            assignments.append(node.value)
    if len(assignments) != 1:
        raise ValueError('Expected exactly one literal RIDE_VARIANTS assignment in the backed-up file')
    try:
        entries = ast.literal_eval(assignments[0])
    except (ValueError, TypeError) as exc:
        raise ValueError('RIDE_VARIANTS is not a literal list/tuple; convert it manually to JSON') from exc
    if not isinstance(entries, (list, tuple)) or not all(isinstance(v, dict) for v in entries):
        raise ValueError('RIDE_VARIANTS must contain dictionaries in a list or tuple')
    result = deepcopy(list(entries))
    for entry in result:
        entry.setdefault('status', 'candidate')
    return result


def migrate(source, platform_keys, *, directory=None):
    entries = read_legacy_variants(source)
    keys = list(dict.fromkeys(platform_keys))
    if not keys:
        raise ValueError('Choose at least one platform')
    documents = []
    # Preflight every requested catalog before creating any file. Each actual
    # creation is independently locked; unrelated platform files are untouched.
    for key in keys:
        document = {'schema_version': 1, 'platform_key': key, 'variants': entries}
        validate_document(document, key)
        path = working_directory(directory) / (key + '.json')
        if path.exists():
            raise ValueError(f'Existing variant file will not be overwritten: {path}')
        documents.append((key, document))
    paths = []
    for key, document in documents:
        paths.append(VariantStore.create(key, document, directory=directory).path)
    return paths


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, help='Backed-up v176/v177 ride_config.py')
    parser.add_argument('--platform', action='append', required=True, choices=tuple(PLATFORMS))
    parser.add_argument('--directory', type=Path, help='Working catalog directory (normally omit)')
    args = parser.parse_args(argv)
    try:
        paths = migrate(args.source, args.platform, directory=args.directory)
    except (OSError, SyntaxError, ValueError) as exc:
        print(f'Variant migration failed: {exc}', file=sys.stderr)
        return 1
    for path in paths:
        print(f'Created {path}')
    print('PARKS and calibration data remain in ride_config.py; merge any local changes separately.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
