"""Profile-aware review tests; synthetic fixtures, production math, no GUI needed."""
from __future__ import annotations
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import ast
import csv
import hashlib
import json
import shutil
import numpy as np
import pytest

from tools.sim_player.dynamic_review import (ROOT,ReviewSpec,ReviewCancelled,DynamicProfile,
    build_recording,build_session,discover_profiles,profile_choice,read_diagnostics,
    processing_mismatches,save_review,read_review,sha256)
from tools.nl2_dynamic_profiles.replay import prepare,processing_identity
from tools.nl2_dynamic_profiles.evaluate import calculate,diagnostic_rows
from tools.sim_player.dynamic_optimization import Geometry
from tools.nl2_sync.matcher import Reference
from tools.nl2_sync_benchmark.pipeline import NoNetwork
from tools.nl2_timing_study.tests.test_analysis import make_trial
from tools.nl2_timing_study.analysis import read_trial
from tools.nl2_timing_study.common import write_table
from tools.sim_player.tuning import muscle_flags,tuning_metrics
from tools.sim_player.anomaly_detection import detect_anomalies


@pytest.fixture(scope='module')
def bundle(tmp_path_factory):
    root=tmp_path_factory.mktemp('dynamic_review')
    trial=make_trial(root,'capture')
    meta=json.loads(trial.read_text());meta.update(park_key='blackbird',ride='Blackbird',
        view_identity=[0,0,0,0,1],departure_station=[0,0],epoch_monotonic_s=100.)
    trial.write_text(json.dumps(meta));trace=read_trial(trial)
    reference=Reference.from_trace(trace)
    results=root/'run_one';results.mkdir();(results/'index.csv').write_text('profile\n')
    paths=[]
    with NoNetwork() as guard:
        for platform in ('chair_v3','platform_v2'):
            series=prepare(ROOT,trace,trace,platform,'sine')
            for target in ('strict','relaxed5'):
                folder=results/platform/'blackbird'/target;folder.mkdir(parents=True)
                ref=folder/'reference.npz'
                np.savez_compressed(ref,time_s=reference.time,xyz_m=reference.xyz,
                                    quaternion_xyzw=reference.quat,speed_mps=reference.speed)
                identity,context=processing_identity(ROOT,series,platform,0.,sha256(ref))
                context_copy=json.loads(json.dumps(context))
                gains=np.array([[.2]*6,[.6]*6,[.35]*6])
                if target=='relaxed5':gains[:,3]+=.05
                p=DynamicProfile([0.,30.,60.],gains,[.1]*6,identity,dict(
                    name='Blackbird_'+platform+'_'+target,target=target,reference_file='reference.npz',
                    reference_trial=trace.name,processing=context_copy,qualification='synthetic_only',
                    source_captures=[dict(trial=trace.name,timing_sha256=meta['file_sha256']['timing'])]))
                profile=folder/'profile.json';p.save(profile)
                data=calculate(series,Geometry(platform,0.),p)
                write_table(folder/(trace.name+'_dynamic_diagnostics.csv'),list(diagnostic_rows(data,series)))
                paths.append(profile)
        assert guard.attempts==0
    return SimpleNamespace(root=root,results=results,profiles=paths,trial=trial,capture=trace.name)


@pytest.mark.parametrize('index',[0,1,2,3])
def test_saved_study_matches_production_lengths(bundle,index):
    with NoNetwork() as guard:
        r=build_recording(ReviewSpec(bundle.profiles[index],bundle.capture))
        assert r.dynamic_profile['parity']['max_output_difference_mm']==0
        assert r.dynamic_profile['parity']['max_requested_difference_mm']<1e-6
        assert len(r.frames)==1201 and r.times[0]==0 and r.duration==60
        assert r.dynamic_profile['source']=='saved study'
        assert all(f.analysis['dynamic']['profile_lag_s'] is None for f in r.frames)
        assert guard.attempts==0


