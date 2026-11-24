#config_store.py  ver 11
from __future__ import annotations

import ipaddress
import json
from pathlib import Path
import shutil


LOCAL_CONFIG_FILENAME = "poc_config_local.json"


def _json_type(value) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if value is None:
        return "null"
    return type(value).__name__


def _structure(value: dict, prefix: str = "") -> dict[str, str]:
    result: dict[str, str] = {}
    for key, child in value.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        result[path] = _json_type(child)
        if isinstance(child, dict):
            result.update(_structure(child, path))
    return result


def _structure_error(canonical_path: Path, local_path: Path,
                     canonical: dict, local: dict) -> RuntimeError | None:
    expected = _structure(canonical)
    actual = _structure(local)

    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    type_mismatches = sorted(
        path for path in set(expected) & set(actual)
        if expected[path] != actual[path]
    )
    if not (missing or extra or type_mismatches):
        return None

    lines = [
        "Local POC configuration does not match the canonical field structure.",
        f"Canonical: {canonical_path}",
        f"Local: {local_path}",
        "",
        "The local file is the runtime configuration and is never merged with "
        "the canonical file. Update it so both files contain the same fields "
        "and JSON field types; values may differ.",
    ]
    if missing:
        lines.extend(("", "Missing from local:"))
        lines.extend(f"  - {path} ({expected[path]})" for path in missing)
    if extra:
        lines.extend(("", "Not present in canonical:"))
        lines.extend(f"  - {path} ({actual[path]})" for path in extra)
    if type_mismatches:
        lines.extend(("", "Field type mismatch:"))
        lines.extend(
            f"  - {path}: canonical={expected[path]}, local={actual[path]}"
            for path in type_mismatches
        )
    return RuntimeError("\n".join(lines))


class ConfigStore:
    """Use a complete machine-local config validated against a tracked template.

    poc_config.json is the Git-tracked canonical template/schema.
    poc_config_local.json is the complete machine-local runtime file and is
    excluded from Git. If local does not exist it is copied byte-for-byte from
    canonical. Thereafter values are never merged between the files.

    At every load the recursive field names and JSON field types must match.
    This catches added, removed, renamed or structurally modified fields while
    deliberately allowing machine-specific values to differ.
    """

    def __init__(
        self,
        path: str | Path = "poc_config.json",
        local_path: str | Path | None = None,
    ):
        self.path = Path(path)
        self.local_path = (
            Path(local_path)
            if local_path is not None
            else self.path.with_name(LOCAL_CONFIG_FILENAME)
        )
        self.local_created = False
        self.data = self._load()

    @staticmethod
    def _read_json(path: Path, label: str) -> dict:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise RuntimeError(f"Unable to read {label} {path}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Invalid JSON in {label} {path} at line {exc.lineno}, "
                f"column {exc.colno}: {exc.msg}"
            ) from exc
        if not isinstance(data, dict):
            raise RuntimeError(f"{label.capitalize()} {path} must contain a JSON object")
        return data

    def _load(self) -> dict:
        if not self.path.is_file():
            raise RuntimeError(f"Canonical POC configuration is missing: {self.path}")

        canonical = self._read_json(self.path, "canonical POC configuration")
        self._validate(canonical, self.path)

        if not self.local_path.exists():
            try:
                shutil.copyfile(self.path, self.local_path)
            except OSError as exc:
                raise RuntimeError(
                    f"Unable to create local POC configuration {self.local_path}: {exc}"
                ) from exc
            self.local_created = True

        local = self._read_json(self.local_path, "local POC configuration")
        mismatch = _structure_error(self.path, self.local_path, canonical, local)
        if mismatch is not None:
            raise mismatch
        self._validate(local, self.local_path)
        return local

    def _validate(self, data: dict, source_path: Path) -> None:
        from config.platforms import PLATFORMS
        from core.sim_registry import SIMS

        platform = data.get("selected_platform")
        if platform not in PLATFORMS:
            raise RuntimeError(
                f"Invalid selected_platform {platform!r} in {source_path}; "
                f"expected one of: {', '.join(PLATFORMS)}"
            )
        simulator = data.get("selected_sim")
        simulator_keys = {definition.key for definition in SIMS}
        if simulator not in simulator_keys:
            raise RuntimeError(
                f"Invalid selected_sim {simulator!r} in {source_path}; "
                f"expected one of: {', '.join(sorted(simulator_keys))}"
            )
        legacy_pins = data.get("use_legacy_pi_pins")
        if not isinstance(legacy_pins, bool):
            raise RuntimeError(
                f"Invalid use_legacy_pi_pins {legacy_pins!r} in {source_path}; "
                "expected JSON true or false"
            )
        from core.platform_geometry import validate_operating_floor_clearance
        try:
            validate_operating_floor_clearance(
                data.get("ground_loading_min_clearance_mm")
            )
        except ValueError as exc:
            raise RuntimeError(
                f"Invalid ground-loading clearance in {source_path}: {exc}"
            ) from exc
        self._validate_endpoint(
            data.get("sim_host_service"), "sim_host_service", source_path,
            allow_disabled=False,
        )
        self._validate_endpoint(
            data.get("digital_twin"), "digital_twin", source_path,
            allow_disabled=True,
        )

    @staticmethod
    def _validate_endpoint(value, name: str, source_path: Path,
                           *, allow_disabled: bool) -> None:
        if not isinstance(value, dict):
            raise RuntimeError(f"{name} in {source_path} must be a JSON object")
        address = value.get("address")
        if allow_disabled and address == "-":
            pass
        else:
            try:
                parsed = ipaddress.ip_address(str(address))
            except ValueError as exc:
                raise RuntimeError(
                    f"Invalid {name} IPv4 address {address!r} in {source_path}"
                ) from exc
            if parsed.version != 4:
                raise RuntimeError(f"{name} address in {source_path} must be IPv4")
        try:
            port = int(value.get("port"))
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"Invalid {name} port in {source_path}") from exc
        if not 1 <= port <= 65535:
            raise RuntimeError(
                f"{name} port in {source_path} must be between 1 and 65535"
            )

    def save(self) -> None:
        try:
            self.local_path.write_text(
                json.dumps(self.data, indent=2) + "\n", encoding="utf-8"
            )
        except OSError as exc:
            raise RuntimeError(
                f"Unable to write local POC configuration {self.local_path}: {exc}"
            ) from exc

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value) -> None:
        self.data[key] = value
        self._validate(self.data, self.local_path)
        self.save()
