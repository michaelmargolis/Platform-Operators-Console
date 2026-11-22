#test_raspi_network_v218.py  ver 6
from __future__ import annotations

import importlib.util
import ipaddress
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NETWORK_CHECK_PATH = ROOT / "raspi" / "network_check.py"
CONFIGURE_NETWORK_PATH = ROOT / "raspi" / "configure-network.sh"
TEST_POC_PATH = ROOT / "raspi" / "start-test-poc.sh"
TEST_LAUNCHER_INSTALLER_PATH = ROOT / "raspi" / "install-test-poc-launcher.sh"
TEST_NETWORK_HELPER_PATH = ROOT / "raspi" / "test-poc-network-helper.sh"


def _load_network_check():
    spec = importlib.util.spec_from_file_location("network_check_v218", NETWORK_CHECK_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_config(path: Path, address: str = "192.168.0.3/24") -> None:
    path.write_text(
        "PLATFORM_INTERFACE=eth0\n"
        "PLATFORM_MAC=00:11:22:33:44:55\n"  # legacy value must be ignored
        f"PLATFORM_ADDRESS={address}\n"
        "DEVELOPMENT_INTERFACE=eth1\n",
        encoding="utf-8",
    )


def test_network_check_ignores_legacy_mac_and_accepts_configured_ip(tmp_path: Path):
    module = _load_network_check()
    config = tmp_path / "network-config.conf"
    _write_config(config)
    sys_class = tmp_path / "sys" / "class" / "net"
    (sys_class / "eth0").mkdir(parents=True)

    error = module.check_platform_network(
        config_path=config,
        sys_class_net=sys_class,
        address_reader=lambda interface: {ipaddress.ip_address("192.168.0.3")},
    )
    assert error is None


def test_network_check_rejects_wrong_ipv4_but_not_mac(tmp_path: Path):
    module = _load_network_check()
    config = tmp_path / "network-config.conf"
    _write_config(config)
    sys_class = tmp_path / "sys" / "class" / "net"
    (sys_class / "eth0").mkdir(parents=True)

    error = module.check_platform_network(
        config_path=config,
        sys_class_net=sys_class,
        address_reader=lambda interface: {ipaddress.ip_address("10.10.10.20")},
    )
    assert error is not None
    assert "eth0" in error
    assert "192.168.0.3" in error
    assert "MAC" not in error


def test_network_check_rejects_missing_eth0(tmp_path: Path):
    module = _load_network_check()
    config = tmp_path / "network-config.conf"
    _write_config(config)
    sys_class = tmp_path / "sys" / "class" / "net"
    sys_class.mkdir(parents=True)

    error = module.check_platform_network(
        config_path=config,
        sys_class_net=sys_class,
        address_reader=lambda interface: set(),
    )
    assert error == "Configured platform interface eth0 was not found"


def test_permanent_network_script_no_longer_matches_or_binds_mac():
    source = CONFIGURE_NETWORK_PATH.read_text(encoding="utf-8")
    assert "PLATFORM_MAC=" not in source
    assert '802-3-ethernet.mac-address ""' in source
    assert 'PLATFORM_INTERFACE="$(read_setting PLATFORM_INTERFACE)"' in source


def test_test_poc_uses_in_memory_profile_restores_original_and_keeps_display_auto():
    source = TEST_POC_PATH.read_text(encoding="utf-8")
    helper = TEST_NETWORK_HELPER_PATH.read_text(encoding="utf-8")
    assert 'TEST_PROFILE="POC Test Temporary"' in source
    assert "connection add save no" in helper
    assert 'nmcli connection up uuid "$uuid" ifname "$interface"' in helper
    assert '"$SCRIPT_DIR/start_console.sh"' in source
    assert "POC_COMPACT_UI=1" not in source


def test_test_poc_prefers_zenity_but_keeps_offline_terminal_fallback():
    source = TEST_POC_PATH.read_text(encoding="utf-8")
    assert "command -v zenity" in source
    assert "GSK_RENDERER=cairo GTK_A11Y=none zenity --question" in source
    assert '--ok-label="Continue"' in source
    assert '--cancel-label="Cancel"' in source
    assert "Continue with temporary POC networking? [y/N]" in source
    assert "apt-get" not in source
    assert "apt install" not in source
    assert "wget " not in source
    assert "curl " not in source


def test_test_poc_launcher_is_explicit_not_autostart_and_does_not_install_zenity():
    source = TEST_LAUNCHER_INSTALLER_PATH.read_text(encoding="utf-8")
    assert "Name=Test Poc" in source
    assert "Terminal=true" in source
    assert "start-test-poc.sh" in source
    assert "start compact POC" not in source
    assert "command -v zenity" in source
    assert "apt-get" not in source
    assert "apt install" not in source
    assert ".config/autostart" not in source


def test_test_poc_runtime_never_requests_sudo_password_and_uses_fixed_helper():
    source = TEST_POC_PATH.read_text(encoding="utf-8")
    assert 'PRIV_HELPER="/usr/local/libexec/poc-test-network-helper"' in source
    assert 'sudo -n "$PRIV_HELPER" version' in source
    assert 'sudo -n "$PRIV_HELPER" activate' in source
    assert 'sudo -n "$PRIV_HELPER" restore' in source
    assert "sudo -v" not in source
    assert "SUDO_KEEPALIVE_PID" not in source
    assert "sudo nmcli" not in source
    assert "sudo arping" not in source


def test_test_poc_installer_uses_root_owned_helper_and_narrow_sudoers_rule():
    installer = TEST_LAUNCHER_INSTALLER_PATH.read_text(encoding="utf-8")
    helper = TEST_NETWORK_HELPER_PATH.read_text(encoding="utf-8")
    assert 'PRIV_HELPER="/usr/local/libexec/poc-test-network-helper"' in installer
    assert 'SUDOERS_FILE="/etc/sudoers.d/poc-test-poc-network"' in installer
    assert 'install -o root -g root -m 755 "$HELPER_SOURCE" "$PRIV_HELPER"' in installer
    assert 'NOPASSWD: $PRIV_HELPER' in installer
    assert 'visudo -cf "$SUDOERS_TMP"' in installer
    assert 'case "${1:-}" in' in helper
    assert 'HELPER_API="1"' in helper
    assert "eval " not in helper
    assert "bash -c" not in helper
    assert "sh -c" not in helper

def test_test_poc_installer_reports_missing_files_repairs_modes_and_allows_missing_icon():
    installer = TEST_LAUNCHER_INSTALLER_PATH.read_text(encoding="utf-8")
    assert 'required Test Poc file is missing:' in installer
    assert 'chmod 755 "$START_TEST" "$START_CONSOLE" "$HELPER_SOURCE"' in installer
    assert 'DESKTOP_ICON="utilities-terminal"' in installer
    assert 'if [[ -f "$ICON_SOURCE" ]]' in installer
    assert 'complete POC runtime/test launcher not found' not in installer

