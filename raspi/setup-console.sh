#!/bin/bash
# setup-console.sh  ver 13
set -euo pipefail

APP_USER="${SUDO_USER:-}"
DISPLAY_MODE="auto"
DO_FULL_UPGRADE=0

is_ssh_session() {
    if [[ -n "${SSH_CONNECTION:-}" || -n "${SSH_CLIENT:-}" || -n "${SSH_TTY:-}" ]]; then
        return 0
    fi

    # sudo can discard the SSH environment. Walk the process ancestry so an
    # installer launched as "sudo ./raspi/setup-console.sh" is still safe.
    local pid="${PPID:-0}"
    local command_name parent_pid
    while [[ "$pid" =~ ^[0-9]+$ ]] && ((pid > 1)); do
        command_name="$(cat "/proc/$pid/comm" 2>/dev/null || true)"
        if [[ "$command_name" == "sshd" || "$command_name" == sshd:* ]]; then
            return 0
        fi
        parent_pid="$(awk '/^PPid:/ { print $2 }' "/proc/$pid/status" 2>/dev/null || true)"
        [[ "$parent_pid" =~ ^[0-9]+$ ]] || break
        [[ "$parent_pid" != "$pid" ]] || break
        pid="$parent_pid"
    done
    return 1
}

usage() {
    cat <<'EOF'
Usage: sudo ./raspi/setup-console.sh [options]

Options:
  --user NAME                  desktop user that will run POC
  --display auto               HDMI or firmware-detected display (default)
  --display touch2-7           7-inch Raspberry Pi Touch Display 2
  --display waveshare-10.1     project Waveshare 10.1-inch DSI overlay
  --full-upgrade               install all current OS updates first
  -h, --help                   show this help

Run this from the root of the complete PlatformOperatorsConsole tree on a
fresh, current Raspberry Pi OS 64-bit Desktop installation.
EOF
}

