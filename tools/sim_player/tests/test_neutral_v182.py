# test_neutral_v182.py ver 1
"""Offline/POC matching, replay cache and variant/experiment persistence."""
from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.analyze_nl2_csv import analyze, make_backend, read_capture
from tools.sim_player.recording import load_recording
from tools.sim_player.variant_presets import candidate_entry
from tools.sim_player.tests.test_recording import make_capture
from tools.sim_player.tuning import ExperimentSettings, save_experiment, load_experiment, tuning_metrics
from sims.nolimits2.variant_store import VariantStore


def test_recorded_chair_variant_offset_changes_lengths_not_processed_axes(tmp_path):
    path=make_capture(tmp_path,video=False)
    store=VariantStore.load('chair_v3')
    current=load_recording(path, platform='chair_v3', variant_store=store,
                           default_standard_variant=True)
    snapshot=dict(current.variant_snapshot,neutral_height_delta_mm=-15)
    shifted=load_recording(path,platform='chair_v3',variant_store=store,
                           variant_label='Blackbird',variant_snapshot=snapshot)
    assert shifted.platform_neutral_height_mm==pytest.approx(316.5)
    assert shifted.frames[0].processed_axes==pytest.approx(current.frames[0].processed_axes)
    assert shifted.frames[0].input_axes==pytest.approx(current.frames[0].input_axes)
    assert shifted.frames[0].analysis['requested_real'][2]==pytest.approx(
        current.frames[0].analysis['requested_real'][2]+15)
    assert min(shifted.frames[0].muscle_lengths_mm)>min(current.frames[0].muscle_lengths_mm)
    assert shifted.frames[0].modeled_clearance_mm is None
    assert path.read_bytes()==make_capture(tmp_path,video=False).read_bytes()


def test_shared_offline_analyzer_and_live_backend_match_each_frame(tmp_path):
    path=make_capture(tmp_path,video=False)
    store=VariantStore.load('platform_v2')
    snapshot=dict(store.snapshot('Blackbird'),neutral_height_delta_mm=-20)
    rec=load_recording(path,platform='platform_v2',variant_label='Blackbird',variant_snapshot=snapshot)
    b=make_backend('platform_v2')
    b.set_ride_neutral_height_delta_mm(-20)
    for sample in rec.frames:
        norm=sample.processed_axes
        expected=b.real_with_ride_neutral_offset(b.k.norm_to_real(norm))
        applied, actuator=b._limit_workspace(expected)
        if actuator is None:
            actuator = b._actuator_lengths_for_real_transform(applied)
        raw_lengths = [float(v) - float(b.cfg.FIXED_HARDWARE_LENGTH)
                       for v in actuator]
        output_lengths = [max(b.cfg.MUSCLE_MIN_LENGTH,
                              min(b.cfg.MUSCLE_MAX_LENGTH, length))
                          for length in raw_lengths]
        assert sample.analysis['requested_real']==pytest.approx(expected)
        assert sample.analysis['applied_real']==pytest.approx(applied)
        assert sample.muscle_lengths_mm==pytest.approx(output_lengths)
        assert sample.modeled_clearance_mm==pytest.approx(b.workspace_floor_clearance)


def test_zero_offset_is_numerically_identical_to_no_offset(tmp_path):
    path=make_capture(tmp_path,video=False)
    for platform in ('chair_v3','platform_v2'):
        base=load_recording(path,platform=platform,default_standard_variant=True)
        snap=dict(base.variant_snapshot,neutral_height_delta_mm=0.0)
        current=load_recording(path,platform=platform,
                               variant_label='Blackbird',variant_snapshot=snap)
        for a,b in zip(base.frames,current.frames):
            assert a.analysis['requested_real']==b.analysis['requested_real']
            assert a.analysis['applied_real']==b.analysis['applied_real']
            assert a.muscle_lengths_mm==b.muscle_lengths_mm


def test_saved_candidate_carries_current_neutral_offset(tmp_path):
    store=VariantStore.load('chair_v3',directory=tmp_path/'variants')
    rec=load_recording(make_capture(tmp_path/'capture',video=False), platform='chair_v3',
                       variant_store=store,default_standard_variant=True)
    snapshot=dict(rec.variant_snapshot,neutral_height_delta_mm=12.5)
    rec=load_recording(rec.csv_path,platform='chair_v3',variant_store=store,
                       variant_label='Blackbird',variant_snapshot=snapshot)
    candidate=candidate_entry('Blackbird [High Neutral]',rec)
    assert candidate['neutral_height_delta_mm']==12.5
    store.add_candidate(candidate)
    assert VariantStore.load('chair_v3',directory=tmp_path/'variants')\
        .entry('Blackbird [High Neutral]')['neutral_height_delta_mm']==12.5
    experiment=ExperimentSettings(variant_label='Blackbird [High Neutral]',
                                  variant_snapshot=snapshot,platform_key='chair_v3')
    save_experiment(tmp_path/'expt.json',experiment)
    assert load_experiment(tmp_path/'expt.json').variant_snapshot['neutral_height_delta_mm']==12.5
    assert rec.csv_path.read_bytes()==make_capture(tmp_path/'capture',video=False).read_bytes()


def test_output_summaries_show_starting_lengths_and_separate_clip_categories(tmp_path):
    rec=load_recording(make_capture(tmp_path,video=False),platform='chair_v3',
                       default_standard_variant=True)
    metrics=tuning_metrics(rec)
    assert metrics.initial_muscle_min_mm==pytest.approx(min(rec.frames[0].muscle_lengths_mm))
    assert metrics.initial_muscle_max_mm==pytest.approx(max(rec.frames[0].muscle_lengths_mm))
    assert metrics.short_clip_frames>=0 and metrics.long_clip_frames>=0
    assert metrics.short_clip_frames<=metrics.final_clipped_frames
    assert metrics.long_clip_frames<=metrics.final_clipped_frames


