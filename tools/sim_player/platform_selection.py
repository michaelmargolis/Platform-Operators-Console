# platform_selection.py ver 3
"""Read-only platform identity/provenance for offline replay (no hardware I/O)."""
from importlib import import_module
import hashlib
from pathlib import Path

from config.platforms import PLATFORMS
from core.config_store import ConfigStore

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
    canonical = Path(path) if path is not None else ROOT / 'poc_config.json'
    try:
        config = ConfigStore(canonical)
    except RuntimeError as exc:
        raise ValueError(str(exc)) from exc
    key = config.get('selected_platform', 'platform_v2')
    if key not in PLATFORMS:
        raise ValueError(
            f"Invalid selected_platform {key!r} in {config.local_path}"
        )
    return key