while (($#)); do
    case "$1" in
        --user)
            [[ $# -ge 2 ]] || { echo "ERROR: --user requires a name" >&2; exit 2; }
            APP_USER="$2"
            shift 2
            ;;
        --display)
            [[ $# -ge 2 ]] || { echo "ERROR: --display requires a mode" >&2; exit 2; }
            DISPLAY_MODE="$2"
            shift 2
            ;;
        --full-upgrade)
            DO_FULL_UPGRADE=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "ERROR: unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: run this installer with sudo" >&2
    exit 1
fi
if [[ "$DISPLAY_MODE" != "auto" && "$DISPLAY_MODE" != "touch2-7" && "$DISPLAY_MODE" != "waveshare-10.1" ]]; then
    echo "ERROR: display mode must be auto, touch2-7 or waveshare-10.1" >&2
    exit 2
fi
if [[ ! -r /proc/device-tree/model ]] || ! tr -d '\0' </proc/device-tree/model | grep -q "Raspberry Pi"; then
    echo "ERROR: this installer must run on a Raspberry Pi" >&2
    exit 1
fi
if [[ ! -r /etc/os-release ]]; then
    echo "ERROR: cannot identify the operating system" >&2
    exit 1
fi
. /etc/os-release
if [[ "${ID:-}" != "raspbian" && "${ID:-}" != "debian" ]]; then
    echo "ERROR: Raspberry Pi OS is required (found ${PRETTY_NAME:-unknown})" >&2
    exit 1
fi
if [[ -z "$APP_USER" || "$APP_USER" == "root" ]] || ! id "$APP_USER" >/dev/null 2>&1; then
    echo "ERROR: use --user NAME to specify the existing desktop user" >&2
    exit 1
fi
APP_HOME="$(getent passwd "$APP_USER" | cut -d: -f6)"
if [[ -z "$APP_HOME" || ! -d "$APP_HOME" ]]; then
    echo "ERROR: home directory not found for $APP_USER" >&2
    exit 1
fi
APP_DIR="$APP_HOME/Runtime"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
if [[ ! -f "$SOURCE_DIR/poc.py" || ! -f "$SOURCE_DIR/version_check.py" ]]; then
    echo "ERROR: installer is not inside a complete POC source tree" >&2
    exit 1
fi

echo "Installing POC for user $APP_USER from $SOURCE_DIR"
apt-get update
if ((DO_FULL_UPGRADE)); then
    DEBIAN_FRONTEND=noninteractive apt-get full-upgrade -y
fi

PACKAGES=(
    kanshi
    plymouth
    plymouth-themes
    python3
    python3-numpy
    python3-pyqt5
    python3-pyqtgraph
    python3-serial
    python3-pytest
    raspi-config
)
if apt-cache show python3-rpi-lgpio >/dev/null 2>&1; then
    PACKAGES+=(python3-rpi-lgpio)
elif apt-cache show python3-rpi.gpio >/dev/null 2>&1; then
    PACKAGES+=(python3-rpi.gpio)
else
    echo "ERROR: no Raspberry Pi OS GPIO compatibility package is available" >&2
    exit 1
fi
DEBIAN_FRONTEND=noninteractive apt-get install -y "${PACKAGES[@]}"

install -d -o "$APP_USER" -g "$APP_USER" "$APP_DIR"
if [[ "$SOURCE_DIR" != "$APP_DIR" ]]; then
    SAVED_POC_CONFIG=""
    SAVED_NETWORK_CONFIG=""
    if [[ -f "$APP_DIR/poc_config.json" ]]; then
        SAVED_POC_CONFIG="$(mktemp)"
        cp -a "$APP_DIR/poc_config.json" "$SAVED_POC_CONFIG"
    fi
    if [[ -f "$APP_DIR/raspi/network-config.conf" ]]; then
        SAVED_NETWORK_CONFIG="$(mktemp)"
        cp -a "$APP_DIR/raspi/network-config.conf" "$SAVED_NETWORK_CONFIG"
    fi
    cp -a "$SOURCE_DIR/." "$APP_DIR/"
    if [[ -n "$SAVED_POC_CONFIG" ]]; then
        cp -a "$SAVED_POC_CONFIG" "$APP_DIR/poc_config.json"
        rm -f "$SAVED_POC_CONFIG"
    fi
    if [[ -n "$SAVED_NETWORK_CONFIG" ]]; then
        cp -a "$SAVED_NETWORK_CONFIG" "$APP_DIR/raspi/network-config.conf"
        rm -f "$SAVED_NETWORK_CONFIG"
    fi
fi
find "$APP_DIR" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$APP_DIR" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
chmod +x \
    "$APP_DIR/raspi/setup-console.sh" \
    "$APP_DIR/raspi/start_console.sh" \
    "$APP_DIR/raspi/switch-to-runtime.sh" \
    "$APP_DIR/raspi/install-display-assets.sh" \
    "$APP_DIR/raspi/install-desktop-launcher.sh" \
    "$APP_DIR/raspi/configure-network.sh"

CONFIG_FILE=""
for candidate in /boot/firmware/config.txt /boot/config.txt; do
    if [[ -f "$candidate" ]]; then
        CONFIG_FILE="$candidate"
        break
    fi
done
if [[ -z "$CONFIG_FILE" ]]; then
    echo "ERROR: Raspberry Pi boot config.txt was not found" >&2
    exit 1
fi

CONFIG_TEMP="$(mktemp "${CONFIG_FILE}.poc.XXXXXX")"
trap 'rm -f "$CONFIG_TEMP"' EXIT
awk '
    function flush_all() {
        if (pending_all) {
            print "[all]"
            pending_all = 0
        }
    }

    /^# BEGIN POC (DISPLAY|POE FAN|BOOT CONFIG)$/ {
        # Older installers placed [all] immediately before the managed block.
        pending_all = 0
        in_poc_block = 1
        next
    }
    in_poc_block {
        if ($0 ~ /^# END POC (DISPLAY|POE FAN|BOOT CONFIG)$/) {
            in_poc_block = 0
        }
        next
    }
    /^\[all\]$/ {
        # Buffer section headers so a legacy header before a POC block can be
        # discarded. Consecutive empty [all] sections collapse to one.
        pending_all = 1
        next
    }
    /^# Aggressive cooling for official Raspberry Pi PoE or PoE\+ HAT$/ {
        next
    }
    /^dtparam=poe_fan_temp[0-3]=/ {
        next
    }
    /^display_auto_detect=/ {
        next
    }
    /^dtoverlay=vc4-kms-dsi-ili9881-7inch(,.*)?$/ {
        next
    }
    /^dtoverlay=vc4-kms-dsi-waveshare-panel,10_1_inch(,.*)?$/ {
        next
    }
    {
        flush_all()
        print
    }
' "$CONFIG_FILE" >"$CONFIG_TEMP"
cp "$CONFIG_TEMP" "$CONFIG_FILE"

{
    echo "# BEGIN POC BOOT CONFIG"
    echo "[all]"
    case "$DISPLAY_MODE" in
        auto)
            echo "display_auto_detect=1"
            ;;
        touch2-7)
            echo "dtoverlay=vc4-kms-dsi-ili9881-7inch"
            ;;
        waveshare-10.1)
            echo "display_auto_detect=1"
            echo "dtoverlay=vc4-kms-dsi-waveshare-panel,10_1_inch"
            ;;
    esac
    echo "# Aggressive cooling for official Raspberry Pi PoE or PoE+ HAT"
    echo "dtparam=poe_fan_temp0=30000,poe_fan_temp0_hyst=2000"
    echo "dtparam=poe_fan_temp1=35000,poe_fan_temp1_hyst=2000"
    echo "dtparam=poe_fan_temp2=40000,poe_fan_temp2_hyst=2000"
    echo "dtparam=poe_fan_temp3=45000,poe_fan_temp3_hyst=2000"
    echo "# END POC BOOT CONFIG"
} >>"$CONFIG_FILE"
rm -f "$CONFIG_TEMP"
trap - EXIT

