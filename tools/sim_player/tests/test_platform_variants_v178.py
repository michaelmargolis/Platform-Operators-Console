# test_platform_variants_v178.py ver 7
"""Platform propagation and JSON candidate saving without Qt or physical I/O."""
from dataclasses import replace
from types import SimpleNamespace
import ast
import json
from pathlib import Path
import pytest

from sims.nolimits2.variant_store import VariantStore
from tools.sim_player.platform_selection import platform_choices, platform_metadata, initial_platform_key
from tools.sim_player.recording import load_recording
from tools.sim_player.variant_presets import (choices_for_capture, default_label_for_capture,
    dropdown_choices_for_capture, snapshot_for_capture, candidate_entry)
from tools.sim_player.tuning import ExperimentSettings, load_experiment, save_experiment
from tools.sim_player.tests.test_recording import make_capture


def test_platform_selector_uses_all_configured_platform_names():
    from config.platforms import PLATFORMS
    from importlib import import_module
    choices=platform_choices()
    assert [k for k,_ in choices]==list(PLATFORMS)
    assert len({name.casefold() for _, name in choices}) == len(choices)
    assert dict(choices)['chair_v3'] == 'Chair V3'
    assert dict(choices)['platform_v2'] == 'Ground-Loading Platform'
    for key, name in choices:
        assert name == import_module(PLATFORMS[key]['sop_config_module']).PlatformConfig.PLATFORM_NAME
        assert len(platform_metadata(key)['platform_config_sha256']) == 64
    for deferred in ('chair_v2', 'Falcon'):
        assert deferred not in dict(choices)
        with pytest.raises(ValueError, match='Unknown platform'):
            platform_metadata(deferred)


def test_default_platform_uses_complete_local_config_without_modifying_canonical(tmp_path):
    canonical = tmp_path / 'poc_config.json'
    canonical.write_text(
        '{"_file_version":"poc_config.json ver 9","selected_sim":"nolimits2",'
        '"selected_platform":"platform_v2","use_legacy_pi_pins":false,'
        '"ground_loading_min_clearance_mm":15.0,'
        '"sim_host_service":{"enabled":true,"address":"192.168.0.4","port":10040,'
        '"heartbeat_interval_s":1.0,"heartbeat_timeout_s":3.0},'
        '"digital_twin":{"address":"-","port":10020},'
        '"festo_valve0_side":{"platform_v2":"right","chair_v3":"left",'
        '"chair_v2":"left","Falcon":"right"}}'
    )
    local = tmp_path / 'poc_config_local.json'
    local.write_text(
        canonical.read_text().replace(
            '"selected_platform":"platform_v2"',
            '"selected_platform":"chair_v3"',
        )
    )
    old = canonical.read_bytes()
    assert initial_platform_key(canonical) == 'chair_v3'
    assert canonical.read_bytes() == old


def test_recording_uses_selected_geometry_limits_floor_and_snapshot(tmp_path):
    path=make_capture(tmp_path/'capture',video=False)
    original=path.read_bytes()
    mdx=load_recording(path,platform='platform_v2',default_standard_variant=True)
    chair=load_recording(path,platform='chair_v3',default_standard_variant=True)
    assert mdx.platform_key=='platform_v2' and chair.platform_key=='chair_v3'
    assert mdx.max_length_mm==1000 and chair.max_length_mm==800
    assert mdx.min_length_mm==750 and chair.min_length_mm==600
    assert mdx.frames[0].muscle_lengths_mm!=chair.frames[0].muscle_lengths_mm
    assert mdx.frames[0].modeled_clearance_mm is not None
    assert all(f.modeled_clearance_mm is None for f in chair.frames)
    assert mdx.variant_snapshot['platform_key']=='platform_v2'
    assert chair.variant_snapshot['platform_key']=='chair_v3'
    assert mdx.platform_config_sha256!=chair.platform_config_sha256
    assert path.read_bytes()==original


def test_platform_catalogs_can_use_same_label_with_independent_settings(tmp_path):
    a=VariantStore.load('platform_v2',directory=tmp_path/'a')
    b=VariantStore.load('chair_v3',directory=tmp_path/'b')
    a.add_candidate({'label':'Blackbird [Custom]', 'park':'blackbird','gain_scale_pct':{'roll_gain':80}})
    b.add_candidate({'label':'Blackbird [Custom]', 'park':'blackbird','gain_scale_pct':{'roll_gain':130}})
    assert snapshot_for_capture('Blackbird','Blackbird [Custom]',a)['gain_scale_pct']['roll']==80
    assert snapshot_for_capture('Blackbird','Blackbird [Custom]',b)['gain_scale_pct']['roll']==130
    a.review('Blackbird [Custom]','preferred')
    assert default_label_for_capture('Blackbird',a)=='Blackbird [Custom]'
    assert default_label_for_capture('Blackbird',b)=='Blackbird'
    assert 'Blackbird' not in choices_for_capture('Blackbird',a)
    assert 'Blackbird' in choices_for_capture('Blackbird',b)


