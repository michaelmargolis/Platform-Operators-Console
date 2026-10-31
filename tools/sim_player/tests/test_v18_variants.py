# test_v18_variants.py ver 1
"""Standalone v18 variant processing tests without Qt/POC platform backend."""
import json
import pytest
from sims.nolimits2 import ride_config
from tools.sim_player.variant_presets import (
    apply_heave, choices_for_capture, gain_tuple, python_variant_entry, snapshot_for_capture,
)
from tools.sim_player.tuning import ExperimentSettings, save_experiment, load_experiment


def test_variant_selection_matches_capture_park_only():
    labels = choices_for_capture('Blackbird')
    assert 'Blackbird [Sine Roll 120%]' in labels
    assert 'GrandNational' not in labels
    assert choices_for_capture('unknown') == ()
    with pytest.raises(ValueError):
        snapshot_for_capture('Blackbird','GrandNational')
    assert gain_tuple(snapshot_for_capture('Blackbird','Blackbird [Sine Roll 120%]')) == (
        100,100,100,120,100,100)


def test_calibrated_height_endpoints_and_original_unchanged():
    rows = [(0.0,5.9,(0,0,0.77,0,0,0),None),
            (0.05,23.45,(0,0,0.77,0,0,0),None),
            (0.1,41.0,(0,0,0.77,0,0,0),None)]
    snapshot=ride_config.variant('Blackbird')
    mapped=apply_heave(rows,snapshot)
    assert [row[2][2] for row in mapped] == pytest.approx([0.9,0,-0.9])
    assert all(row[2][2] == .77 for row in rows)
    assert rows[0][2] != mapped[0][2]
    snapshot['normalized_height_amplitude'] = .70
    assert apply_heave(rows,snapshot)[0][2][2] == pytest.approx(.70)


def test_legacy_height_profile_and_vertical_g_requires_data():
    snap=ride_config.variant('Mdx Logo Coaster')
    snap['heave_mode']='legacy_height'
    rows=[(0,7.4,(0,0,1,0,0,0),None),(1,32.6,(0,0,1,0,0,0),None)]
    assert [v[2][2] for v in apply_heave(rows,snap)] == pytest.approx([-.5,-.18])
    snap['heave_mode']='vertical_g'
    with pytest.raises(ValueError,match='vertical_g_heave'):
        apply_heave(rows,snap)
    rows_g = [(0,7.4,(0,0,1,0,0,0),0.25),(1,32.6,(0,0,1,0,0,0),-.15)]
    assert [v[2][2] for v in apply_heave(rows_g,snap)] == [.25,-.15]


def test_experiment_v2_retains_archived_snapshot_and_reads_v1(tmp_path):
    snapshot=ride_config.variant('Blackbird [Sine]')
    settings=ExperimentSettings('sine',(100,100,100,120,100,100),45,25,
                                'Blackbird [Sine]',snapshot,{'master_gain':90})
    path=tmp_path/'study.json'
    save_experiment(path,settings)
    saved=json.loads(path.read_text())
    assert saved['schema']=='sim_player_experiment_v3'
    assert saved['variant_label']=='Blackbird [Sine]'
    assert saved['variant_snapshot']['park']=='blackbird'
    assert load_experiment(path)==settings
    old={k:v for k,v in saved.items() if k in ('angular_cue','relative_gains_percent','anomaly_step_warning_mm','anomaly_clearance_warning_mm')}
    old['schema']='sim_player_experiment_v1'
    path.write_text(json.dumps(old))
    assert load_experiment(path).variant_label is None


def test_variant_export_entry_is_copy_only():
    line=python_variant_entry('Blackbird [Sine Roll 120%]',park='blackbird',angular_cue='sine',
          gains=(100,100,100,120,100,100))
    assert "'label': 'Blackbird [Sine Roll 120%]'" in line
    assert "'gain_scale_pct': {'roll_gain': 120}" in line