def test_profile_browser_all_targets_and_platforms(bundle):
    choices,errors=discover_profiles(bundle.results)
    assert len(choices)==4 and not errors
    assert {c.target for c in choices}=={'strict','relaxed5'}
    assert {c.platform for c in choices}=={'chair_v3','platform_v2'}
    assert all(c.captures==(bundle.capture,) for c in choices)


def test_any_pair_same_platform_and_capture(bundle):
    s=build_session([ReviewSpec(p,bundle.capture) for p in bundle.profiles[:2]])
    assert len(s.recordings)==2
    assert s.recordings[0].times==s.recordings[1].times
    # Independent profile gains rather than a hard-coded strict-vs-relaxed switch.
    assert s.recordings[0].frames[600].analysis['dynamic']['multipliers']!=s.recordings[1].frames[600].analysis['dynamic']['multipliers']


def test_mismatched_platforms_rejected(bundle):
    with pytest.raises(ValueError,match='platform'):
        build_session([ReviewSpec(bundle.profiles[0],bundle.capture),ReviewSpec(bundle.profiles[2],bundle.capture)])


def test_common_capture_required(bundle):
    with pytest.raises(ValueError,match='same source recording'):
        build_session([ReviewSpec(bundle.profiles[0],bundle.capture),ReviewSpec(bundle.profiles[1],'different')])


def test_unity_is_after_washout_not_refiltered(bundle):
    s=build_session([ReviewSpec(bundle.profiles[0],bundle.capture),ReviewSpec(bundle.profiles[0],bundle.capture,'unity')])
    r=s.recordings[1]
    assert all(f.processed_axes==f.source_axes for f in r.frames)
    assert all(f.analysis['dynamic']['multipliers']==(1.,)*6 for f in r.frames)
    assert r.cue_mode==s.recordings[0].cue_mode
    assert r.variant_snapshot==s.recordings[0].variant_snapshot


def test_fallback_is_constant_bound_profile_value(bundle):
    p=DynamicProfile.load(bundle.profiles[0]);r=build_recording(ReviewSpec(bundle.profiles[0],bundle.capture,'fallback'))
    assert all(np.allclose(f.processed_axes,np.array(f.source_axes)*p.fallback) for f in r.frames)


def test_no_double_filter_on_seek(bundle):
    r=build_recording(ReviewSpec(bundle.profiles[0],bundle.capture))
    first=r.at(40.2)
    for t in (1.,49.,0.,55.,40.2):r.at(t)
    assert r.at(40.2) is first


def test_rounding_not_reported_as_clipping(bundle):
    r=build_recording(ReviewSpec(bundle.profiles[0],bundle.capture))
    assert any(any(abs(a-b)>1e-5 for a,b in zip(f.analysis['workspace_lengths'],f.muscle_lengths_mm)) for f in r.frames)
    assert tuning_metrics(r).final_clipped_frames==0
    assert not any(e.category=='final_clipped' for e in detect_anomalies(r))
    assert all(not any(muscle_flags(f,r.min_length_mm,r.max_length_mm)[1]) for f in r.frames)


@pytest.mark.parametrize('value',['nan','inf','-1'])
def test_invalid_diagnostics_rejected(bundle,tmp_path,value):
    profile=bundle.profiles[0];p=DynamicProfile.load(profile)
    src=profile.parent/(bundle.capture+'_dynamic_diagnostics.csv')
    with src.open() as f:reader=csv.DictReader(f);fields=reader.fieldnames;rows=list(reader)
    rows[5]['multiplier_roll']=value
    dest=tmp_path/'bad.csv';write_table(dest,rows)
    with pytest.raises(ValueError):read_diagnostics(dest,p)


