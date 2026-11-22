#!/bin/bash
# test-poc-network-helper.sh  ver 1
set -euo pipefail

TEST_PROFILE="POC Test Temporary"
HELPER_API="1"

fail() {
    echo "ERROR: $*" >&2
    exit 2
}

require_root() {
    [[ $EUID -eq 0 ]] || fail "this helper must run as root"
}

validate_interface() {
    local interface="$1"
    [[ "$interface" =~ ^[a-zA-Z0-9_.:-]+$ ]] || fail "invalid interface: $interface"
    [[ -d "/sys/class/net/$interface" ]] || fail "interface not found: $interface"
    [[ "$(nmcli -g GENERAL.TYPE device show "$interface" 2>/dev/null || true)" == "ethernet" ]] \
        || fail "$interface is not an Ethernet interface"
}

validate_ipv4_interface() {
    python3 - "$1" <<'PY'
import ipaddress
import sys
try:
    value = ipaddress.ip_interface(sys.argv[1])
except ValueError as exc:
    raise SystemExit(f"ERROR: invalid IPv4 interface: {exc}")
if value.version != 4:
    raise SystemExit("ERROR: address must be IPv4")
PY
}

validate_ipv4_address() {
    python3 - "$1" <<'PY'
import ipaddress
import sys
try:
    value = ipaddress.ip_address(sys.argv[1])
except ValueError as exc:
    raise SystemExit(f"ERROR: invalid IPv4 address: {exc}")
if value.version != 4:
    raise SystemExit("ERROR: address must be IPv4")
PY
}

delete_test_profile() {
    if nmcli -t -f NAME connection show | grep -Fxq "$TEST_PROFILE"; then
        nmcli connection down "$TEST_PROFILE" >/dev/null 2>&1 || true
        nmcli connection delete "$TEST_PROFILE" >/dev/null
    fi
}

require_root
command -v nmcli >/dev/null 2>&1 || fail "NetworkManager nmcli is not installed"

case "${1:-}" in
    version)
        [[ $# -eq 1 ]] || fail "usage: $0 version"
        printf '%s\n' "$HELPER_API"
        ;;

    probe)
        [[ $# -eq 1 ]] || fail "usage: $0 probe"
        ;;

    delete-test)
        [[ $# -eq 1 ]] || fail "usage: $0 delete-test"
        delete_test_profile
        ;;

    check-address)
        [[ $# -eq 3 ]] || fail "usage: $0 check-address INTERFACE IPv4"
        interface="$2"
        target_ip="$3"
        validate_interface "$interface"
        validate_ipv4_address "$target_ip"
        if ! command -v arping >/dev/null 2>&1; then
            exit 3
        fi
        # iputils arping -D returns 0 when the address appears unused and 1 when
        # another host replies. Other failures are reported distinctly.
        set +e
        arping -D -c 2 -w 3 -I "$interface" "$target_ip" >/dev/null 2>&1
        rc=$?
        set -e
        [[ $rc -eq 0 ]] && exit 0
        [[ $rc -eq 1 ]] && exit 1
        exit 4
        ;;

    activate)
        [[ $# -eq 3 ]] || fail "usage: $0 activate INTERFACE IPv4/PREFIX"
        interface="$2"
        address="$3"
        validate_interface "$interface"
        validate_ipv4_interface "$address"
        delete_test_profile
        nmcli connection add save no \
            type ethernet \
            con-name "$TEST_PROFILE" \
            ifname "$interface" \
            connection.autoconnect no \
            802-3-ethernet.mac-address "" \
            ipv4.method manual \
            ipv4.addresses "$address" \
            ipv4.gateway "" \
            ipv4.dns "" \
            ipv4.never-default yes \
            ipv6.method disabled >/dev/null
        if ! nmcli connection up "$TEST_PROFILE" ifname "$interface" >/dev/null; then
            delete_test_profile
            fail "failed to activate temporary Test Poc profile"
        fi
        ;;

    restore)
        [[ $# -eq 3 ]] || fail "usage: $0 restore INTERFACE UUID"
        interface="$2"
        uuid="$3"
        validate_interface "$interface"
        [[ "$uuid" =~ ^[0-9A-Fa-f-]{32,36}$ ]] || fail "invalid NetworkManager UUID"
        delete_test_profile
        nmcli connection up uuid "$uuid" ifname "$interface" >/dev/null
        ;;

    *)
        fail "usage: $0 {version|probe|delete-test|check-address INTERFACE IPv4|activate INTERFACE IPv4/PREFIX|restore INTERFACE UUID}"
        ;;
esac
