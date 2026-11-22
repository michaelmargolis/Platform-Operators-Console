#!/bin/bash
# start-test-poc.sh  ver 5
set -euo pipefail

TEST_PROFILE="POC Test Temporary"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
CONFIG_FILE="$SCRIPT_DIR/network-config.conf"
PRIV_HELPER="/usr/local/libexec/poc-test-network-helper"
EXPECTED_HELPER_API="1"

read_setting() {
    local key="$1"
    local value
    value="$(sed -n "s/^[[:space:]]*$key[[:space:]]*=[[:space:]]*//p" "$CONFIG_FILE" | tail -n 1)"
    value="${value//$'\r'/}"
    value="${value//[[:space:]]/}"
    if [[ -z "$value" ]]; then
        echo "ERROR: $key is missing from $CONFIG_FILE" >&2
        exit 1
    fi
    printf '%s' "$value"
}

if ! command -v nmcli >/dev/null 2>&1; then
    echo "ERROR: NetworkManager nmcli is not installed" >&2
    exit 1
fi
if [[ ! -r "$CONFIG_FILE" ]]; then
    echo "ERROR: cannot read $CONFIG_FILE" >&2
    exit 1
fi
if [[ ! -x "$SCRIPT_DIR/start_console.sh" ]]; then
    echo "ERROR: $SCRIPT_DIR/start_console.sh is not executable" >&2
    exit 1
fi

PLATFORM_INTERFACE="$(read_setting PLATFORM_INTERFACE)"
PLATFORM_ADDRESS="$(read_setting PLATFORM_ADDRESS)"
TARGET_IP="${PLATFORM_ADDRESS%/*}"

if [[ ! "$PLATFORM_INTERFACE" =~ ^[a-zA-Z0-9_.:-]+$ ]]; then
    echo "ERROR: invalid PLATFORM_INTERFACE: $PLATFORM_INTERFACE" >&2
    exit 1
fi
python3 - "$PLATFORM_ADDRESS" <<'PY'
import ipaddress
import sys
try:
    address = ipaddress.ip_interface(sys.argv[1])
except ValueError as exc:
    raise SystemExit(f"ERROR: invalid PLATFORM_ADDRESS: {exc}")
if address.version != 4:
    raise SystemExit("ERROR: PLATFORM_ADDRESS must be IPv4")
PY
if [[ ! -d "/sys/class/net/$PLATFORM_INTERFACE" ]]; then
    echo "ERROR: configured platform interface $PLATFORM_INTERFACE was not found" >&2
    exit 1
fi
if [[ "$(nmcli -g GENERAL.TYPE device show "$PLATFORM_INTERFACE" 2>/dev/null || true)" != "ethernet" ]]; then
    echo "ERROR: $PLATFORM_INTERFACE is not an Ethernet interface" >&2
    exit 1
fi

ORIGINAL_UUID="$(nmcli -t -f UUID,DEVICE connection show --active | awk -F: -v dev="$PLATFORM_INTERFACE" '$2 == dev { print $1; exit }')"
ORIGINAL_NAME="$(nmcli -g GENERAL.CONNECTION device show "$PLATFORM_INTERFACE" 2>/dev/null || true)"
CURRENT_ADDRESS="$(ip -4 -br address show dev "$PLATFORM_INTERFACE" 2>/dev/null | awk '{$1=$1; print}' || true)"
[[ -n "$CURRENT_ADDRESS" ]] || CURRENT_ADDRESS="no IPv4 address"
[[ -n "$ORIGINAL_NAME" && "$ORIGINAL_NAME" != "--" ]] || ORIGINAL_NAME="none"

if [[ "$ORIGINAL_NAME" == "$TEST_PROFILE" ]]; then
    echo "ERROR: $TEST_PROFILE is already active on $PLATFORM_INTERFACE." >&2
    echo "Reboot the Pi to restore its normal NetworkManager profile before starting another test." >&2
    exit 1
fi

cat <<EOF2

============================================================
 Test Poc - temporary platform network
============================================================
 Interface        : $PLATFORM_INTERFACE
 Current profile  : $ORIGINAL_NAME
 Current address  : $CURRENT_ADDRESS
 POC test address : $PLATFORM_ADDRESS