def test_csv_changed_requested_command_rejected(bundle,tmp_path):
    src=bundle.profiles[0].parent;dest=tmp_path/'candidate';shutil.copytree(src,dest)
    csv_path=dest/(bundle.capture+'_dynamic_diagnostics.csv')
    with csv_path.open() as f:rows=list(csv.DictReader(f))
    rows[50]['output_muscle_1_mm']='700'
    write_table(csv_path,rows)
    with pytest.raises(ValueError,match='mismatch'):
        build_recording(ReviewSpec(dest/'profile.json',bundle.capture))


def test_profile_checksum_enforced(bundle,tmp_path):
    src=bundle.profiles[0];d=json.loads(src.read_text());d['multipliers'][1][0]=.7
    bad=tmp_path/'bad.json';bad.write_text(json.dumps(d))
    with pytest.raises(ValueError,match='checksum'):DynamicProfile.load(bad)


def test_reference_checksum_enforced(bundle,tmp_path):
    dest=tmp_path/'candidate';shutil.copytree(bundle.profiles[0].parent,dest)
    p=dest/'reference.npz';p.write_bytes(p.read_bytes()+b'changed')
    with pytest.raises(ValueError,match='SHA256'):profile_choice(dest/'profile.json')


def test_processing_change_refused(bundle,tmp_path):
    with pytest.raises(ValueError,match='requires the installed processing/geometry files'):
        build_recording(ReviewSpec(bundle.profiles[0],bundle.capture),root=tmp_path)


def _checkout_with_one_changed_source(bundle, tmp_path):
    """Complete source tree plus a harmless legacy cfg text change."""
    profile = DynamicProfile.load(bundle.profiles[0])
    for name in profile.metadata['processing']['source_hashes']:
        source = ROOT / name
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    changed = tmp_path / 'kinematics/cfg_SuspendedChair.py'
    changed.write_bytes(changed.read_bytes() + b'\n# offline review source parity test\n')
    return tmp_path


def test_saved_review_accepts_source_text_change_only_after_numerical_parity(bundle, tmp_path):
    root = _checkout_with_one_changed_source(bundle, tmp_path)
    profile = DynamicProfile.load(bundle.profiles[0])
    assert processing_mismatches(profile, root) == ['kinematics/cfg_SuspendedChair.py']
    record = build_recording(ReviewSpec(bundle.profiles[0], bundle.capture), root=root)
    assert record.dynamic_profile['numerical_replay_parity_verified']
    assert record.dynamic_profile['processing_source_mismatches'] == ['kinematics/cfg_SuspendedChair.py']
    assert record.dynamic_profile['parity']['max_requested_difference_mm'] < 1e-6
    assert 'qualification does NOT transfer' in record.diagnostics


def test_static_comparison_with_changed_source_still_checks_original_dynamic_parity(bundle, tmp_path, monkeypatch):
    from tools.sim_player import dynamic_review as dr
    root = _checkout_with_one_changed_source(bundle, tmp_path)
    modes=[]
    original = dr._calculate_ticks
    def checking(*args, **kwargs):
        modes.append(args[2])
        return original(*args, **kwargs)
    monkeypatch.setattr(dr, '_calculate_ticks', checking)
    record=build_recording(ReviewSpec(bundle.profiles[0],bundle.capture,'unity'),root=root)
    assert modes == ['dynamic','unity']
    assert record.dynamic_profile['numerical_replay_parity_verified']


def test_raw_replay_with_changed_source_still_refused(bundle, tmp_path):
    root = _checkout_with_one_changed_source(bundle, tmp_path)
    with pytest.raises(ValueError,match='Raw replay cannot reuse'):
        build_recording(ReviewSpec(bundle.profiles[0],bundle.capture,trial_path=bundle.trial),root=root)


