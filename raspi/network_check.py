#network_check.py  ver 2
"""Validate the configured platform interface and IPv4 address before POC starts."""
from __future__ import annotations

import argparse
import ipaddress
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable


CONFIG_PATH = Path(__file__).with_name("network-config.conf")
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


def _interface_ipv4_addresses(interface: str) -> set[ipaddress.IPv4Address]:
    completed = subprocess.run(
        ["ip", "-4", "-o", "addr", "show", "dev", interface],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return set()
    addresses: set[ipaddress.IPv4Address] = set()
    for line in completed.stdout.splitlines():
        fields = line.split()
        try:
            inet_index = fields.index("inet")
            cidr = fields[inet_index + 1]
            addresses.add(ipaddress.ip_interface(cidr).ip)
        except (ValueError, IndexError):
            continue
    return addresses


def check_platform_network(
    config_path: Path = CONFIG_PATH,
    sys_class_net: Path = Path("/sys/class/net"),
    address_reader: Callable[[str], set[ipaddress.IPv4Address]] = _interface_ipv4_addresses,
) -> str | None:
    try:
        values = load_config(config_path)
    except (OSError, ValueError) as exc:
        return f"Cannot read {config_path}: {exc}"

    interface = values.get("PLATFORM_INTERFACE", "")
    configured_address = values.get("PLATFORM_ADDRESS", "")
    if not interface:
        return f"PLATFORM_INTERFACE is missing from {config_path}"
    if not INTERFACE_RE.fullmatch(interface):
        return f"PLATFORM_INTERFACE is invalid in {config_path}"
    if not (sys_class_net / interface).is_dir():
        return f"Configured platform interface {interface} was not found"
    try:
        expected = ipaddress.ip_interface(configured_address)
    except ValueError:
        return f"PLATFORM_ADDRESS is invalid in {config_path}"
    if expected.version != 4:
        return f"PLATFORM_ADDRESS must be IPv4 in {config_path}"

    actual_addresses = address_reader(interface)
    if expected.ip not in actual_addresses:
        actual = ", ".join(str(address) for address in sorted(actual_addresses)) or "no IPv4 address"
        return (
            f"{interface} has {actual}; expected {expected.ip}.\n"
            f"Apply the platform address from {config_path}."
        )
    return None


# Backward-compatible name for older imports/tests.
def check_platform_adapter(
    config_path: Path = CONFIG_PATH,
    sys_class_net: Path = Path("/sys/class/net"),
) -> str | None:
    return check_platform_network(config_path=config_path, sys_class_net=sys_class_net)


def show_error(detail: str) -> None:
    try:
        from PyQt5 import QtWidgets

        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
        QtWidgets.QMessageBox.critical(
            None,
            "POC Network Configuration",
            "Platform network needs configuring.\n\n" + detail,
        )
        app.processEvents()
    except Exception as exc:
        print(f"Unable to display network error dialog: {exc}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gui", action="store_true", help="show a Qt error dialog")
    parser.add_argument(
        "--wait",
        type=float,
        default=10.0,
        help="seconds to wait for the configured IPv4 address (default: 10)",
    )
    args = parser.parse_args()

    deadline = time.monotonic() + max(0.0, args.wait)
    while True:
        error = check_platform_network()
        if error is None:
            print("POC platform network check OK")
            return 0
        if time.monotonic() >= deadline:
            break
        time.sleep(0.5)

    print(f"PLATFORM NETWORK NEEDS CONFIGURING: {error}", file=sys.stderr)
    if args.gui:
        show_error(error)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