def test_player_keeps_candidates_editable_but_poc_prefers_accepted_ride(tmp_path):
    s=VariantStore.load('platform_v2',directory=tmp_path)
    s.review('Blackbird [Sine]','preferred')
    s.add_candidate({'label':'Blackbird [Future]', 'park':'blackbird'})
    assert s.visible_labels('blackbird')==('Blackbird [Sine]',)
    choices=dropdown_choices_for_capture('Blackbird',s)
    assert choices[0]==('Blackbird [Sine]','Blackbird [Sine]')
    assert ('Blackbird [Future]','Blackbird [Future]') in choices
    assert ('Blackbird raw',None) in choices
    assert not any('Gravity' in label for label,_ in choices)


def test_save_candidate_preserves_tuning_and_never_changes_capture_or_global_ini(tmp_path):
    s=VariantStore.load('chair_v3',directory=tmp_path/'variants')
    path=make_capture(tmp_path/'capture',video=False)
    ini=Path(__file__).resolve().parents[3]/'sims/nolimits2/telemetry_config.ini'
    old_csv,old_ini=path.read_bytes(),ini.read_bytes()
    r=load_recording(path,platform='chair_v3',variant_store=s,default_standard_variant=True)
    r=replace(r,cue_mode='sine',gain_percent=(100,100,100,120,90,100))
    entry=candidate_entry('Blackbird [Chair Trial]',r)
    assert entry['status']=='candidate'
    assert entry['gain_scale_pct']=={'roll_gain':120,'pitch_gain':90}
    assert 'platform_key' not in entry
    assert 'park_info' not in entry
    s.add_candidate(entry)
    loaded=VariantStore.load('chair_v3',directory=tmp_path/'variants')
    assert loaded.entry(entry['label'])==entry
    assert path.read_bytes()==old_csv
    assert ini.read_bytes()==old_ini
    with pytest.raises(ValueError):candidate_entry('Blackbird Bad Label',r)
    with pytest.raises(ValueError):candidate_entry('Blackbird [Raw]',replace(r,variant_snapshot=None))


def test_platform_mismatches_do_not_silently_reuse_tuning(tmp_path):
    path=make_capture(tmp_path/'capture',video=False)
    a=VariantStore.load('platform_v2')
    with pytest.raises(ValueError,match='catalog belongs'):
        load_recording(path,platform='chair_v3',variant_store=a)
    with pytest.raises(ValueError,match='belongs to another platform'):
        load_recording(path,platform='chair_v3',variant_label='Blackbird',variant_snapshot=a.snapshot('Blackbird'))


def test_experiment_v3_records_and_restores_platform_and_geometry(tmp_path):
    md=platform_metadata('chair_v3')
    settings=ExperimentSettings(platform_key='chair_v3',
        platform_config_module=md['platform_config_module'],platform_config_sha256=md['platform_config_sha256'])
    path=tmp_path/'exp.json'
    save_experiment(path,settings)
    assert load_experiment(path)==settings
    data=json.loads(path.read_text())
    assert data['schema']=='sim_player_experiment_v3'
    data['schema']='sim_player_experiment_v2'
    for key in ('platform_key','platform_config_module','platform_config_sha256'):data.pop(key)
    path.write_text(json.dumps(data))
    assert load_experiment(path).platform_key is None


def extracted(class_name,method,namespace=None):
    path=Path(__file__).resolve().parents[1]/'app.py'
    tree=ast.parse(path.read_text(encoding='utf-8'))
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==class_name)
    fn=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name==method)
    fn.decorator_list=[]
    ns=namespace or {}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[])),str(path),'exec'),ns)
    return ns[method]


def test_cache_token_contains_platform_and_geometry_not_just_variant_label():
    token=extracted('ReplayWindow','_variant_cache_token')
    a=SimpleNamespace(platform_key='platform_v2',platform_config_sha256='aaa',variant_snapshot=None)
    b=SimpleNamespace(platform_key='chair_v3',platform_config_sha256='bbb',variant_snapshot=None)
    assert token(a)!=token(b)
    b.platform_key=a.platform_key
    assert token(a)!=token(b)


def test_actual_worker_forwards_platform_and_store_to_shared_analysis():
    calls=[];loaded=[];failed=[]
    def calculate(*args,**kwargs):calls.append((args,kwargs));return 'ok'
    run=extracted('LoadWorker','run',{'load_recording':calculate})
    store=object()
    worker=SimpleNamespace(csv_path='ride.csv',platform_key='chair_v3',variant_store=store,
        cue_mode='euler',gain_percent=(100,)*6,variant_label=None,variant_snapshot=None,
        default_standard_variant=True,loaded=SimpleNamespace(emit=loaded.append),
        failed=SimpleNamespace(emit=failed.append))
    run(worker)
    assert loaded==['ok'] and failed==[]
    assert calls[0][1]['platform']=='chair_v3'
    assert calls[0][1]['variant_store'] is store