def test_saved_review_with_modified_results_and_changed_source_still_refused(bundle, tmp_path):
    root = _checkout_with_one_changed_source(bundle, tmp_path / 'checkout')
    dest = tmp_path / 'altered_profile'
    shutil.copytree(bundle.profiles[0].parent,dest)
    csv_path = dest/(bundle.capture+'_dynamic_diagnostics.csv')
    with csv_path.open() as stream:rows=list(csv.DictReader(stream))
    rows[80]['output_muscle_2_mm']='700'
    write_table(csv_path,rows)
    with pytest.raises(ValueError,match='Source files differ AND the installed kinematics'):
        build_recording(ReviewSpec(dest/'profile.json',bundle.capture),root=root)


def test_crlf_only_accepted(bundle,tmp_path):
    p=DynamicProfile.load(bundle.profiles[0]);name='tools/nl2_sync/profile.py'
    p.metadata['processing']['source_hashes']={name:sha256(ROOT/name)}
    f=tmp_path/name;f.parent.mkdir(parents=True);f.write_bytes((ROOT/name).read_bytes().replace(b'\n',b'\r\n'))
    assert not processing_mismatches(p,tmp_path)


def test_cancellation_is_explicit(bundle):
    with pytest.raises(ReviewCancelled):build_recording(ReviewSpec(bundle.profiles[0],bundle.capture),cancel=lambda:True)


def test_save_reopen_does_not_write_variants(bundle,tmp_path):
    s=build_session([ReviewSpec(bundle.profiles[0],bundle.capture),ReviewSpec(bundle.profiles[0],bundle.capture,'unity')])
    s.active=1
    path=tmp_path/'review.json';save_review(path,s,25.,dict(start=10,end=30,mode='loop',loop_delay_s=1))
    specs,active,seconds,loop=read_review(path)
    assert specs==s.specs and active==1 and seconds==25 and loop['mode']=='loop'
    with pytest.raises(FileExistsError):save_review(path,s)


def test_saved_review_rejects_changed_trace(bundle,tmp_path):
    dest=tmp_path/'candidate';shutil.copytree(bundle.profiles[0].parent,dest)
    s=build_session([ReviewSpec(dest/'profile.json',bundle.capture)])
    path=tmp_path/'review.json';save_review(path,s)
    diagnostic=dest/(bundle.capture+'_dynamic_diagnostics.csv');diagnostic.write_text(diagnostic.read_text()+'\n')
    with pytest.raises(ValueError,match='trace has changed'):read_review(path)


def test_invalid_review_names_and_modes():
    for capture in ('../capture','C:\\capture','/tmp/capture'):
        with pytest.raises(ValueError):ReviewSpec(Path('profile.json'),capture)
    with pytest.raises(ValueError):ReviewSpec(Path('profile.json'),'capture','live')


@pytest.mark.parametrize('index',[0,2])
def test_raw_causal_replay_matches_saved_study(bundle,index):
    spec=ReviewSpec(bundle.profiles[index],bundle.capture)
    with NoNetwork() as guard:
        saved=build_recording(spec)
        raw=build_recording(replace(spec,trial_path=bundle.trial))
        assert raw.times==saved.times
        assert [f.muscle_lengths_mm for f in raw.frames]==[f.muscle_lengths_mm for f in saved.frames]
        assert np.allclose([f.processed_axes for f in raw.frames],[f.processed_axes for f in saved.frames])
        assert raw.dynamic_profile['source']=='raw causal replay'
        assert any(f.analysis['dynamic']['profile_lag_s'] is not None for f in raw.frames)
        assert guard.attempts==0


def test_raw_trial_identity_checked(bundle,tmp_path):
    d=json.loads(bundle.trial.read_text());d['park_key']='fenrir'
    dest=tmp_path/'capture_trial.json';dest.write_text(json.dumps(d))
    raw=Path(bundle.trial.parent/d['files']['timing']);shutil.copy(raw,tmp_path/raw.name)
    with pytest.raises(ValueError,match='mismatch'):
        build_recording(ReviewSpec(bundle.profiles[0],bundle.capture,trial_path=dest))


