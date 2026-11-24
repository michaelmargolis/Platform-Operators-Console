#test_config_store.py  ver 10
import json

import pytest

from core.config_store import ConfigStore


CANONICAL = {
    "_file_version": "poc_config.json ver 9",
    "selected_sim": "nolimits2",
    "selected_platform": "platform_v2",
    "festo_valve0_side": {
        "platform_v2": "right",
        "chair_v3": "left",
        "chair_v2": "left",
        "Falcon": "right",
    },
    "use_legacy_pi_pins": False,
    "ground_loading_min_clearance_mm": 15.0,
    "sim_host_service": {
        "enabled": True,
        "address": "192.168.0.4",
        "port": 10040,
        "heartbeat_interval_s": 1.0,
        "heartbeat_timeout_s": 3.0,
    },
    "digital_twin": {
        "address": "-",
        "port": 10020,
    },
}


def write_config(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def test_platform_v2_maps_to_ground_loading_platform():
    from config.platforms import PLATFORMS

    definition = PLATFORMS["platform_v2"]
    assert definition["name"] == "Ground-Loading Platform"
    assert definition["platform_name"] == "wheelchair_platform"
    assert definition["sop_config_module"] == "kinematics.cfg_SuspendedPlatformMdx"

    module = __import__(definition["sop_config_module"], fromlist=["PlatformConfig"])
    cfg = module.PlatformConfig()
    assert cfg.PLATFORM_NAME == "Ground-Loading Platform"
    assert cfg.WORKSPACE_LIMIT_MODE == "geometry"
    assert cfg.MUSCLE_EXTENSION_RESERVE == 60.0


def test_missing_local_is_copied_from_canonical_byte_for_byte(tmp_path):
    canonical = tmp_path / "poc_config.json"
    write_config(canonical, CANONICAL)

    store = ConfigStore(canonical)
    local = tmp_path / "poc_config_local.json"

    assert store.local_created is True
    assert local.read_bytes() == canonical.read_bytes()
    assert store.data == CANONICAL


def test_pre_v250_local_gets_only_valve_side_defaults_migrated(tmp_path):
    canonical = tmp_path / "poc_config.json"
    local = tmp_path / "poc_config_local.json"
    write_config(canonical, CANONICAL)

    old_local = json.loads(json.dumps(CANONICAL))
    del old_local["festo_valve0_side"]
    old_local["selected_platform"] = "chair_v3"
    write_config(local, old_local)

    store = ConfigStore(canonical)

    assert store.local_migrated_fields == ("festo_valve0_side",)
    assert store.get("selected_platform") == "chair_v3"
    assert store.get("festo_valve0_side") == CANONICAL["festo_valve0_side"]
    persisted = json.loads(local.read_text(encoding="utf-8"))
    assert persisted["festo_valve0_side"] == CANONICAL["festo_valve0_side"]


def test_existing_local_values_are_authoritative_and_canonical_is_unchanged(tmp_path):
    canonical = tmp_path / "poc_config.json"
    local = tmp_path / "poc_config_local.json"
    write_config(canonical, CANONICAL)

    local_data = json.loads(json.dumps(CANONICAL))
    local_data["selected_platform"] = "chair_v3"
    local_data["use_legacy_pi_pins"] = True
    local_data["sim_host_service"]["address"] = "192.168.0.7"
    write_config(local, local_data)
    before = canonical.read_bytes()

    store = ConfigStore(canonical)

    assert store.local_created is False
    assert store.get("selected_platform") == "chair_v3"
    assert store.get("use_legacy_pi_pins") is True
    assert store.get("sim_host_service")["address"] == "192.168.0.7"
    assert canonical.read_bytes() == before


def test_set_writes_only_local_file(tmp_path):
    canonical = tmp_path / "poc_config.json"
    write_config(canonical, CANONICAL)
    before = canonical.read_bytes()

    store = ConfigStore(canonical)
    store.set("ground_loading_min_clearance_mm", 25.5)

    assert canonical.read_bytes() == before
    local = json.loads((tmp_path / "poc_config_local.json").read_text(encoding="utf-8"))
    assert local["ground_loading_min_clearance_mm"] == 25.5
    assert ConfigStore(canonical).get("ground_loading_min_clearance_mm") == 25.5


@pytest.mark.parametrize("missing_path", [
    ("use_legacy_pi_pins",),
    ("sim_host_service", "heartbeat_timeout_s"),
    ("digital_twin", "port"),
])
def test_missing_local_field_blocks_startup(tmp_path, missing_path):
    canonical = tmp_path / "poc_config.json"
    local = tmp_path / "poc_config_local.json"
    write_config(canonical, CANONICAL)
    local_data = json.loads(json.dumps(CANONICAL))

    parent = local_data
    for part in missing_path[:-1]:
        parent = parent[part]
    del parent[missing_path[-1]]
    write_config(local, local_data)

    with pytest.raises(RuntimeError, match=r"Missing from local"):
        ConfigStore(canonical)


@pytest.mark.parametrize("extra_path", [
    ("obsolete_setting",),
    ("sim_host_service", "old_port"),
])
def test_extra_local_field_blocks_startup(tmp_path, extra_path):
    canonical = tmp_path / "poc_config.json"
    local = tmp_path / "poc_config_local.json"
    write_config(canonical, CANONICAL)
    local_data = json.loads(json.dumps(CANONICAL))

    parent = local_data
    for part in extra_path[:-1]:
        parent = parent[part]
    parent[extra_path[-1]] = 1
    write_config(local, local_data)

    with pytest.raises(RuntimeError, match=r"Not present in canonical"):
        ConfigStore(canonical)


@pytest.mark.parametrize("path,value", [
    (("use_legacy_pi_pins",), "false"),
    (("ground_loading_min_clearance_mm",), "15"),
    (("sim_host_service",), "192.168.0.4"),
    (("sim_host_service", "port"), "10040"),
])
def test_json_type_mismatch_blocks_startup(tmp_path, path, value):
    canonical = tmp_path / "poc_config.json"
    local = tmp_path / "poc_config_local.json"
    write_config(canonical, CANONICAL)
    local_data = json.loads(json.dumps(CANONICAL))

    parent = local_data
    for part in path[:-1]:
        parent = parent[part]
    parent[path[-1]] = value
    write_config(local, local_data)

    with pytest.raises(RuntimeError, match=r"Field type mismatch"):
        ConfigStore(canonical)


def test_value_differences_do_not_count_as_structure_mismatch(tmp_path):
    canonical = tmp_path / "poc_config.json"
    local = tmp_path / "poc_config_local.json"
    write_config(canonical, CANONICAL)
    local_data = json.loads(json.dumps(CANONICAL))
    local_data["_file_version"] = "local note"
    local_data["selected_sim"] = "spacecoaster"
    local_data["selected_platform"] = "chair_v3"
    local_data["use_legacy_pi_pins"] = True
    local_data["digital_twin"]["address"] = "192.168.1.99"
    write_config(local, local_data)

    store = ConfigStore(canonical)

    assert store.get("selected_sim") == "spacecoaster"
    assert store.get("selected_platform") == "chair_v3"
    assert store.get("use_legacy_pi_pins") is True


def test_malformed_local_json_fails_with_location(tmp_path):
    canonical = tmp_path / "poc_config.json"
    local = tmp_path / "poc_config_local.json"
    write_config(canonical, CANONICAL)
    local.write_text('{"selected_sim":', encoding="utf-8")

    with pytest.raises(RuntimeError, match=r"Invalid JSON.*line 1"):
        ConfigStore(canonical)


@pytest.mark.parametrize("key,value", [
    ("selected_platform", "not-a-platform"),
    ("selected_sim", "not-a-simulator"),
])
def test_invalid_local_runtime_selection_fails_closed(tmp_path, key, value):
    canonical = tmp_path / "poc_config.json"
    local = tmp_path / "poc_config_local.json"
    write_config(canonical, CANONICAL)
    local_data = json.loads(json.dumps(CANONICAL))
    local_data[key] = value
    write_config(local, local_data)

    with pytest.raises(RuntimeError, match=key):
        ConfigStore(canonical)


@pytest.mark.parametrize("platform,side", [
    ("platform_v2", "centre"),
    ("chair_v3", ""),
])
def test_invalid_festo_valve0_side_fails_closed(tmp_path, platform, side):
    canonical = tmp_path / "poc_config.json"
    local = tmp_path / "poc_config_local.json"
    write_config(canonical, CANONICAL)
    local_data = json.loads(json.dumps(CANONICAL))
    local_data["festo_valve0_side"][platform] = side
    write_config(local, local_data)

    with pytest.raises(RuntimeError, match="festo_valve0_side"):
        ConfigStore(canonical)


def test_invalid_local_network_endpoint_fails_closed(tmp_path):
    canonical = tmp_path / "poc_config.json"
    local = tmp_path / "poc_config_local.json"
    write_config(canonical, CANONICAL)
    local_data = json.loads(json.dumps(CANONICAL))
    local_data["sim_host_service"]["address"] = "not-an-address"
    write_config(local, local_data)

    with pytest.raises(RuntimeError, match="sim_host_service IPv4 address"):
        ConfigStore(canonical)
