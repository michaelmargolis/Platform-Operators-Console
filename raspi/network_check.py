#network_check.py  ver 1
"""Validate the configured built-in platform adapter before POC starts."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


CONFIG_PATH = Path(__file__).with_name("network-config.conf")
MAC_RE = re.compile(r"^(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$")
INTERFACE_RE = re.compile(r"^[a-zA-Z0-9_.:-]+$")


def load_config(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"invalid line in {path.name}: {raw_line}")
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def check_platform_adapter(
    config_path: Path = CONFIG_PATH,
    sys_class_net: Path = Path("/sys/class/net"),
) -> str | None:
    try:
        values = load_config(config_path)
    except (OSError, ValueError) as exc:
        return f"Cannot read {config_path}: {exc}"

    interface = values.get("PLATFORM_INTERFACE", "")
    expected_mac = values.get("PLATFORM_MAC", "").lower()
    if not interface:
        return f"PLATFORM_INTERFACE is missing from {config_path}"
    if not INTERFACE_RE.fullmatch(interface):
        return f"PLATFORM_INTERFACE is invalid in {config_path}"
    if not MAC_RE.fullmatch(expected_mac):
        return f"PLATFORM_MAC is invalid in {config_path}"

    address_path = sys_class_net / interface / "address"
    try:
        actual_mac = address_path.read_text(encoding="ascii").strip().lower()
    except OSError:
        return f"Configured platform interface {interface} was not found"
    if actual_mac != expected_mac:
        return (
            f"{interface} has MAC {actual_mac}; expected {expected_mac}.\n"
            f"Update {config_path} for this Raspberry Pi."
        )
    return None


def show_error(detail: str) -> None:
    try:
        from PyQt5 import QtWidgets

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
        QtWidgets.QMessageBox.critical(
            None,
            "POC Network Configuration",
            "Network adapters need configuring.\n\n" + detail,
        )
        app.processEvents()
    except Exception as exc:
        print(f"Unable to display network error dialog: {exc}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gui", action="store_true", help="show a Qt error dialog")
    args = parser.parse_args()

    error = check_platform_adapter()
    if error is None:
        print("POC platform network adapter check OK")
        return 0

    print(f"NETWORK ADAPTERS NEED CONFIGURING: {error}", file=sys.stderr)
    if args.gui:
        show_error(error)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
