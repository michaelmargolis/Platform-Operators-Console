# variant_store.py ver 1
"""Shared, Qt-independent per-platform NL2 variant catalog and durable reviews.

Working files: ride_variants/<platform_key>.json (or POC_RIDE_VARIANTS_DIR).
Shipped defaults: ride_variants/defaults/<platform_key>.json; never overwritten
onto an existing working file. A running POC keeps its startup catalog; only
review statuses may change. Concurrent/external edits require an explicit reload.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import logging
import os
from pathlib import Path
import tempfile

from config.platforms import PLATFORMS
from sims.nolimits2 import ride_config

STATUSES = frozenset(("candidate", "preferred", "rejected"))
DEFAULT_DIRECTORY = Path(__file__).resolve().parent / "ride_variants"


class VariantStoreError(ValueError):
    pass


class VariantConflictError(VariantStoreError):
    pass


def _unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise VariantStoreError(f"Duplicate JSON key: {name!r}")
        result[name] = value
    return result


def _decode(raw):
    try:
        return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_object,
                          parse_constant=lambda value: (_ for _ in ()).throw(
                              VariantStoreError(f"Non-finite JSON number: {value}")))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise VariantStoreError(f"Invalid variant JSON: {exc}") from exc


def validate_document(data, platform_key):
    if platform_key not in PLATFORMS:
        raise VariantStoreError(f"Unknown platform key: {platform_key!r}")
    if not isinstance(data, dict) or set(data) != {"schema_version", "platform_key", "variants"}:
        raise VariantStoreError("Expected schema_version, platform_key and variants in variant JSON")
    if type(data['schema_version']) is not int or data['schema_version'] != 1:
        raise VariantStoreError("Unsupported ride variant schema_version (expected 1)")
    if data['platform_key'] != platform_key:
        raise VariantStoreError(f"Variant file belongs to {data['platform_key']!r}, not {platform_key!r}")
    if not isinstance(data['variants'], list):
        raise VariantStoreError("variants must be a JSON array")
    try:
        ride_config.validate(variants=data['variants'], show_variant_modifiers=True)
    except (ValueError, TypeError, KeyError) as exc:
        raise VariantStoreError(str(exc)) from exc
    preferred = set()
    for entry in data['variants']:
        if entry.get('status', 'candidate') == 'preferred':
            if entry['park'] in preferred:
                raise VariantStoreError(f"Multiple preferred variants for park {entry['park']!r}")
            preferred.add(entry['park'])
    return data


def working_directory(directory=None):
    return Path(directory or os.environ.get('POC_RIDE_VARIANTS_DIR') or DEFAULT_DIRECTORY).resolve()


@contextmanager
def _write_lock(path):
    lock = path.with_name('.' + path.name + '.lock')
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise VariantConflictError(
            f"Variant file is being saved: {path}. Close the other writer and retry. "
            f"After a crashed writer, remove {lock.name} only while both apps are stopped.") from exc
    try:
        with os.fdopen(fd, 'w', encoding='ascii') as stream:
            stream.write(str(os.getpid()))
        yield
    finally:
        try:
            lock.unlink(missing_ok=True)
        except OSError:
            # Do not report a successfully committed catalog as a failed save.
            # A retained lock blocks subsequent writes until explicitly cleared.
            logging.getLogger(__name__).warning("Cannot remove variant write lock %s", lock,
                                                exc_info=True)


def _atomic_bytes(path, raw):
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name + '.', suffix='.tmp', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _encoded(data):
    return (json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')


class VariantStore:
    def __init__(self, platform_key, data, *, path=None, raw=None):
        validate_document(data, platform_key)
        self.platform_key = platform_key
        self.path = Path(path) if path is not None else None
        self._data = deepcopy(data)
        self._raw = raw

    @classmethod
    def load(cls, platform_key, *, directory=None):
        if platform_key not in PLATFORMS:
            raise VariantStoreError(f"Unknown platform key: {platform_key!r}")
        path = working_directory(directory) / (platform_key + '.json')
        if not path.exists():
            default = DEFAULT_DIRECTORY / 'defaults' / (platform_key + '.json')
            if not default.is_file():
                raise VariantStoreError(f"No variant file for {platform_key}: {path}")
            raw = default.read_bytes()
            validate_document(_decode(raw), platform_key)
            path.parent.mkdir(parents=True, exist_ok=True)
            with _write_lock(path):
                if not path.exists():
                    _atomic_bytes(path, raw)
        raw = path.read_bytes()
        try:
            return cls(platform_key, _decode(raw), path=path, raw=raw)
        except VariantStoreError as exc:
            raise VariantStoreError(f"{path}: {exc}") from exc

    @classmethod
    def create(cls, platform_key, data, *, directory=None):
        """Create an explicitly supplied catalog; never overwrite an existing one."""
        validate_document(data, platform_key)
        path = working_directory(directory) / (platform_key + '.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = _encoded(data)
        with _write_lock(path):
            if path.exists():
                raise VariantConflictError(f"Variant file already exists; nothing overwritten: {path}")
            _atomic_bytes(path, raw)
        return cls(platform_key, data, path=path, raw=raw)

    @classmethod
    def for_capture(cls, platform_key='platform_v2'):
        """Recorder enumerates physical parks independently of review decisions."""
        return cls(platform_key, {
            'schema_version': 1, 'platform_key': platform_key,
            'variants': [{'label': p['name'], 'park': key} for key, p in ride_config.PARKS.items()],
        })

    def entries(self):
        return tuple(deepcopy(self._data['variants']))

    def entry(self, label):
        for entry in self._data['variants']:
            if entry['label'] == label:
                return deepcopy(entry)
        raise KeyError(label)

    def labels(self, park_key=None):
        return tuple(v['label'] for v in self._data['variants']
                     if park_key is None or v['park'] == park_key)

    def visible_labels(self, park_key=None):
        preferred = {v['park'] for v in self._data['variants']
                     if v.get('status', 'candidate') == 'preferred'}
        return tuple(v['label'] for v in self._data['variants']
                     if (park_key is None or v['park'] == park_key)
                     and (v.get('status', 'candidate') == 'preferred'
                          or (v.get('status', 'candidate') == 'candidate' and v['park'] not in preferred)))

    def player_labels(self, park_key=None):
        """Research UI may inspect new candidates even while POC has a preferred ride."""
        return tuple(v["label"] for v in self._data["variants"]
                     if (park_key is None or v["park"] == park_key)
                     and v.get("status", "candidate") != "rejected")

    def validate_display(self, show_modifiers=None):
        show = ride_config.SHOW_VARIANT_MODIFIERS if show_modifiers is None else show_modifiers
        visible = set(self.visible_labels())
        ride_config.validate(variants=[v for v in self._data['variants'] if v['label'] in visible],
                             show_variant_modifiers=show)

    def default_label(self, park_key=None):
        visible = self.visible_labels(park_key)
        if not visible:
            return None
        if park_key is None:
            # Start on the configured park, preferring its accepted variant.
            for v in self._data['variants']:
                if v['label'] == ride_config.DEFAULT_VARIANT_LABEL:
                    match = self.default_label(v['park'])
                    if match is not None:
                        return match
            return visible[0]
        for label in visible:
            if self.entry(label).get('status') == 'preferred':
                return label
        standard = ride_config.PARKS[park_key]['name']
        return standard if standard in visible else visible[0]

    def snapshot(self, label):
        result = ride_config.resolve_variant(self.entry(label))
        result["platform_key"] = self.platform_key
        return result

    def review_preview(self, label, decision):
        if decision not in ('preferred', 'rejected'):
            raise VariantStoreError("Review decision must be preferred or rejected")
        original = self.entry(label)
        if original.get('status') == 'rejected':
            raise VariantStoreError("This loaded variant is already rejected; select another ride")
        data = deepcopy(self._data)
        for v in data['variants']:
            if v['label'] == label:
                v['status'] = decision
            elif decision == 'preferred' and v['park'] == original['park']:
                v['status'] = 'rejected'
        candidate = VariantStore(self.platform_key, data)
        return candidate

    def _commit(self, updated):
        if self.path is None:
            raise VariantStoreError("This catalog is read-only")
        validate_document(updated, self.platform_key)
        raw = _encoded(updated)
        with _write_lock(self.path):
            if not self.path.exists() or self.path.read_bytes() != self._raw:
                raise VariantConflictError(
                    "Variant file changed outside this session. Nothing saved. "
                    "Restart POC or reselect the platform in the player before retrying.")
            # Backup must succeed before replacing the active catalog.
            _atomic_bytes(self.path.with_suffix('.json.bak'), self._raw)
            _atomic_bytes(self.path, raw)
        self._data = deepcopy(updated)
        self._raw = raw

    def review(self, label, decision):
        updated = self.review_preview(label, decision)
        updated.validate_display()
        self._commit(updated._data)

    def add_candidate(self, entry):
        entry = deepcopy(entry)
        entry['status'] = 'candidate'
        updated = deepcopy(self._data)
        updated['variants'].append(entry)
        # POC-only duplicate display validation is intentionally deferred to
        # POC startup. A researcher can save multiple candidates with full IDs.
        self._commit(updated)
        return entry['label']

    @property
    def fingerprint(self):
        return hashlib.sha256(self._raw or _encoded(self._data)).hexdigest()