AUTOSTART_DIR="$APP_HOME/.config/autostart"
install -d -o "$APP_USER" -g "$APP_USER" "$AUTOSTART_DIR"
cat >"$AUTOSTART_DIR/poc.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Platform Operators Console
Comment=Start POC in appliance mode
Exec=$APP_DIR/raspi/start_console.sh
Terminal=false
X-GNOME-Autostart-enabled=true
EOF
chown "$APP_USER:$APP_USER" "$AUTOSTART_DIR/poc.desktop"

DESKTOP_LAUNCHER="$("$APP_DIR/raspi/install-desktop-launcher.sh" --user "$APP_USER")"

KANSHI_DIR="$APP_HOME/.config/kanshi"
KANSHI_CONFIG="$KANSHI_DIR/config"
install -d -o "$APP_USER" -g "$APP_USER" "$KANSHI_DIR"
KANSHI_TEMP="$(mktemp)"
trap 'rm -f "$KANSHI_TEMP"' EXIT
if [[ "$DISPLAY_MODE" == "touch2-7" ]]; then
    if [[ -f "$KANSHI_CONFIG" && ! -f "$KANSHI_CONFIG.before-poc" ]]; then
        cp -a "$KANSHI_CONFIG" "$KANSHI_CONFIG.before-poc"
        chown "$APP_USER:$APP_USER" "$KANSHI_CONFIG.before-poc"
    fi
    cat >"$KANSHI_TEMP" <<'EOF'
# BEGIN POC TOUCH2 ROTATION
profile poc-touch2 {
    output DSI-1 enable transform 90
}
# END POC TOUCH2 ROTATION
EOF
elif [[ -f "$KANSHI_CONFIG" ]]; then
    awk '
        /^# BEGIN POC TOUCH2 ROTATION$/ { in_poc_block = 1; next }
        /^# END POC TOUCH2 ROTATION$/ { in_poc_block = 0; next }
        !in_poc_block { print }
    ' "$KANSHI_CONFIG" >"$KANSHI_TEMP"
else
    : >"$KANSHI_TEMP"
fi
install -o "$APP_USER" -g "$APP_USER" -m 644 "$KANSHI_TEMP" "$KANSHI_CONFIG"
rm -f "$KANSHI_TEMP"
trap - EXIT

usermod -aG gpio,video,input,render,dialout "$APP_USER"
raspi-config nonint do_boot_behaviour B4

if [[ "$DISPLAY_MODE" == "touch2-7" ]]; then
    echo "Installing Touch Display 2 splash and wallpaper..."
    "$APP_DIR/raspi/install-display-assets.sh" --user "$APP_USER"
fi

NETWORK_DEFERRED=0
if is_ssh_session; then
    NETWORK_DEFERRED=1
    echo "SSH session detected; preparing POC network profiles without interrupting the connection..."
    "$APP_DIR/raspi/configure-network.sh" --defer-activation
else
    echo "Configuring POC Ethernet roles..."
    "$APP_DIR/raspi/configure-network.sh"
fi

echo "Validating installed POC tree..."
runuser -u "$APP_USER" -- python3 "$APP_DIR/version_check.py"
runuser -u "$APP_USER" -- python3 -m pytest -q "$APP_DIR/tests/test_physical_controls.py"
runuser -u "$APP_USER" -- python3 - <<'PY'
import RPi.GPIO
from PyQt5 import QtCore, QtWidgets
import numpy
import pyqtgraph
import serial
print("POC Python imports OK")
PY

echo
echo "POC installation completed."
echo "Display mode: $DISPLAY_MODE"
echo "Application:  $APP_DIR"
echo "Launcher:     $DESKTOP_LAUNCHER"
echo "Logs:         $APP_HOME/.local/state/poc/poc.log"
if [[ "$DISPLAY_MODE" == "touch2-7" ]]; then
    echo "Touch 2:      desktop transform 90; Plymouth DeviceScale 1"
fi
if ((NETWORK_DEFERRED)); then
    echo "Network:      profiles prepared; current connections remain active until reboot"
fi
echo "Reboot when ready: sudo reboot"
