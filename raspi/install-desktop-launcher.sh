#!/bin/bash
# install-desktop-launcher.sh  ver 1
set -euo pipefail

APP_USER="${SUDO_USER:-}"

usage() {
    cat <<'EOF'
Usage: sudo ./raspi/install-desktop-launcher.sh [--user NAME]

Create or refresh the Platform Operators Console launcher in the desktop
user's configured Desktop directory.
EOF
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

APP_HOME="$(getent passwd "$APP_USER" | cut -d: -f6)"
APP_DIR="$APP_HOME/Runtime"
if [[ ! -x "$APP_DIR/raspi/start_console.sh" || ! -f "$APP_DIR/images/POC.png" ]]; then
    echo "ERROR: complete POC runtime not found at $APP_DIR" >&2
    exit 1
fi

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
DESKTOP_LAUNCHER="$DESKTOP_DIR/POC.desktop"
cat >"$DESKTOP_LAUNCHER" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Platform Operators Console
Comment=Start POC in appliance mode
Exec=$APP_DIR/raspi/start_console.sh
Path=$APP_DIR
Icon=$APP_DIR/images/POC.png
Terminal=false
StartupNotify=false
EOF
chown "$APP_USER:$APP_USER" "$DESKTOP_LAUNCHER"
chmod 755 "$DESKTOP_LAUNCHER"

echo "$DESKTOP_LAUNCHER"
