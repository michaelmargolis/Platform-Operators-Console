# settings.py ver 1
"""Persistent sim_player preferences; independent of POC operational settings.

Qt-free by design. QSettings is supplied by the GUI, but a fake settings
store can be used in tests. Experimental motion configuration is not stored
here; it remains separate from UI/network preferences.
"""
from __future__ import annotations

from dataclasses import dataclass
import ipaddress


@dataclass(frozen=True)
class VisualizerSettings:
    enabled: bool = False
    ip_address: str = "127.0.0.1"
    port: int = 10020

    def validate(self) -> "VisualizerSettings":
        address = str(self.ip_address).strip()
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError as exc:
            raise ValueError("Visualizer address must be a valid IPv4 address") from exc
        if parsed.version != 4:
            raise ValueError("Visualizer currently supports IPv4 addresses only")
        try:
            port = int(self.port)
        except (ValueError, TypeError) as exc:
            raise ValueError("Visualizer port must be an integer") from exc
        if not 1 <= port <= 65535:
            raise ValueError("Visualizer port must be between 1 and 65535")
        return VisualizerSettings(bool(self.enabled), address, port)


def _read_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def load_visualizer_settings(store) -> VisualizerSettings:
    """Load preferences, reverting malformed legacy values to safe defaults."""
    value = VisualizerSettings(
        enabled=_read_bool(store.value("visualizer/enabled", False)),
        ip_address=str(store.value("visualizer/ip_address", "127.0.0.1")),
        port=store.value("visualizer/port", 10020),
    )
    try:
        return value.validate()
    except ValueError:
        return VisualizerSettings()


def save_visualizer_settings(store, value: VisualizerSettings) -> None:
    value = value.validate()
    store.setValue("visualizer/enabled", value.enabled)
    store.setValue("visualizer/ip_address", value.ip_address)
    store.setValue("visualizer/port", value.port)
