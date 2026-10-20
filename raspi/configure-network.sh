#!/bin/bash
# configure-network.sh  ver 5
set -euo pipefail

PLATFORM_PROFILE="POC Platform Static"
DEVELOPMENT_PROFILE="POC Development DHCP"
WIFI_DISABLE_SERVICE="poc-disable-wifi.service"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/network-config.conf"
FORCE=0
DEFER_ACTIVATION=0

is_ssh_session() {
    if [[ -n "${SSH_CONNECTION:-}" || -n "${SSH_CLIENT:-}" || -n "${SSH_TTY:-}" ]]; then
        return 0
    fi

    # sudo commonly removes SSH_* variables. Process ancestry remains
    # available and prevents an accidental network cut-off in that case.
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
Usage: sudo ./raspi/configure-network.sh [--force] [--defer-activation]

  --force               allow immediate activation while running over SSH
  --defer-activation    write profiles now; activate them on the next boot
EOF
}

while (($#)); do
    case "$1" in
        --force)
            FORCE=1
            ;;
        --defer-activation)
            DEFER_ACTIVATION=1
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
    shift
done

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: run this script with sudo" >&2
    exit 1
fi
if is_ssh_session && ((FORCE == 0 && DEFER_ACTIVATION == 0)); then
    echo "ERROR: activating the profiles interrupts both Ethernet links." >&2
    echo "Use --defer-activation, run locally, or use --force if interruption is acceptable." >&2
    exit 1
fi
if ! command -v nmcli >/dev/null 2>&1; then
    echo "ERROR: NetworkManager nmcli is not installed" >&2
    exit 1
fi
if [[ ! -r "$CONFIG_FILE" ]]; then
    echo "ERROR: cannot read $CONFIG_FILE" >&2
    exit 1
fi

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

confirm_default_yes() {
    local prompt="$1"
    local reply
    if [[ ! -t 0 ]]; then
        echo "ERROR: confirmation required but standard input is not a terminal" >&2
        exit 1
    fi
    read -r -p "$prompt" reply
    case "${reply,,}" in
        ""|y|yes) return 0 ;;
        n|no) return 1 ;;
        *)
            echo "Please answer y or n." >&2
            confirm_default_yes "$prompt"
            ;;
    esac
}

update_platform_mac() {
    local new_mac="$1"
    local config_temp owner_id group_id file_mode
    config_temp="$(mktemp "${CONFIG_FILE}.poc.XXXXXX")"
    owner_id="$(stat -c %u "$CONFIG_FILE")"
    group_id="$(stat -c %g "$CONFIG_FILE")"
    file_mode="$(stat -c %a "$CONFIG_FILE")"
    awk -v mac="$new_mac" '
        /^PLATFORM_MAC[[:space:]]*=/ {
            print "PLATFORM_MAC=" mac
            updated = 1
            next
        }
        { print }
        END {
            if (!updated) {
                print "PLATFORM_MAC=" mac
            }
        }
    ' "$CONFIG_FILE" >"$config_temp"
    install -o "$owner_id" -g "$group_id" -m "$file_mode" \
        "$config_temp" "$CONFIG_FILE"
    rm -f "$config_temp"
}

install_wifi_disable_service() {
    local service_path="/etc/systemd/system/$WIFI_DISABLE_SERVICE"
    cat >"$service_path" <<'EOF'
[Unit]
Description=Disable Wi-Fi for POC appliance operation
After=NetworkManager.service
Wants=NetworkManager.service

[Service]
Type=oneshot
ExecStart=/usr/bin/nmcli radio wifi off
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
    chmod 644 "$service_path"
    systemctl daemon-reload
    systemctl enable "$WIFI_DISABLE_SERVICE" >/dev/null
}

remove_wifi_disable_service() {
    local service_path="/etc/systemd/system/$WIFI_DISABLE_SERVICE"
    if systemctl is-enabled --quiet "$WIFI_DISABLE_SERVICE" 2>/dev/null; then
        systemctl disable "$WIFI_DISABLE_SERVICE" >/dev/null
    fi
    if [[ -f "$service_path" ]]; then
        rm -f "$service_path"
        systemctl daemon-reload
    fi
}