def test_dynamic_sync_events_are_available_and_quantization_not_a_warning(bundle):
    r=build_recording(ReviewSpec(bundle.profiles[0],bundle.capture));frames=list(r.frames)
    f=frames[10];info=deepcopy_json(f.analysis);info['dynamic']['status']='lost_hold';info['dynamic']['profile_lag_s']=.6;info['dynamic']['fallback_active']=True
    frames[10]=replace(f,analysis=info)
    events=detect_anomalies(replace(r,frames=tuple(frames)))
    assert {'sync_lost','profile_lag','profile_fallback'} <= {e.category for e in events}


def deepcopy_json(value):
    # Analysis may contain numpy scalar values; deep copy does not serialize.
    from copy import deepcopy
    return deepcopy(value)


def test_generated_csvs_cannot_enter_raw_capture_picker():
    from tools.sim_player.video_sync import is_ride_telemetry_csv
    assert is_ride_telemetry_csv('Blackbird_repeat01.csv')
    for name in ('capture_dynamic_diagnostics.csv','capture_timing.csv','capture_polls.csv','index.csv','gain_curve.csv'):
        assert not is_ride_telemetry_csv(name)


def _method(path,cls_name,name,namespace=None):
    tree=ast.parse(path.read_text());cls=next(c for c in tree.body if isinstance(c,ast.ClassDef) and c.name==cls_name)
    method=next(m for m in cls.body if isinstance(m,ast.FunctionDef) and m.name==name)
    env=dict(namespace or {})
    exec(compile(ast.fix_missing_locations(ast.Module(body=[method],type_ignores=[])),str(path),'exec'),env)
    return env[name]


def test_switch_keeps_playhead_loop_and_uses_cached_side():
    method=_method(ROOT/'tools/sim_player/dynamic_workflow.py','DynamicProfileWorkflowMixin','_dynamic_switch')
    records=(object(),object());session=SimpleNamespace(active=0,recordings=records)
    # A simple property-bearing session like the production dataclass.
    class Session:
        active=0
        @property
        def recording(self):return records[self.active]
    s=Session();calls=[]
    ui=SimpleNamespace(_dynamic_session=s,profile_slot=SimpleNamespace(currentData=lambda:1),
        _review_busy=lambda:False,playing=True,current_s=25.,loop_range=(10,30),
        _set_playing=lambda b:calls.append(('play',b)),_apply_reanalysis=lambda r:calls.append(('apply',r)),
        _refresh_dynamic_view=lambda:calls.append(('refresh',None)))
    method(ui)
    assert s.active==1 and ui.current_s==25 and ui.loop_range==(10,30)
    assert calls==[('play',False),('apply',records[1]),('refresh',None),('play',True)]


@pytest.mark.parametrize('name',['_set_baseline','_restore_baseline','_reset_gains','_save_experiment','_load_experiment','save_variant_entry'])
def test_static_mutation_commands_guarded_in_dynamic_review(name):
    method=_method(ROOT/'tools/sim_player/app.py','ReplayWindow',name)
    calls=[];ui=SimpleNamespace(_dynamic_session=object(),_dynamic_readonly=lambda:calls.append('blocked') or True)
    method(ui);assert calls==['blocked']


def test_caching_dynamic_recording_cannot_pollute_static_cache():
    method=_method(ROOT/'tools/sim_player/app.py','ReplayWindow','_cache_recording')
    method(SimpleNamespace(),SimpleNamespace(dynamic_profile={'test':True}))


def test_cancel_requests_interruption_without_terminating_worker():
    method=_method(ROOT/'tools/sim_player/dynamic_workflow.py','DynamicProfileWorkflowMixin','_cancel_dynamic_load')
    calls=[];ui=SimpleNamespace(_dynamic_pending=True,worker=SimpleNamespace(requestInterruption=lambda:calls.append('cancel')),
        profile_cancel_button=SimpleNamespace(setEnabled=lambda x:calls.append(x)))
    method(ui);assert calls==['cancel',False]
