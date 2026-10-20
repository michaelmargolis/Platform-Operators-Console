#!/bin/bash
# install-display-assets.sh  ver 4
set -euo pipefail

APP_USER="${SUDO_USER:-poc}"

usage() {
    cat <<'EOF'
Usage: sudo ./raspi/install-display-assets.sh [--user NAME]

Install the POC Touch Display 2 Plymouth splash and desktop wallpaper.
Run this from the live /home/poc/Runtime tree while logged into the desktop.
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
if [[ "$APP_USER" == "root" ]] || ! id "$APP_USER" >/dev/null 2>&1; then
    echo "ERROR: desktop user '$APP_USER' does not exist" >&2
    exit 1
fi

APP_HOME="$(getent passwd "$APP_USER" | cut -d: -f6)"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
SPLASH_PORTRAIT_SOURCE="$APP_DIR/raspi/assets/splash/poc_splash_touch2_boot.png"
WALLPAPER_SOURCE="$APP_DIR/raspi/assets/wallpaper/poc_wallpaper_1280x720.png"

for required_file in \
    "$SPLASH_PORTRAIT_SOURCE" \
    "$WALLPAPER_SOURCE"; do
    if [[ ! -f "$required_file" ]]; then
        echo "ERROR: required image not found: $required_file" >&2
        exit 1
    fi
done

check_png_dimensions() {
    python3 - "$1" "$2" "$3" <<'PY'
import struct
import sys

path, expected_width, expected_height = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
with open(path, "rb") as image:
    header = image.read(24)
if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
    raise SystemExit(f"ERROR: not a valid PNG file: {path}")
width, height = struct.unpack(">II", header[16:24])
if (width, height) != (expected_width, expected_height):
    raise SystemExit(
        f"ERROR: {path} is {width}x{height}; expected {expected_width}x{expected_height}"
    )
PY
}

check_png_dimensions "$SPLASH_PORTRAIT_SOURCE" 720 1280
check_png_dimensions "$WALLPAPER_SOURCE" 1280 720

if ! command -v plymouth-set-default-theme >/dev/null 2>&1; then
    echo "ERROR: plymouth-set-default-theme is not installed" >&2
    exit 1
fi
if ! command -v update-initramfs >/dev/null 2>&1; then
    echo "ERROR: update-initramfs is not installed" >&2
    exit 1
fi

THEME_NAME="poc"
THEME_DIR="/usr/share/plymouth/themes/$THEME_NAME"
STATE_DIR="/var/lib/poc-display-assets"
install -d -m 755 "$THEME_DIR" "$STATE_DIR"

if [[ ! -f "$STATE_DIR/previous-plymouth-theme" ]]; then
    plymouth-set-default-theme >"$STATE_DIR/previous-plymouth-theme" 2>/dev/null || true
fi

install -m 644 "$SPLASH_PORTRAIT_SOURCE" "$THEME_DIR/splash.png"

cat >"$THEME_DIR/poc.plymouth" <<'EOF'
[Plymouth Theme]
Name=POC Touch Display 2
Description=Platform Operators Console Touch Display 2 boot splash
ModuleName=script

[script]
ImageDir=/usr/share/plymouth/themes/poc
ScriptFile=/usr/share/plymouth/themes/poc/poc.script
EOF

cat >"$THEME_DIR/poc.script" <<'EOF'
Window.SetBackgroundTopColor(0.0, 0.0, 0.0);
Window.SetBackgroundBottomColor(0.0, 0.0, 0.0);

splash_image = Image("splash.png");
splash_sprite = Sprite(splash_image);
splash_sprite.SetX(0);
splash_sprite.SetY(0);
splash_sprite.SetZ(10);
EOF

chmod 644 "$THEME_DIR/poc.plymouth" "$THEME_DIR/poc.script" "$THEME_DIR/splash.png"

PLYMOUTH_CONFIG="/etc/plymouth/plymouthd.conf"
if [[ -f "$PLYMOUTH_CONFIG" && ! -f "$STATE_DIR/previous-plymouthd.conf" ]]; then
    cp -a "$PLYMOUTH_CONFIG" "$STATE_DIR/previous-plymouthd.conf"
fi
cat >"$PLYMOUTH_CONFIG" <<'EOF'
# Administrator customizations managed by the POC display-asset installer
[Daemon]
Theme=poc
ShowDelay=0
DeviceScale=1
EOF

echo "Selecting the POC Plymouth theme..."
plymouth-set-default-theme -R "$THEME_NAME"

if [[ "$(plymouth-set-default-theme)" != "$THEME_NAME" ]]; then
    echo "ERROR: Plymouth did not retain the POC theme selection" >&2
    exit 1
fi
check_png_dimensions "$THEME_DIR/splash.png" 720 1280

if ! command -v pcmanfm >/dev/null 2>&1; then
    echo "WARNING: pcmanfm is unavailable; boot splash installed but wallpaper was not changed." >&2
else
    USER_ID="$(id -u "$APP_USER")"
    USER_RUNTIME_DIR="/run/user/$USER_ID"
    WAYLAND_SOCKET="wayland-0"
    DBUS_ADDRESS="unix:path=$USER_RUNTIME_DIR/bus"

    if [[ -S "$USER_RUNTIME_DIR/$WAYLAND_SOCKET" ]]; then
        echo "Applying the POC desktop wallpaper..."
        runuser -u "$APP_USER" -- env \
            XDG_RUNTIME_DIR="$USER_RUNTIME_DIR" \
            WAYLAND_DISPLAY="$WAYLAND_SOCKET" \
            DBUS_SESSION_BUS_ADDRESS="$DBUS_ADDRESS" \
            pcmanfm --set-wallpaper="$WALLPAPER_SOURCE" --wallpaper-mode=fit
    else
        echo "WARNING: the $APP_USER Wayland desktop is not active; wallpaper was not changed." >&2
        echo "Run this script again from a terminal on the Pi desktop." >&2
    fi
fi

echo
echo "POC display assets installed. Reboot to test the boot splash."
echo "Plymouth DeviceScale is fixed at 1 for the native 720 x 1280 boot canvas."
echo "The script did not change display rotation or kernel command-line settings."
