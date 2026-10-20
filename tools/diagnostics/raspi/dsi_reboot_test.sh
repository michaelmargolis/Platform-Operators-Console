#!/usr/bin/env bash
# dsi_reboot_test.sh ver 1
# Standalone Raspberry Pi Touch Display 2 warm-reboot reliability test.

set -u

readonly TEST_NAME="poc-dsi-reboot-test"
readonly INSTALLED_SCRIPT="/usr/local/sbin/${TEST_NAME}"
readonly UNIT_NAME="${TEST_NAME}.service"
readonly UNIT_FILE="/etc/systemd/system/${UNIT_NAME}"
readonly STATE_DIR="/var/lib/${TEST_NAME}"
readonly COUNT_FILE="${STATE_DIR}/completed_reboots"
readonly LOG_FILE="/var/log/${TEST_NAME}.log"
readonly TARGET_REBOOTS=30
readonly CHECK_DELAY_SECONDS=75

log_line() {
    printf '%s\n' "$*" >>"${LOG_FILE}"
}

require_root() {
    if [[ "${EUID}" -ne 0 ]]; then
        echo "Run with sudo: sudo bash $0 --install" >&2
        exit 1
    fi
}

install_test() {
    require_root

    install -D -m 0755 "$0" "${INSTALLED_SCRIPT}"
    install -d -m 0755 "${STATE_DIR}"
    printf '0\n' >"${COUNT_FILE}"
    : >"${LOG_FILE}"
    chmod 0644 "${LOG_FILE}"

    tee "${UNIT_FILE}" >/dev/null <<EOF
[Unit]
Description=POC Touch Display 2 reboot reliability test
After=multi-user.target
StartLimitIntervalSec=0

[Service]
Type=oneshot
ExecStart=${INSTALLED_SCRIPT} --boot-check
TimeoutStartSec=infinity

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable "${UNIT_NAME}" >/dev/null

    log_line "POC Touch Display 2 reboot reliability test"
    log_line "Started: $(date -Is)"
    log_line "Target warm reboots: ${TARGET_REBOOTS}"
    log_line "Check delay after each boot: ${CHECK_DELAY_SECONDS} seconds"
    log_line "Kernel: $(uname -a)"
    log_line "Relevant boot configuration:"
    grep -nEi 'display_auto_detect|dtoverlay=.*(vc4|dsi|ili9881)|max_framebuffers' \
        /boot/firmware/config.txt >>"${LOG_FILE}" 2>&1 || true
    log_line "================================================================"

    echo "Installed and enabled ${UNIT_NAME}."
    echo "The Pi will perform exactly ${TARGET_REBOOTS} warm reboots."
    echo "Results: ${LOG_FILE}"
    echo "Cancel later with: sudo ${INSTALLED_SCRIPT} --stop"
    echo "Keep the platform deactivated and clear of personnel."
    echo "First reboot in 10 seconds; press Ctrl-C now to cancel."
    sleep 10
    sync
    systemctl reboot
}

read_count() {
    local value="0"
    if [[ -r "${COUNT_FILE}" ]]; then
        read -r value <"${COUNT_FILE}" || value="0"
    fi
    if [[ ! "${value}" =~ ^[0-9]+$ ]]; then
        value="0"
    fi
    printf '%s' "${value}"
}

boot_check() {
    require_root
    install -d -m 0755 "${STATE_DIR}"
    touch "${LOG_FILE}"

    sleep "${CHECK_DELAY_SECONDS}"

    local previous_count current_count boot_id throttled
    local kms_output dsi_ok backlight_ok driver_ok overall
    local -a backlights mipi_devices
    local device driver_path

    previous_count="$(read_count)"
    current_count=$((previous_count + 1))
    printf '%s\n' "${current_count}" >"${COUNT_FILE}"

    boot_id="$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || echo unavailable)"
    throttled="$(vcgencmd get_throttled 2>&1 || true)"
    if command -v kmsprint >/dev/null 2>&1; then
        kms_output="$(kmsprint -l 2>&1 || true)"
    else
        kms_output="kmsprint command not found"
    fi

    dsi_ok=0
    if grep -Eq 'DSI-[0-9]+.*\(connected\)' <<<"${kms_output}"; then
        dsi_ok=1
    fi

    shopt -s nullglob
    backlights=(/sys/class/backlight/*)
    mipi_devices=(/sys/bus/mipi-dsi/devices/*)
    shopt -u nullglob

    backlight_ok=0
    if ((${#backlights[@]} > 0)); then
        backlight_ok=1
    fi

    driver_ok=0
    for device in "${mipi_devices[@]}"; do
        driver_path="$(readlink -f "${device}/driver" 2>/dev/null || true)"
        if [[ "${driver_path}" == *ili9881* ]]; then
            driver_ok=1
        fi
    done

    overall="PASS"
    if ((dsi_ok == 0 || backlight_ok == 0 || driver_ok == 0)); then
        overall="FAIL"
    fi

    log_line "Reboot ${current_count}/${TARGET_REBOOTS}: ${overall}"
    log_line "Time: $(date -Is)"
    log_line "Boot ID: ${boot_id}"
    log_line "Under-voltage/throttling: ${throttled}"
    log_line "DSI connected: ${dsi_ok}"
    log_line "Backlight present: ${backlight_ok}"
    log_line "ILI9881 driver bound: ${driver_ok}"
    log_line "--- kmsprint -l ---"
    printf '%s\n' "${kms_output}" >>"${LOG_FILE}"
    log_line "--- backlight devices ---"
    if ((${#backlights[@]} > 0)); then
        printf '%s\n' "${backlights[@]}" >>"${LOG_FILE}"
    else
        log_line "none"
    fi
    log_line "--- MIPI DSI devices and bound drivers ---"
    if ((${#mipi_devices[@]} > 0)); then
        for device in "${mipi_devices[@]}"; do
            driver_path="$(readlink -f "${device}/driver" 2>/dev/null || true)"
            printf '%s -> %s\n' "${device}" "${driver_path:-unbound}" >>"${LOG_FILE}"
        done
    else
        log_line "none"
    fi
    log_line "--- relevant kernel messages ---"
    dmesg 2>&1 | grep -iE \
        'dsi|drm|panel|backlight|ili9881|goodix|under.?voltage' \
        >>"${LOG_FILE}" || log_line "none"
    log_line "================================================================"
    sync

    if ((current_count >= TARGET_REBOOTS)); then
        log_line "Completed ${TARGET_REBOOTS} warm reboots at $(date -Is)."
        log_line "Automatic rebooting is now disabled."
        systemctl disable "${UNIT_NAME}" >/dev/null 2>&1 || true
        sync
        exit 0
    fi

    sleep 5
    systemctl reboot
}

stop_test() {
    require_root
    systemctl disable --now "${UNIT_NAME}" >/dev/null 2>&1 || true
    log_line "Test stopped manually at $(date -Is), after $(read_count) completed reboot checks."
    sync
    echo "Test stopped. Existing results remain in ${LOG_FILE}."
}

show_status() {
    require_root
    echo "Completed reboot checks: $(read_count)/${TARGET_REBOOTS}"
    echo "Log: ${LOG_FILE}"
    systemctl is-enabled "${UNIT_NAME}" 2>/dev/null || true
    tail -n 25 "${LOG_FILE}" 2>/dev/null || true
}

case "${1:---install}" in
    --install)
        install_test
        ;;
    --boot-check)
        boot_check
        ;;
    --stop)
        stop_test
        ;;
    --status)
        show_status
        ;;
    *)
        echo "Usage: $0 [--install|--status|--stop]" >&2
        exit 2
        ;;
esac
