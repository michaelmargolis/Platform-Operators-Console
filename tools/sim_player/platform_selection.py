# platform_selection.py ver 2
"""Read-only platform identity/provenance for offline replay (no hardware I/O)."""
from importlib import import_module
import hashlib
import json
from pathlib import Path

from config.platforms import PLATFORMS

ROOT = Path(__file__).resolve().parents[2]


def platform_metadata(key):
    if key not in PLATFORMS:
        raise ValueError(f"Unknown platform: {key!r}")
    name = PLATFORMS[key]['sop_config_module']
    module = import_module(name)
    source = Path(module.__file__).resolve()
    return {'platform_key': key,
            'platform_name': str(getattr(module.PlatformConfig, 'PLATFORM_NAME', PLATFORMS[key]['name'])),
            'platform_config_module': name,
            'platform_config_sha256': hashlib.sha256(source.read_bytes()).hexdigest()}


def platform_choices():
    """Distinct operator labels for every configured platform key.

    Legacy chair_v2 and chair_v3 use the same kinematics module, so its
    PLATFORM_NAME cannot distinguish their independent variant catalogs.
    Prefer that name unless it repeats; then use the platform registry's
    distinct display name. Keep the underlying key and geometry unchanged.
    """
    choices = []
    used_labels = set()
    for key, config in PLATFORMS.items():
        label = platform_metadata(key)['platform_name']
        if label.casefold() in used_labels:
            label = str(config['name'])
        if label.casefold() in used_labels:
            label = f'{label} ({key})'
        if label.casefold() in used_labels:
            raise ValueError(f'Platform display name is ambiguous for {key!r}')
        choices.append((key, label))
        used_labels.add(label.casefold())
    return tuple(choices)


def initial_platform_key(path=None):
    path = Path(path) if path is not None else ROOT / 'poc_config.json'
    if not path.exists():
        return 'platform_v2'
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    key = data.get('selected_platform', 'platform_v2')
    if key not in PLATFORMS:
        raise ValueError(f"Invalid selected_platform {key!r} in {path}")
    return key