PLATFORM_INTERFACE="$(read_setting PLATFORM_INTERFACE)"
PLATFORM_MAC="$(read_setting PLATFORM_MAC)"
PLATFORM_ADDRESS="$(read_setting PLATFORM_ADDRESS)"
DEVELOPMENT_INTERFACE="$(read_setting DEVELOPMENT_INTERFACE)"

if [[ ! "$PLATFORM_MAC" =~ ^([[:xdigit:]]{2}:){5}[[:xdigit:]]{2}$ ]]; then
    echo "ERROR: invalid platform adapter MAC address: $PLATFORM_MAC" >&2
    exit 1
fi
if [[ "$PLATFORM_INTERFACE" == "$DEVELOPMENT_INTERFACE" ]]; then
    echo "ERROR: platform and development interfaces must be different" >&2
    exit 1
fi
for interface in "$PLATFORM_INTERFACE" "$DEVELOPMENT_INTERFACE"; do
    if [[ ! "$interface" =~ ^[a-zA-Z0-9_.:-]+$ ]]; then
        echo "ERROR: invalid network interface name: $interface" >&2
        exit 1
    fi
done
python3 - "$PLATFORM_ADDRESS" <<'PY'
import ipaddress
import sys

try:
    interface = ipaddress.ip_interface(sys.argv[1])
except ValueError as exc:
    raise SystemExit(f"ERROR: invalid PLATFORM_ADDRESS: {exc}")
if interface.version != 4:
    raise SystemExit("ERROR: PLATFORM_ADDRESS must be IPv4")
PY

PLATFORM_ADDRESS_FILE="/sys/class/net/$PLATFORM_INTERFACE/address"
if [[ ! -r "$PLATFORM_ADDRESS_FILE" ]]; then
    echo "ERROR: configured platform interface $PLATFORM_INTERFACE was not found" >&2
    echo "NETWORK ADAPTERS NEED CONFIGURING: edit $CONFIG_FILE" >&2
    exit 1
fi
PLATFORM_TYPE="$(nmcli -g GENERAL.TYPE device show "$PLATFORM_INTERFACE")"
if [[ "$PLATFORM_TYPE" != "ethernet" ]]; then
    echo "ERROR: $PLATFORM_INTERFACE is $PLATFORM_TYPE, not Ethernet" >&2
    echo "NETWORK ADAPTERS NEED CONFIGURING: edit $CONFIG_FILE" >&2
    exit 1
fi
ACTUAL_PLATFORM_MAC="$(tr '[:upper:]' '[:lower:]' <"$PLATFORM_ADDRESS_FILE")"
if [[ "$ACTUAL_PLATFORM_MAC" != "${PLATFORM_MAC,,}" ]]; then
    if confirm_default_yes \
        "$PLATFORM_INTERFACE MAC is ($ACTUAL_PLATFORM_MAC), use this as the built-in platform adapter? [Y/n] "; then
        update_platform_mac "$ACTUAL_PLATFORM_MAC"
        PLATFORM_MAC="$ACTUAL_PLATFORM_MAC"
        echo "Updated PLATFORM_MAC in $CONFIG_FILE"
    else
        echo "ERROR: platform adapter was not confirmed; network configuration unchanged" >&2
        exit 1
    fi
fi
if [[ -d "/sys/class/net/$DEVELOPMENT_INTERFACE" ]]; then
    DEVELOPMENT_TYPE="$(nmcli -g GENERAL.TYPE device show "$DEVELOPMENT_INTERFACE")"
    if [[ "$DEVELOPMENT_TYPE" != "ethernet" ]]; then
        echo "ERROR: $DEVELOPMENT_INTERFACE is $DEVELOPMENT_TYPE, not Ethernet" >&2
        echo "NETWORK ADAPTERS NEED CONFIGURING: edit $CONFIG_FILE" >&2
        exit 1
    fi
