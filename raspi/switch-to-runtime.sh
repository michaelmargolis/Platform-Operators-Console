#!/bin/bash
# switch-to-runtime.sh  ver 1
set -euo pipefail

if [[ $EUID -eq 0 ]]; then
    echo "ERROR: run this as the desktop user, without sudo" >&2
    exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
if [[ ! -f "$APP_DIR/poc.py" || ! -f "$APP_DIR/version_check.py" ]]; then
    echo "ERROR: this script is not inside a complete POC runtime tree" >&2
    exit 1
fi

chmod u+x "$SCRIPT_DIR/start_console.sh" "$SCRIPT_DIR/switch-to-runtime.sh"
AUTOSTART_DIR="$HOME/.config/autostart"
mkdir -p "$AUTOSTART_DIR"
cat >"$AUTOSTART_DIR/poc.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Platform Operators Console
Comment=Start POC in appliance mode
Exec=$SCRIPT_DIR/start_console.sh
Terminal=false
X-GNOME-Autostart-enabled=true
EOF
chmod u+rw,go+r "$AUTOSTART_DIR/poc.desktop"

echo "POC autostart now uses: $APP_DIR"
echo "Validate with: cd '$APP_DIR' && python3 version_check.py"