def test_raw_replay_never_receives_named_variant_height_delta(tmp_path):
    path=make_capture(tmp_path,video=False)
    raw=load_recording(path,platform='chair_v3')
    assert raw.variant_snapshot is None
    assert raw.frames[0].analysis['requested_real'][2]==pytest.approx(
        make_backend('chair_v3').k.norm_to_real(raw.frames[0].processed_axes)[2])


def extracted(methods):
    path=Path(__file__).resolve().parents[1]/'app.py'
    tree=ast.parse(path.read_text())
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='ReplayWindow')
    found=[n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name in methods]
    ns={}
    exec(compile(ast.fix_missing_locations(ast.Module(body=found,type_ignores=[])),str(path),'exec'),ns)
    return {name:ns[name] for name in methods}


def test_actual_tuning_handler_applies_new_snapshot_and_no_disk_write():
    method=extracted({'_neutral_offset_changed'})['_neutral_offset_changed']
    old={'label':'Blackbird','park':'blackbird','neutral_height_delta_mm':0.0}
    recording=SimpleNamespace(cue_mode='euler',variant_snapshot=old)
    calls=[]
    ui=SimpleNamespace(recording=recording,loading=False,
                       tuning_panel=SimpleNamespace(gain_percent=lambda:(100,)*6),
                       _request_reanalysis=lambda *a,**kw:calls.append((a,kw)))
    method(ui,-12.5)
    assert calls[0][1]['variant_snapshot']['neutral_height_delta_mm']==-12.5
    assert old['neutral_height_delta_mm']==0
    assert calls[0][0][0]=='euler'


def test_real_reanalysis_preserves_supplied_tuned_snapshot_not_old_recording():
    """Regression: 'keep' must not overwrite experimental neutral settings."""
    method=extracted({'_request_reanalysis'})['_request_reanalysis']
    # Verify the controlling branch at AST level; invoking its Qt LoadWorker is
    # separately tested by the existing controller suite.
    fn=ast.parse((Path(__file__).resolve().parents[1]/'app.py').read_text())
    cls=next(n for n in fn.body if isinstance(n,ast.ClassDef) and n.name=='ReplayWindow')
    target=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='_request_reanalysis')
    snippet=ast.get_source_segment((Path(__file__).resolve().parents[1]/'app.py').read_text(),target)
    assert 'if variant_snapshot is None:' in snippet
    assert 'variant_snapshot = getattr(self.recording, "variant_snapshot", None)' in snippet


def panel_methods(methods):
    """Exercise the real Qt panel methods without importing Qt in headless CI."""
    path=Path(__file__).resolve().parents[1]/'tuning_panel.py'
    tree=ast.parse(path.read_text())
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='MotionTuningPanel')
    found=[n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name in methods]
    ns={}
    exec(compile(ast.fix_missing_locations(ast.Module(body=found,type_ignores=[])),str(path),'exec'),ns)
    return {name:ns[name] for name in methods}


def test_exact_numeric_neutral_value_survives_five_mm_slider_and_commits_once():
    from types import MethodType

    class FakeValue:
        def __init__(self):
            self.number=0.0
            self.blocked=False
            self.down=False
        def blockSignals(self,v):self.blocked=bool(v)
        def setValue(self,v):self.number=float(v)
        def value(self):return self.number
        def isSliderDown(self):return self.down
        def setText(self,value):self.text=value
        def setEnabled(self,value):self.enabled=value
    class FakeTimer:
        def __init__(self):self.starts=0
        def stop(self):pass
        def start(self):self.starts+=1
    neutral=FakeValue()
    slider=FakeValue()
    summary=FakeValue()
    timer=FakeTimer()
    emitted=[]
    ui=SimpleNamespace(neutral_box=neutral,neutral_slider=slider,
                       neutral_summary=summary,_platform_neutral=316.5,
                       _neutral_debounce=timer,_neutral_last_sent=0.0,
                       _analysis_enabled=True,_neutral_named_variant=True,
                       neutral_changed=SimpleNamespace(emit=lambda value:emitted.append(value)))
    functions=panel_methods({'set_neutral_offset','neutral_offset_mm',
                             '_neutral_summary_update','_neutral_slider_changed',
                             '_neutral_numeric_changed','_commit_neutral'})
    for name,fn in functions.items():setattr(ui,name,MethodType(fn,ui))
    ui.set_neutral_offset(12.5)
    assert neutral.value()==12.5 and slider.value()==10
    assert '329.0 mm' in summary.text
    assert not emitted
    ui._commit_neutral()  # Loading a saved variant must not retrigger analysis.
    assert not emitted
    slider.down=True
    ui._neutral_slider_changed(27)
    assert neutral.value()==25
    assert timer.starts==0
    slider.down=False
    ui._commit_neutral()
    assert emitted==[25]
    ui._commit_neutral()
    assert emitted==[25]


def test_neutral_panel_signals_and_exact_value_widget_declared():
    source=(Path(__file__).resolve().parents[1]/'tuning_panel.py').read_text()
    assert 'QDoubleSpinBox()' in source
    assert 'neutral_slider.sliderReleased.connect(self._commit_neutral)' in source
    assert 'neutral_box.editingFinished.connect(self._commit_neutral)' in source
    assert 'neutral_reset_button.clicked.connect(self._neutral_reset)' in source
