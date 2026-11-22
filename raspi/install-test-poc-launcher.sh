#!/bin/bash
# install-test-poc-launcher.sh  ver 4
set -euo pipefail

APP_USER="${SUDO_USER:-}"
PRIV_HELPER="/usr/local/libexec/poc-test-network-helper"
SUDOERS_FILE="/etc/sudoers.d/poc-test-poc-network"

usage() {
    cat <<'USAGE'
Usage: sudo ./raspi/install-test-poc-launcher.sh [--user NAME]

Create or refresh the opt-in "Test Poc" desktop launcher and install the
root-owned fixed-function network helper needed for password-free Test Poc use.
The launcher uses a temporary NetworkManager profile and does not alter the
saved eth0 connection.
USAGE
}

while (($#)); do
    case "$1" in
        --user)
            [[ $# -ge 2 ]] || { echo "ERROR: --user requires a name" >&2; exit 2; }
            APP_USER="$2"
            shift 2
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
    echo "ERROR: run this script with sudo" >&2
    exit 1
fi
if [[ -z "$APP_USER" || "$APP_USER" == "root" ]] || ! id "$APP_USER" >/dev/null 2>&1; then
    echo "ERROR: use --user NAME to specify the existing desktop user" >&2
    exit 1
fi
if [[ ! "$APP_USER" =~ ^[a-z_][a-z0-9_-]*$ ]]; then
    echo "ERROR: unsupported desktop user name: $APP_USER" >&2
    exit 1
fi

APP_HOME="$(getent passwd "$APP_USER" | cut -d: -f6)"
APP_DIR="$APP_HOME/Runtime"
START_TEST="$APP_DIR/raspi/start-test-poc.sh"
START_CONSOLE="$APP_DIR/raspi/start_console.sh"
HELPER_SOURCE="$APP_DIR/raspi/test-poc-network-helper.sh"
ICON_SOURCE="$APP_DIR/images/POC.png"

missing=0
for required in "$START_TEST" "$START_CONSOLE" "$HELPER_SOURCE"; do
    if [[ ! -f "$required" ]]; then
        echo "ERROR: required Test Poc file is missing: $required" >&2
        missing=1
    fi
done
if [[ $missing -ne 0 ]]; then
    echo "ERROR: Test Poc cannot be installed from incomplete runtime: $APP_DIR" >&2
    exit 1
fi

# Delta updates copied through Windows/WinSCP may lose Unix execute bits.
# The installer is already privileged, so repair the runtime launcher bits here.
chmod 755 "$START_TEST" "$START_CONSOLE" "$HELPER_SOURCE"

if [[ -f "$ICON_SOURCE" ]]; then
    DESKTOP_ICON="$ICON_SOURCE"
else
    DESKTOP_ICON="utilities-terminal"
    echo "NOTE: $ICON_SOURCE is absent; using the system terminal icon for Test Poc."
fi

# Install a root-owned copy outside the user-writable Runtime tree. Sudoers
# grants passwordless execution of this fixed-function helper only, never the
# Runtime copy and never arbitrary nmcli/arping commands.
install -d -o root -g root -m 755 "$(dirname "$PRIV_HELPER")"
install -o root -g root -m 755 "$HELPER_SOURCE" "$PRIV_HELPER"

SUDOERS_TMP="$(mktemp)"
cleanup_tmp() {
    rm -f "$SUDOERS_TMP"
}
trap cleanup_tmp EXIT
cat >"$SUDOERS_TMP" <<EOF2
# Managed by POC install-test-poc-launcher.sh
$APP_USER ALL=(root) NOPASSWD: $PRIV_HELPER
EOF2
chmod 440 "$SUDOERS_TMP"
if ! visudo -cf "$SUDOERS_TMP" >/dev/null; then
    echo "ERROR: generated Test Poc sudoers rule failed validation" >&2
    exit 1
fi
install -o root -g root -m 440 "$SUDOERS_TMP" "$SUDOERS_FILE"

DESKTOP_DIR=""
if command -v xdg-user-dir >/dev/null 2>&1; then
    DESKTOP_DIR="$(runuser -u "$APP_USER" -- xdg-user-dir DESKTOP 2>/dev/null || true)"
fi
if [[ "$DESKTOP_DIR" != /* ]]; then
    DESKTOP_DIR="$APP_HOME/Desktop"
fi
case "$DESKTOP_DIR" in
    "$APP_HOME"|"$APP_HOME"/*) ;;
    *)
        echo "WARNING: ignoring Desktop directory outside $APP_HOME: $DESKTOP_DIR" >&2
        DESKTOP_DIR="$APP_HOME/Desktop"
        ;;
esac

install -d -o "$APP_USER" -g "$APP_USER" "$DESKTOP_DIR"
DESKTOP_LAUNCHER="$DESKTOP_DIR/Test-Poc.desktop"
cat >"$DESKTOP_LAUNCHER" <<EOF2
[Desktop Entry]
Version=1.0
Type=Application
Name=Test Poc
Comment=Temporarily apply the POC eth0 address and start POC
Exec=$APP_DIR/raspi/start-test-poc.sh
Path=$APP_DIR
Icon=$DESKTOP_ICON
Terminal=true
StartupNotify=false
EOF2
chown "$APP_USER:$APP_USER" "$DESKTOP_LAUNCHER"
chmod 755 "$DESKTOP_LAUNCHER"

if command -v zenity >/dev/null 2>&1; then
    echo "Zenity detected: Test Poc will use a touch/mouse Continue/Cancel dialog."
else
    echo "NOTE: zenity is not installed; Test Poc will use the terminal y/N prompt."
    echo "No package installation or internet access is attempted by this installer."
fi

echo "Installed password-free Test Poc network helper for user: $APP_USER"
echo "  Helper : $PRIV_HELPER"
echo "  Sudoers: $SUDOERS_FILE"
echo "  Desktop: $DESKTOP_LAUNCHER"
