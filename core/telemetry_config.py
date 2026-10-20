#telemetry_config.py  ver 4
from __future__ import annotations

import configparser
from pathlib import Path
from typing import Iterable


AXES = ("x", "y", "z", "roll", "pitch", "yaw")


class _ClassicalFilter:
    def __init__(self, tau: float, gain: float = 1.0):
        self.tau = max(0.001, float(tau))
        self.gain = float(gain)
        self.output = 0.0
        self.prev_input = 0.0

    def update(self, value: float, dt: float) -> float:
        if dt <= 0.0:
            return self.output * self.gain
        alpha = self.tau / (self.tau + dt)
        self.output = alpha * (self.output + float(value) - self.prev_input)
        self.prev_input = float(value)
        return self.output * self.gain


class _LegacyDecayFilter:
    """Reproduce the peak-following decay used by the legacy controller."""

    DECAY_TARGET = 0.02

    def __init__(self, duration: float):
        self.duration = max(0.01, float(duration))
        self.output = 0.0

    def update(self, value: float, dt: float) -> float:
        value = float(value)
        if abs(value) >= abs(self.output):
            self.output = value
        elif dt > 0.0:
            factor = self.DECAY_TARGET ** (float(dt) / self.duration)
            self.output *= factor
        return self.output


class TelemetryConfig:
    """Editable per-simulator gain and washout settings.

    The file lives in the selected simulator directory because POC writes these
    values and each simulator needs independent motion tuning.
    """

    DEFAULTS = {
        "master_gain": 100,
        "gains": [100] * 6,
        "washout_type": "classical",
        "washout_gain": 1.0,
        "enabled": [True, True, True, False, False, True],
        "tau": [1.2, 1.2, 1.8, 1.0, 1.0, 2.2],
        "legacy_duration": [12.0] * 6,
    }

    def __init__(self, path: Path):
        self.path = Path(path)
        self.values = {}
        self._filters = [None] * 6
        self._temporary_classical_z = False
        self._temporary_classical_z_filter = None
        self.load()

    def load(self) -> dict:
        values = {
            "master_gain": self.DEFAULTS["master_gain"],
            "gains": list(self.DEFAULTS["gains"]),
            "washout_type": self.DEFAULTS["washout_type"],
            "washout_gain": self.DEFAULTS["washout_gain"],
            "enabled": list(self.DEFAULTS["enabled"]),
            "tau": list(self.DEFAULTS["tau"]),
            "legacy_duration": list(self.DEFAULTS["legacy_duration"]),
        }
        parser = configparser.ConfigParser()
        if self.path.exists():
            parser.read(self.path)
            gains = parser["Gains"] if parser.has_section("Gains") else {}
            values["master_gain"] = _int(gains.get("master", 100), 100, 0, 100)
            values["gains"] = [
                _int(gains.get(axis, 100), 100, 0, 200) for axis in AXES
            ]

            wash = parser["Washout"] if parser.has_section("Washout") else {}
            wash_type = str(wash.get("type", values["washout_type"])).strip().lower()
            if wash_type in ("disabled", "classical", "legacy"):
                values["washout_type"] = wash_type
            values["washout_gain"] = _float(wash.get("gain", 1.0), 1.0, 0.0, 10.0)
            values["enabled"] = [
                _bool(wash.get(f"enabled_{axis}", "1" if default else "0"), default)
                for axis, default in zip(AXES, values["enabled"])
            ]
            values["tau"] = [
                _float(wash.get(f"tau_{axis}", default), default, 0.01, 60.0)
                for axis, default in zip(AXES, values["tau"])
            ]
            values["legacy_duration"] = [
                _float(wash.get(f"legacy_duration_{axis}", default), default, 0.01, 120.0)
                for axis, default in zip(AXES, values["legacy_duration"])
            ]

        self.values = values
        self._rebuild_filters()
        return self.snapshot()

    def snapshot(self) -> dict:
        return {
            "master_gain": int(self.values["master_gain"]),
            "gains": list(self.values["gains"]),
            "washout_type": str(self.values["washout_type"]),
            "washout_gain": float(self.values["washout_gain"]),
            "enabled": list(self.values["enabled"]),
            "tau": list(self.values["tau"]),
            "legacy_duration": list(self.values["legacy_duration"]),
        }

    def apply_values(self, values: dict) -> None:
        self.values["master_gain"] = _int(values.get("master_gain", 100), 100, 0, 100)
        self.values["gains"] = [
            _int(v, 100, 0, 200) for v in _six(values.get("gains"), 100)
        ]
        wash_type = str(values.get("washout_type", "classical")).strip().lower()
        self.values["washout_type"] = wash_type if wash_type in ("disabled", "classical", "legacy") else "disabled"
        self.values["washout_gain"] = _float(values.get("washout_gain", 1.0), 1.0, 0.0, 10.0)
        self.values["enabled"] = [bool(v) for v in _six(values.get("enabled"), False)]
        self.values["tau"] = [_float(v, 1.0, 0.01, 60.0) for v in _six(values.get("tau"), 1.0)]
        self.values["legacy_duration"] = [
            _float(v, 12.0, 0.01, 120.0)
            for v in _six(values.get("legacy_duration"), 12.0)
        ]
        self._rebuild_filters()

    def save(self) -> None:
        parser = configparser.ConfigParser()
        parser["Gains"] = {"master": str(int(self.values["master_gain"]))}
        for axis, value in zip(AXES, self.values["gains"]):
            parser["Gains"][axis] = str(int(value))

        parser["Washout"] = {
            "type": str(self.values["washout_type"]),
            "gain": f"{float(self.values['washout_gain']):.4f}",
        }
        for i, axis in enumerate(AXES):
            parser["Washout"][f"enabled_{axis}"] = "1" if self.values["enabled"][i] else "0"
            parser["Washout"][f"tau_{axis}"] = f"{float(self.values['tau'][i]):.4f}"
            parser["Washout"][f"legacy_duration_{axis}"] = f"{float(self.values['legacy_duration'][i]):.4f}"

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as stream:
            stream.write("#telemetry_config.ini ver 2\n")
            parser.write(stream)

    def scale_without_washout(self, transform: Iterable[float], intensity_percent: float) -> tuple[float, ...]:
        master = self.values["master_gain"] / 100.0
        intensity = max(0.0, min(100.0, float(intensity_percent))) / 100.0
        return tuple(
            float(value) * (gain / 100.0) * master * intensity
            for value, gain in zip(transform, self.values["gains"])
        )

    def process(self, transform: Iterable[float], intensity_percent: float, dt: float) -> tuple[float, ...]:
        scaled = self.scale_without_washout(transform, intensity_percent)
        result = []
        for index, (value, filt) in enumerate(zip(scaled, self._filters)):
            if index == 2 and self._temporary_classical_z:
                filt = self._temporary_classical_z_filter
            result.append(filt.update(value, dt) if filt is not None else value)
        return tuple(result)

    def set_temporary_classical_z(self, enabled: bool) -> None:
        """Override Z with classical washout without changing saved settings."""
        self._temporary_classical_z = bool(enabled)
        self._temporary_classical_z_filter = _ClassicalFilter(
            self.values["tau"][2], self.values["washout_gain"]
        )

    def _rebuild_filters(self) -> None:
        kind = self.values["washout_type"]
        global_gain = self.values["washout_gain"]
        filters = []
        for i in range(6):
            if kind == "disabled" or not self.values["enabled"][i]:
                filters.append(None)
            elif kind == "classical":
                filters.append(_ClassicalFilter(self.values["tau"][i], global_gain))
            elif kind == "legacy":
                filters.append(_LegacyDecayFilter(self.values["legacy_duration"][i]))
            else:
                filters.append(None)
        self._filters = filters
        self._temporary_classical_z_filter = _ClassicalFilter(
            self.values["tau"][2], global_gain
        )


def _six(values, default):
    values = list(values or [])
    return (values + [default] * 6)[:6]


def _int(value, default, minimum, maximum):
    try:
        return max(minimum, min(maximum, int(float(value))))
    except (TypeError, ValueError):
        return default


def _float(value, default, minimum, maximum):
    try:
        return max(minimum, min(maximum, float(value)))
    except (TypeError, ValueError):
        return default


def _bool(value, default):
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    return default