------------------------------------------------------------
 This does NOT modify the saved NetworkManager profile.
 The normal profile is restored when Test Poc exits.
 Rebooting also removes the temporary profile and restores
 the normal saved NetworkManager configuration.

 WARNING: Do not use this address while another POC is active
 on the same platform LAN with $TARGET_IP.
============================================================
EOF2

if command -v zenity >/dev/null 2>&1; then
    if ! GSK_RENDERER=cairo GTK_A11Y=none zenity --question \
        --title="Test Poc" \
        --text="Temporarily change $PLATFORM_INTERFACE to $PLATFORM_ADDRESS and start POC?

The saved network configuration will not be changed.
Normal networking is restored when POC exits or after reboot." \
        --ok-label="Continue" \
        --cancel-label="Cancel" \
        --width=560; then
        echo "Test Poc cancelled; network unchanged."
        exit 0
    fi
else
    echo "NOTE: zenity is not installed; using terminal confirmation (no package installation is attempted)."
    read -r -p "Continue with temporary POC networking? [y/N] " reply
    case "${reply,,}" in
        y|yes) ;;
        *)
            echo "Test Poc cancelled; network unchanged."
            exit 0
            ;;
    esac
fi

# Test Poc runtime must never prompt for an administrator password. The launcher
# installer places a root-owned fixed-function helper in /usr/local/libexec and
# grants this desktop user passwordless sudo access to that helper only.
helper_api=""
if [[ -x "$PRIV_HELPER" ]]; then
    helper_api="$(sudo -n "$PRIV_HELPER" version 2>/dev/null || true)"
fi
if [[ "$helper_api" != "$EXPECTED_HELPER_API" ]]; then
    echo "ERROR: Test Poc network permission/helper is not installed or is out of date." >&2
    echo "Run this once from /home/poc/Runtime:" >&2
    echo "  sudo ./raspi/install-test-poc-launcher.sh --user $(id -un)" >&2
    exit 1
fi

TEMP_CREATED=0

cleanup() {
    local status=$?
    trap - EXIT
    set +e
    if [[ "${TEMP_CREATED:-0}" -eq 1 ]]; then
        if [[ -n "${ORIGINAL_UUID:-}" ]]; then
            echo
            echo "Restoring original $PLATFORM_INTERFACE NetworkManager profile..."
            if ! sudo -n "$PRIV_HELPER" restore "$PLATFORM_INTERFACE" "$ORIGINAL_UUID" >/dev/null 2>&1; then
                echo "WARNING: automatic restore failed; reboot will restore the saved network configuration." >&2
            fi
        else
            sudo -n "$PRIV_HELPER" delete-test >/dev/null 2>&1 || true
            echo
            echo "No original active $PLATFORM_INTERFACE profile was recorded; reboot restores normal networking."
        fi
    fi
    return "$status"
}
trap cleanup EXIT
trap 'exit 130' INT TERM HUP

# Remove a stale, inactive in-memory test profile left in the current
# NetworkManager session. A stale active profile was rejected above.
if nmcli -t -f NAME connection show | grep -Fxq "$TEST_PROFILE"; then
    sudo -n "$PRIV_HELPER" delete-test >/dev/null
fi

set +e
sudo -n "$PRIV_HELPER" check-address "$PLATFORM_INTERFACE" "$TARGET_IP" >/dev/null 2>&1
address_check_rc=$?
set -e
case "$address_check_rc" in
    0) ;;
    1)
        echo "ERROR: another device appears to be using $TARGET_IP on $PLATFORM_INTERFACE." >&2
        echo "Temporary POC networking was not activated." >&2
        exit 1
        ;;
    3)
        echo "NOTE: arping is not installed; duplicate-IP detection is unavailable."
        ;;
    *)
        echo "NOTE: duplicate-IP detection could not be completed; continuing with the existing warning." >&2
        ;;
esac

sudo -n "$PRIV_HELPER" activate "$PLATFORM_INTERFACE" "$PLATFORM_ADDRESS"
TEMP_CREATED=1

echo
echo "Temporary POC network active:"
nmcli -f DEVICE,TYPE,STATE,CONNECTION device
ip -br address show dev "$PLATFORM_INTERFACE"
echo
echo "Starting POC..."
echo "Exit POC normally to restore the original network immediately."
echo

cd "$APP_DIR"
"$SCRIPT_DIR/start_console.sh"