fi

WIFI_STATE="$(nmcli -t -f WIFI general 2>/dev/null || true)"
if [[ "$WIFI_STATE" == "enabled" ]]; then
    if confirm_default_yes "Wi-Fi is enabled. Disable Wi-Fi for POC appliance operation? [Y/n] "; then
        install_wifi_disable_service
        if ((DEFER_ACTIVATION)); then
            echo "Wi-Fi remains enabled for this session and will be disabled on the next boot."
        else
            nmcli radio wifi off
            echo "Wi-Fi disabled."
        fi
    else
        remove_wifi_disable_service
        echo "Wi-Fi left enabled."
    fi
fi

connection_exists() {
    nmcli -t -f NAME connection show | grep -Fxq "$1"
}

ensure_connection() {
    local profile="$1"
    local interface="$2"
    if ! connection_exists "$profile"; then
        nmcli connection add type ethernet con-name "$profile" ifname "$interface"
    fi
    nmcli connection modify "$profile" \
        connection.interface-name "$interface" \
        connection.autoconnect yes
}

ensure_connection "$PLATFORM_PROFILE" "$PLATFORM_INTERFACE"
ensure_connection "$DEVELOPMENT_PROFILE" "$DEVELOPMENT_INTERFACE"

nmcli connection modify "$PLATFORM_PROFILE" \
    802-3-ethernet.mac-address "$PLATFORM_MAC" \
    connection.autoconnect-priority 100 \
    ipv4.method manual \
    ipv4.addresses "$PLATFORM_ADDRESS" \
    ipv4.gateway "" \
    ipv4.dns "" \
    ipv4.never-default yes \
    ipv6.method disabled

nmcli connection modify "$DEVELOPMENT_PROFILE" \
    802-3-ethernet.mac-address "" \
    connection.autoconnect-priority 100 \
    ipv4.method auto \
    ipv4.addresses "" \
    ipv4.gateway "" \
    ipv4.dns "" \
    ipv4.never-default no \
    ipv4.route-metric 100 \
    ipv6.method auto

# Retain old profiles for recovery, but prevent them competing at next boot.
while IFS=: read -r profile interface; do
    if [[ "$interface" == "$PLATFORM_INTERFACE" && "$profile" != "$PLATFORM_PROFILE" ]]; then
        nmcli connection modify "$profile" connection.autoconnect no
    elif [[ "$interface" == "$DEVELOPMENT_INTERFACE" && "$profile" != "$DEVELOPMENT_PROFILE" ]]; then
        nmcli connection modify "$profile" connection.autoconnect no
    fi
done < <(nmcli -t -f NAME,DEVICE connection show --active)

if ((DEFER_ACTIVATION)); then
    echo
    echo "POC network profiles prepared; current connections were not interrupted."
    echo "The new Ethernet configuration will activate on the next boot."
    if [[ ! -d "/sys/class/net/$DEVELOPMENT_INTERFACE" ]]; then
        echo "WARNING: $DEVELOPMENT_INTERFACE (USB Ethernet) is not connected."
        echo "Connect the USB Ethernet adapter before rebooting if Wi-Fi is scheduled to be disabled."
    fi
    echo "When ready, reboot manually with: sudo reboot"
else
    if [[ -d "/sys/class/net/$DEVELOPMENT_INTERFACE" ]]; then
        nmcli connection up "$DEVELOPMENT_PROFILE" ifname "$DEVELOPMENT_INTERFACE"
    else
        echo "$DEVELOPMENT_INTERFACE is absent; DHCP profile retained for any USB Ethernet adapter."
    fi
    nmcli connection up "$PLATFORM_PROFILE" ifname "$PLATFORM_INTERFACE"

    echo
    echo "POC network profiles applied."
fi
nmcli -f DEVICE,TYPE,STATE,CONNECTION device
ip -br address
ip route
