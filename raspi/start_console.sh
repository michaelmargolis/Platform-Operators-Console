#!/bin/bash
# start_console.sh  ver 4
set -u

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/poc"
LOG_FILE="$STATE_DIR/poc.log"
MAX_LOG_BYTES=$((10 * 1024 * 1024))

mkdir -p "$STATE_DIR"
cd "$APP_DIR" || exit 1

if [[ -f "$LOG_FILE" ]] && (( $(stat -c %s "$LOG_FILE" 2>/dev/null || echo 0) > MAX_LOG_BYTES )); then
    mv -f "$LOG_FILE" "$LOG_FILE.1"
fi

if ! python3 "$SCRIPT_DIR/network_check.py" --gui >>"$LOG_FILE" 2>&1; then
    printf '%s POC not started: network adapters need configuring\n' \
        "$(date --iso-8601=seconds)" >>"$LOG_FILE"
    exit 1
fi

while true; do
    printf '\n%s Starting POC\n' "$(date --iso-8601=seconds)" >>"$LOG_FILE"
    python3 "$APP_DIR/poc.py" --fullscreen >>"$LOG_FILE" 2>&1
    status=$?
    printf '%s POC exited with status %d\n' "$(date --iso-8601=seconds)" "$status" >>"$LOG_FILE"

    # Escape closes POC normally. Do not immediately relaunch it.
    if [[ $status -eq 0 ]]; then
        exit 0
    fi
    sleep 3
done
