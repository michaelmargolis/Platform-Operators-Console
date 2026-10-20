#test_config_store.py  ver 6
import json

import pytest

from core.config_store import ConfigStore, DEFAULTS


def test_defaults_have_single_shs_address():
    assert "sim_addresses" not in DEFAULTS
    assert DEFAULTS["sim_host_service"]["address"] == "192.168.0.4"
    assert DEFAULTS["selected_platform"] == "platform_v2"
    assert "virtual_output" not in DEFAULTS
    assert DEFAULTS["digital_twin"] == {"address": "-", "port": 10020}



def test_platform_v2_maps_to_mdx_suspended_platform():
    from config.platforms import PLATFORMS

    definition = PLATFORMS["platform_v2"]
    assert definition["name"] == "MDX Suspended Platform"
    assert definition["platform_name"] == "wheelchair_platform"
    assert definition["sop_config_module"] == "kinematics.cfg_SuspendedPlatformMdx"

    module = __import__(definition["sop_config_module"], fromlist=["PlatformConfig"])
    cfg = module.PlatformConfig()
    assert cfg.PLATFORM_NAME == "MDX Suspended Platform"
    assert cfg.WORKSPACE_LIMIT_MODE == "geometry"
    assert cfg.MUSCLE_EXTENSION_RESERVE == 60.0

def test_legacy_sim_addresses_are_removed_from_existing_config(tmp_path):
    path = tmp_path / "poc_config.json"
    path.write_text(json.dumps({
        "selected_sim": "xplane",
        "sim_addresses": {"xplane": "10.0.0.99"},
        "sim_host_service": {"address": "10.0.0.10"},
    }), encoding="utf-8")

    store = ConfigStore(path)

    assert "sim_addresses" not in store.data
    assert store.get("sim_host_service")["address"] == "10.0.0.10"
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert "sim_addresses" not in persisted


def test_config_file_version_metadata_is_added(tmp_path):
    path = tmp_path / "poc_config.json"
    path.write_text(json.dumps({
        "selected_sim": "spacecoaster",
        "sim_host_service": {"address": "192.168.1.93"},
    }), encoding="utf-8")

    ConfigStore(path)

    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["_file_version"] == "poc_config.json ver 6"
    assert "sim_addresses" not in persisted


def test_obsolete_virtual_output_is_removed(tmp_path):
    path = tmp_path / "poc_config.json"
    path.write_text(json.dumps({
        "_file_version": "poc_config.json ver 2",
        "virtual_output": True,
    }), encoding="utf-8")

    store = ConfigStore(path)

    assert "virtual_output" not in store.data
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert "virtual_output" not in persisted


def test_digital_twin_defaults_are_added_and_partial_config_is_merged(tmp_path):
    path = tmp_path / "poc_config.json"
    path.write_text(json.dumps({
        "_file_version": "poc_config.json ver 3",
        "digital_twin": {"address": "192.168.0.20"},
    }), encoding="utf-8")

    store = ConfigStore(path)

    assert store.get("digital_twin") == {
        "address": "192.168.0.20",
        "port": 10020,
    }
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["digital_twin"] == {
        "address": "192.168.0.20",
        "port": 10020,
    }


def test_legacy_wheelchair_platform_key_is_migrated(tmp_path):
    path = tmp_path / "poc_config.json"
    path.write_text(json.dumps({
        "selected_platform": "wheelchair",
    }), encoding="utf-8")

    store = ConfigStore(path)

    assert store.get("selected_platform") == "platform_v2"
    assert json.loads(path.read_text(encoding="utf-8"))["selected_platform"] == "platform_v2"


def test_malformed_json_fails_with_location(tmp_path):
    path = tmp_path / "poc_config.json"
    path.write_text('{"selected_sim":', encoding="utf-8")

    with pytest.raises(RuntimeError, match=r"Invalid JSON.*line 1"):
        ConfigStore(path)


@pytest.mark.parametrize("key,value", [
    ("selected_platform", "not-a-platform"),
    ("selected_sim", "not-a-simulator"),
])
def test_invalid_runtime_selection_fails_closed(tmp_path, key, value):
    path = tmp_path / "poc_config.json"
    path.write_text(json.dumps({key: value}), encoding="utf-8")

    with pytest.raises(RuntimeError, match=key):
        ConfigStore(path)


def test_invalid_network_endpoint_fails_closed(tmp_path):
    path = tmp_path / "poc_config.json"
    path.write_text(json.dumps({
        "sim_host_service": {"address": "not-an-address", "port": 10040},
    }), encoding="utf-8")

    with pytest.raises(RuntimeError, match="sim_host_service IPv4 address"):
        ConfigStore(path)
