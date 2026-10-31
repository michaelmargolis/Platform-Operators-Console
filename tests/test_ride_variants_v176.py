# test_ride_variants_v176.py ver 1
"""v176 regression: labels are IDs; park reloads only for different park keys.

Tests are offline. They send no SHS/Festo output or physical platform commands.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.telemetry_config import TelemetryConfig
from sims.nolimits2 import config, ride_config
from sims.nolimits2.sim import Sim
from tests.test_nolimits2_adapter import FakeNL2Client, axis_quaternion, telemetry


def connected():
    client = FakeNL2Client()
    sim = Sim(client=client)
    sim.connect()
    sim.service()
    assert sim._ride_load_pending
    return sim, client


def load_count(client):
    return sum(command[0] == 'load_park' for command in client.commands)


def completed(sim, client):
    client.complete_request(sim._ride_load_request_id, ok=True)
    sim._service_ride_load_result()


def idx(sim, label):
    return sim.get_experiences().index(label)


def test_no_duplicated_park_calibration_and_recorder_indices():
    assert len(config.RIDES) == 10
    assert tuple(name for name, _ in config.RIDES) == tuple(p['name'] for p in ride_config.PARKS.values())
    for i, (name, _) in enumerate(config.RIDES):
        j = ride_config.variant_index_for_park_index(i)
        assert ride_config.labels()[j] == name
    assert ride_config.variant_index_for_park_index(2) == ride_config.labels().index('Fenrir')
    assert ride_config.PARKS['blackbird']['normalized_height'] == config.NORMALIZED_HEIGHT_PROFILES['Blackbird']


def test_same_park_variant_switch_preserves_station_without_reloading():
    sim, client = connected()
    completed(sim, client)
    sim.select_experience(idx(sim, 'Blackbird'))
    completed(sim, client)
    loads = load_count(client)
    sim._ride_station = (4, 3)
    sim._ready_station = (4, 3)
    sim._last_station_request = 345.0
    sim.select_experience(idx(sim, 'Blackbird [Sine]'))
    assert load_count(client) == loads
    assert not sim._ride_load_pending
    assert sim._ride_station == (4, 3)
    assert sim._ready_station == (4, 3)
    assert sim._last_station_request == 345.0
    assert sim._loaded_index == idx(sim, 'Blackbird [Sine]')
    assert sim._angular_cue_mode == 'sine'
    sim.select_experience(idx(sim, 'Blackbird [Sine Roll 120%]'))
    assert sim._loaded_index == idx(sim, 'Blackbird [Sine Roll 120%]')
    assert load_count(client) == loads
    sim.select_experience(idx(sim, 'Blackbird'))
    assert sim._angular_cue_mode == 'euler'
    assert load_count(client) == loads


def test_latest_variant_during_pending_same_park_does_not_queue_reload():
    sim, client = connected()
    completed(sim, client)
    sim.select_experience(idx(sim, 'Blackbird'))
    in_flight = sim._ride_load_request_id
    sim.select_experience(idx(sim, 'Blackbird [Sine]'))
    sim.select_experience(idx(sim, 'Blackbird [Gravity]'))
    assert sim._queued_ride_index is None
    assert sim._loaded_index == 0
    assert sim._angular_cue_mode == 'euler'
    client.complete_request(in_flight, ok=True)
    sim._service_ride_load_result()
    assert sim._loaded_index == idx(sim, 'Blackbird [Gravity]')
    assert sim._angular_cue_mode == 'gravity'
    assert load_count(client) == 2


def test_different_park_while_loading_is_queued_and_not_duplicated():
    sim, client = connected()
    sim.select_experience(idx(sim, 'Blackbird [Sine]'))
    sim.select_experience(idx(sim, 'GrandNational'))
    assert load_count(client) == 1
    completed(sim, client)
    assert load_count(client) == 2
    assert sim._loading_index == idx(sim, 'GrandNational')
    completed(sim, client)
    assert sim._loaded_index == idx(sim, 'GrandNational')
    assert sim._angular_cue_mode == 'euler'


def test_failed_park_load_leaves_unconfirmed_variant_inactive():
    sim, client = connected()
    completed(sim, client)
    sim.select_experience(idx(sim, 'Blackbird [Sine]'))
    client.complete_request(sim._ride_load_request_id, ok=False, detail='missing park')
    sim._service_ride_load_result()
    assert sim._loaded_index is None
    assert sim._angular_cue_mode == 'euler'
    assert not sim.is_ready_to_start()
    sim.select_experience(idx(sim, 'Blackbird [Sine]'))
    assert sim._ride_load_pending


def test_gain_scales_use_global_config_without_persisting(tmp_path):
    cfg = TelemetryConfig(tmp_path/'telemetry_config.ini')
    global_original = cfg.snapshot()
    cfg.set_variant_gain_scales([100, 100, 100, 120, 100, 100])
    out = cfg.scale_without_washout((0,0,0,0.5,0,0), 100)
    assert out[3] == pytest.approx(0.6 * global_original['gains'][3]/100)
    cfg.save()
    assert cfg.snapshot() == global_original
    fresh = TelemetryConfig(tmp_path/'telemetry_config.ini')
    assert fresh.variant_gain_scales == (100,) * 6
    assert fresh.snapshot() == global_original
    with pytest.raises(ValueError):
        cfg.set_variant_gain_scales([100, 100, True, 100, 100, 100])
    with pytest.raises(ValueError):
        cfg.set_variant_gain_scales([100, 100, 250, 100, 100, 100])


def test_core_variant_sync_uses_confirmed_label_and_clears_overrides(tmp_path):
    pytest.importorskip("PyQt5")
    from core.poc_core import PocCore
    sim, client = connected()
    completed(sim, client)
    sim.select_experience(idx(sim, 'Blackbird'))
    completed(sim, client)
    cfg = TelemetryConfig(tmp_path/'telemetry_config.ini')
    core = SimpleNamespace(sim_key='nolimits2', telemetry_config=cfg, sim=sim,
                           _angular_washout_ride=None, _motion_transform=(0,)*6)
    PocCore._sync_nl2_angular_washout(core)
    assert core._angular_washout_ride == 'Blackbird'
    sim.select_experience(idx(sim, 'Blackbird [Sine Roll 120%]'))
    PocCore._sync_nl2_angular_washout(core)
    assert cfg.variant_gain_scales[3] == 120
    assert cfg.variant_gain_scales[4] == 100
    sim.select_experience(idx(sim, 'Blackbird'))
    PocCore._sync_nl2_angular_washout(core)
    assert cfg.variant_gain_scales == (100,) * 6
    assert cfg.snapshot()['gains'] == [100] * 6


def test_production_angular_cue_shares_offline_mapping_without_yaw_changes():
    sim = Sim(client=FakeNL2Client())
    q = axis_quaternion(2, 1.2)
    tm = telemetry(quaternion=q, gy=1.0)
    sim._yaw_rate_from_quaternion = lambda *_: .2
    euler = sim._transform_from_telemetry(tm)
    sim._angular_cue_mode = 'sine'
    sine = sim._transform_from_telemetry(tm)
    sim._angular_cue_mode = 'gravity'
    gravity = sim._transform_from_telemetry(tm)
    assert sine[5] == euler[5] == gravity[5]
    assert sine[:3] == euler[:3] == gravity[:3]
    assert sine[3] == pytest.approx(gravity[3])
    assert abs(sine[3]-euler[3]) > .05


def test_analyser_variant_washout_and_vertical_g_filter_reuses_shared_config():
    from tools.analyze_nl2_csv import analyze
    data = [(i*.05, 10.0, (0,0,0.25,0.0,0.4,0.0),None) for i in range(1,5)]
    snap = ride_config.variant('Blackbird')
    snap['roll_washout'] = False
    snap['pitch_washout'] = True
    snap['heave_mode'] = 'vertical_g'
    frames, stats = analyze(data,'platform_v2','Recorded heave',80,100,True,
                            ride_name='Blackbird',variant_snapshot=snap)
    assert stats['washout_pitch_enabled'] is True
    assert frames[0]['norm'][4] < .4
    assert abs(frames[0]['norm'][2]) < .25
