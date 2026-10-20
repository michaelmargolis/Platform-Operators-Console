#config_store.py  ver 7
from __future__ import annotations

import ipaddress
import json
from pathlib import Path


CONFIG_FILE_VERSION = "poc_config.json ver 6"


DEFAULTS = {
    "_file_version": CONFIG_FILE_VERSION,
    "selected_sim": "nolimits2",
    "selected_platform": "platform_v2",
    "sim_host_service": {
        "enabled": True,
        "address": "192.168.0.4",
        "port": 10040,
        "heartbeat_interval_s": 1.0,
        "heartbeat_timeout_s": 3.0
    },
    "digital_twin": {
        "address": "-",
        "port": 10020
    }
}


class ConfigStore:
    def __init__(self, path: str | Path = "poc_config.json"):
        self.path = Path(path)
        self.data, migrated = self._load()
        if migrated:
            self.save()

    def _load(self):
        data = json.loads(json.dumps(DEFAULTS))
        migrated = False
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
            except OSError as exc:
                raise RuntimeError(f"Unable to read POC configuration {self.path}: {exc}") from exc
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"Invalid JSON in POC configuration {self.path} "
                    f"at line {exc.lineno}, column {exc.colno}: {exc.msg}"
                ) from exc
            if not isinstance(loaded, dict):
                raise RuntimeError(f"POC configuration {self.path} must contain a JSON object")

            # v31+: all simulators are hosted by the SHS PC. Remove the
            # obsolete per-simulator address map from existing configs.
            if "sim_addresses" in loaded:
                loaded.pop("sim_addresses", None)
                migrated = True
            # Physical Festo UDP output is always available. Remove the
            # obsolete output gate from configurations created by earlier POC.
            if "virtual_output" in loaded:
                loaded.pop("virtual_output", None)
                migrated = True
            # The original wheelchair key is retained only as a migration alias.
            # platform_v2 is the current MDX suspended-platform selection.
            if loaded.get("selected_platform") == "wheelchair":
                loaded["selected_platform"] = "platform_v2"
                migrated = True
            if loaded.get("_file_version") != CONFIG_FILE_VERSION:
                migrated = True
            data.update(loaded)
            # Keep the config file itself versioned without using invalid JSON
            # comments. version_check.py recognises this first field.
            data["_file_version"] = CONFIG_FILE_VERSION
            if isinstance(loaded.get("sim_host_service"), dict):
                data["sim_host_service"] = {
                    **DEFAULTS["sim_host_service"], **loaded["sim_host_service"]
                }
            if isinstance(loaded.get("digital_twin"), dict):
                data["digital_twin"] = {
                    **DEFAULTS["digital_twin"], **loaded["digital_twin"]
                }
        self._validate(data)
        return data, migrated

    def _validate(self, data: dict) -> None:
        from config.platforms import PLATFORMS
        from core.sim_registry import SIMS

        platform = data.get("selected_platform")
        if platform not in PLATFORMS:
            raise RuntimeError(
                f"Invalid selected_platform {platform!r} in {self.path}; "
                f"expected one of: {', '.join(PLATFORMS)}"
            )
        simulator = data.get("selected_sim")
        simulator_keys = {definition.key for definition in SIMS}
        if simulator not in simulator_keys:
            raise RuntimeError(
                f"Invalid selected_sim {simulator!r} in {self.path}; "
                f"expected one of: {', '.join(sorted(simulator_keys))}"
            )
        self._validate_endpoint(
            data.get("sim_host_service"), "sim_host_service", allow_disabled=False
        )
        self._validate_endpoint(
            data.get("digital_twin"), "digital_twin", allow_disabled=True
        )

    def _validate_endpoint(self, value, name: str, *, allow_disabled: bool) -> None:
        if not isinstance(value, dict):
            raise RuntimeError(f"{name} in {self.path} must be a JSON object")
        address = value.get("address")
        if allow_disabled and address == "-":
            pass
        else:
            try:
                parsed = ipaddress.ip_address(str(address))
            except ValueError as exc:
                raise RuntimeError(f"Invalid {name} IPv4 address {address!r} in {self.path}") from exc
            if parsed.version != 4:
                raise RuntimeError(f"{name} address in {self.path} must be IPv4")
        try:
            port = int(value.get("port"))
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"Invalid {name} port in {self.path}") from exc
        if not 1 <= port <= 65535:
            raise RuntimeError(f"{name} port in {self.path} must be between 1 and 65535")

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value) -> None:
        self.data[key] = value
        self.save()
