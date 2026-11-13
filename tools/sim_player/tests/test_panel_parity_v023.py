"""Inspector/Advanced parity and pre-refactor behaviours, without Qt.

Executes the actual controller/panel methods against narrow UI test doubles.
The optional sibling Qt suite renders the real widgets; these are NOT GUI tests.
"""
from __future__ import annotations
import ast
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
from copy import deepcopy
import pytest

from tools.sim_player.recording import RideRecording, PlaybackFrame, RideEvent
from tools.sim_player.muscle_extrema import MuscleExtrema, RelativeMuscleRuler, plot_bounds
from tools.sim_player.panel_state import (
    muscle_bar_state, transform_readout, comparison_pair, candidate_label, event_description,
)
from tools.sim_player.tuning import muscle_flags
from tools.sim_player.anomaly_detection import AnomalyEvent
from tools.sim_player.task_workspace_models import plot_series, plot_unit, plot_guides

PLAYER = Path(__file__).resolve().parents[1]


def methods(file, cls, names, env=None):
    tree=ast.parse((PLAYER/file).read_text())
    klass=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==cls)
    nodes=[n for n in klass.body if isinstance(n,ast.FunctionDef) and n.name in names]
    assert len(nodes)==len(names)
    mod=ast.Module(body=nodes,type_ignores=[])
    namespace=dict(env or {})
    exec(compile(ast.fix_missing_locations(mod),str(PLAYER/file),'exec'),namespace)
    return type('ActualMethods',(),{name:namespace[name] for name in names})


class Value:
    def __init__(self,value=None): self.val=value;self.enabled=True;self.visible=True
    def value(self): return self.val
    def setValue(self,v): self.val=v
    def text(self): return self.val
    def setText(self,v): self.val=v
    def setEnabled(self,v): self.enabled=v
    def isEnabled(self): return self.enabled
    def setVisible(self,v): self.visible=v
    def clear(self): self.val=''
    def setWindowTitle(self,v): self.val=v
    def blockSignals(self,v): pass
    def isSliderDown(self): return False

class Signal:
    def __init__(self): self.calls=[]
    def emit(self,*args): self.calls.append(args)

class Timer:
    def __init__(self): self.running=False
    def start(self): self.running=True
    def stop(self): self.running=False

class Bars:
    def update_values(self,*args,**kwargs): self.args=args;self.kwargs=kwargs
    def clear_extrema(self): self.cleared=True


def frame(t,request, permitted=None,workspace=None,mask=None,**extra):
    permitted=tuple(permitted if permitted is not None else request)
    info=dict(requested=tuple(request), workspace_lengths=tuple(workspace if workspace is not None else permitted))
    if mask is not None: info['final_clip_mask']=tuple(mask)
    info.update(extra)
    return PlaybackFrame(t,(.2,)*6,(.3,)*6,(.4,)*6,permitted,False,25.,info)


def recording(platform='chair_v3'):
    lo,hi=(600.,800.) if platform=='chair_v3' else (750.,1000.)
    mid=(lo+hi)/2
    a=(mid,)*6
    bad=(lo-10,hi+20,mid,mid,mid,mid)
    out=(lo,hi,mid,mid,mid,mid)
    frames=(frame(0.,a),frame(.05,bad,out,workspace=out,mask=(False,)*6),frame(.1,a))
    return RideRecording(Path('capture.csv'),'Blackbird',frames,tuple(f.seconds for f in frames),(),
        None,None,0.,'No video',lo,hi,'test','test',platform_key=platform,
        variant_label='A settings',variant_snapshot={'neutral_height_delta_mm':3.},
        platform_neutral_height_mm=316.5,global_settings_snapshot={
            'washout_type':'classical','enabled':[True,True,False,False,False,False],
            'tau':[1.2]*6})


@pytest.mark.parametrize('platform',['chair_v3','platform_v2'])
def test_same_snapshot_delivers_all_original_diagnostics_to_both_bar_widgets(platform):
    r=recording(platform); history=MuscleExtrema();history.advance(r.frames,0);history.advance(r.frames,1)
    state=muscle_bar_state(r,r.frames[1],extrema=history.extrema(),bounds=plot_bounds(r))
    advanced=Bars();inspector=Bars();state.apply(advanced);state.apply(inspector)
    assert (advanced.args,advanced.kwargs)==(inspector.args,inspector.kwargs)
    assert state.request_oob==(True,True,False,False,False,False)
    assert not any(state.final_clipped)  # request failed but workspace correction succeeded
    assert state.extrema[0][0]==r.min_length_mm-10
    assert state.extrema[1][1]==r.max_length_mm+20
    assert state.requested!=state.values
    assert state.plot_bounds==plot_bounds(r)


def test_final_clamp_flags_are_not_derived_from_rounded_output():
    r=recording(); f=frame(0.,(700.1,)*6,(700.,)*6,workspace=(700.1,)*6,mask=(False,)*6)
    assert not any(muscle_bar_state(r,f).final_clipped)
    f=frame(0.,(590.,)*6,(600.,)*6,workspace=(590.,)*6,mask=(True,)*6)
    assert all(muscle_bar_state(r,f).final_clipped)


def test_invalid_pre_roll_does_not_acquire_new_false_error_flags():
    r=recording(); f=replace(r.frames[1],analysis={**r.frames[1].analysis,'valid':False})
    state=muscle_bar_state(r,f)
    assert not any(state.request_oob) and not any(state.final_clipped)


def test_extreme_markers_persist_after_recovery_backwards_seek_and_loop():
    r=recording();h=MuscleExtrema();h.advance(r.frames,0);h.advance(r.frames,2)
    peaks=h.extrema();bounds=plot_bounds(r)
    for i in (0,1,0,2):
        h.advance(r.frames,i)
        state=muscle_bar_state(r,r.frames[i],extrema=h.extrema(),bounds=bounds)
        assert state.extrema==peaks and state.plot_bounds==bounds
    recovered=muscle_bar_state(r,r.frames[2],extrema=h.extrema(),bounds=bounds)
    assert not any(recovered.request_oob)
    assert recovered.extrema[0][0] < recovered.lo


def test_fixed_limit_positions_and_50mm_scale_remain_platform_independent():
    for key in ('chair_v3','platform_v2'):
        r=recording(key);lo,hi=r.min_length_mm,r.max_length_mm
        ruler=RelativeMuscleRuler(lo,hi,*plot_bounds(r))
        assert ruler.position(lo)==pytest.approx(.12)
        assert ruler.position(hi)==pytest.approx(.88)
        assert ruler.position(lo-10)<.12 and ruler.position(hi+20)>.88
        assert ruler.position(lo+50)-ruler.position(lo)==pytest.approx(.76*50/(hi-lo))


def test_inspector_update_uses_controller_snapshot_and_no_history_of_its_own():
    C=methods('task_workspace.py','TaskInspector',['_set_comparison','set_muscle_state'],
              {'comparison_pair':comparison_pair,'candidate_label':candidate_label})
    panel=C();panel.muscle_readout=Bars();r=recording(); h=MuscleExtrema()
    h.advance(r.frames,0);h.advance(r.frames,2)
    state=muscle_bar_state(r,r.frames[2],extrema=h.extrema(),bounds=plot_bounds(r))
    panel.set_muscle_state(state)
    assert panel.muscle_readout.kwargs['extrema']==h.extrema()
    assert not hasattr(panel,'muscle_extrema')


def test_controller_display_feeds_same_flags_extrema_and_transform_to_both_views():
    C=methods('app.py','ReplayWindow',['_display_current'],{
        'muscle_bar_state':muscle_bar_state,'transform_readout':transform_readout,'clock_label':str})
    w=C();w.recording=recording();w.current_s=.05
    w.axis_bars=Bars();w.muscle_bars=Bars();w.muscle_extrema=MuscleExtrema()
    w.muscle_extrema.advance(w.recording.frames,0);w.muscle_plot_bounds=plot_bounds(w.recording)
    w.panels={'axes':Value()};w.raw_axes_action=NS(isChecked=lambda:True)
    inspector_axis=Bars();inspector_muscle=Bars()
    w.workspace_inspector=NS(set_transform_state=lambda context,v: (setattr(inspector_axis,'axis_context',context),inspector_axis.update_values(v)),
        set_muscle_state=lambda state:state.apply(inspector_muscle),set_current_time=lambda t:None)
    w.markers=NS(set_current=lambda t:None);w.position_label=Value();w.slider=Value();w.info_badge=Value()
    w.plot_canvas=w.overlay_plot=NS(set_current_time=lambda t:None)
    w._display_dynamic_frame=lambda f:None
    w._display_current()
    assert w.muscle_bars.args==inspector_muscle.args
    assert w.muscle_bars.kwargs==inspector_muscle.kwargs
    assert w.axis_bars.args==inspector_axis.args
    assert w.axis_bars.axis_context==inspector_axis.axis_context=='RAW CSV'
    assert inspector_muscle.kwargs['extrema'][0][0]==590


def test_reset_clears_markers_in_both_views():
    C=methods('app.py','ReplayWindow',['_reset_muscle_extrema'],{'plot_bounds':plot_bounds})
    w=C();w.recording=recording();w.muscle_extrema=MuscleExtrema()
    w.muscle_extrema.advance(w.recording.frames,0);w.muscle_extrema.advance(w.recording.frames,2)
    w.muscle_bars=Bars();w.workspace_inspector=NS(muscle_readout=Bars())
    w._reset_muscle_extrema()
    assert w.muscle_extrema.extrema() is None
    assert w.muscle_bars.cleared and w.workspace_inspector.muscle_readout.cleared


@pytest.mark.parametrize('dynamic',[False,True])
@pytest.mark.parametrize('raw',[False,True])
def test_raw_processed_readout_uses_correct_values_and_names(dynamic,raw):
    r=recording();r=replace(r,dynamic_profile={'label':'dynamic'} if dynamic else None)
    f=r.frames[0];context,values=transform_readout(r,f,raw)
    assert values==(f.source_axes if raw else f.processed_axes)
    assert context==(('BASE POST-WASHOUT' if raw else 'PROFILE REQUEST') if dynamic else
                    ('RAW CSV' if raw else 'PROCESSED'))


def test_ab_identity_remains_stable_while_b_is_active():
    a=recording();b=replace(a,variant_label='B settings')
    assert comparison_pair(b,a,'B')==(a,b)
    assert comparison_pair(a,b,'A')==(a,b)
    assert candidate_label(replace(a,dynamic_profile={'label':'strict 01'}))=='strict 01'


def event():
    return AnomalyEvent('requested_range',1.,3.,2.,'Requested outside range','orange',
                        'M1 requests 590 mm','M1',10.,'mm',41,'warning')


def test_event_details_retain_duration_peak_and_original_explanation():
    text=event_description(event())
    assert '1.00-3.00' in text and '(2.00 s)' in text and 'Peak at 2.00' in text
    assert 'M1 requests 590 mm' in text
    assert 'Dispatch' in event_description(RideEvent(0.,'Dispatch','blue','confirmed'))


def test_inspector_examine_routes_original_anomaly_object_not_only_time():
    qt=NS(ItemDataRole=NS(UserRole=1))
    C=methods('task_workspace.py','TaskInspector',['_examine_selected','_event_activated'],{'Qt':qt})
    p=C();e=event();item=NS(data=lambda role:e)
    p.event_list=NS(currentItem=lambda:item);p.examine_requested=Signal();p.seek_requested=Signal()
    p._examine_selected();p._event_activated(item)
    assert p.examine_requested.calls==[(e,)]
    assert p.seek_requested.calls==[(2.,)]


def tuning_panel():
    C=methods('task_workspace.py','TaskInspector',['_gain_value_changed','_commit_gains','_reset_gains','_commit_neutral'])
    p=C();p.recording=recording();p._syncing=False;p._busy=False;p._gain_debounce=Timer()
    p.gain_sliders=[Value(100) for _ in range(6)];p.gain_labels=[Value('100%') for _ in range(6)]
    p._last_sent_gains=(100.,)*6;p.gain_commit_requested=Signal()
    p._last_sent_neutral=3.;p.neutral_box=Value(3.);p.neutral_commit_requested=Signal()
    return p


def test_keyboard_changes_commit_once_after_debounce_without_slider_release():
    p=tuning_panel();p.gain_sliders[0].setValue(125);p._gain_value_changed(0,125)
    assert p._gain_debounce.running and not p.gain_commit_requested.calls
    p._commit_gains();p._commit_gains()
    assert p.gain_commit_requested.calls==[((125.,100.,100.,100.,100.,100.),)]
    assert not p._gain_debounce.running


def test_drag_waits_for_release_and_busy_worker_cannot_receive_another_edit():
    p=tuning_panel();p.gain_sliders[0].setValue(130);p.gain_sliders[0].isSliderDown=lambda:True
    p._gain_value_changed(0,130);assert not p._gain_debounce.running
    p._busy=True;p._commit_gains();assert not p.gain_commit_requested.calls
    p._busy=False;p.gain_sliders[0].isSliderDown=lambda:False;p._commit_gains()
    assert len(p.gain_commit_requested.calls)==1


def test_dynamic_profiles_are_still_readonly():
    p=tuning_panel();p.recording=replace(p.recording,dynamic_profile={'label':'locked'})
    p.gain_sliders[0].setValue(200);p._commit_gains()
    p.neutral_box.setValue(20);p._commit_neutral()
    assert not p.gain_commit_requested.calls and not p.neutral_commit_requested.calls


def test_neutral_commit_ignores_unchanged_and_busy_focus_loss():
    p=tuning_panel();p._commit_neutral();assert not p.neutral_commit_requested.calls
    p.neutral_box.setValue(8.);p._busy=True;p._commit_neutral();assert not p.neutral_commit_requested.calls
    p._busy=False;p._commit_neutral();p._commit_neutral();assert p.neutral_commit_requested.calls==[(8.,)]


def test_restore_baseline_restores_washout_snapshot_as_well_as_cue_and_gains():
    C=methods('app.py','ReplayWindow',['_restore_baseline'])
    w=C();w._dynamic_session=None;w._baseline_recording=recording();w.loading=False
    calls=[];w._apply_experiment_values=lambda *args,**kw:calls.append((args,kw))
    w._restore_baseline()
    assert calls[0][1]['global_settings']==w._baseline_recording.global_settings_snapshot


def test_plot_labels_do_not_call_dynamic_post_gain_motion_raw_or_filter_only():
    r=recording();r=replace(r,dynamic_profile={'label':'test'})
    before,after=plot_series(r,'source_vs_processed_surge')
    assert before[0]=='Base post-washout Surge' and after[0]=='Profile request Surge'
    assert before[1]==tuple(f.source_axes[0] for f in r.frames)
    assert after[1]==tuple(f.processed_axes[0] for f in r.frames)
    assert plot_unit('profile_gains')==(100.,'%') and plot_guides(r,'profile_gains')==((1.,'Unity'),)
    assert plot_unit('m1')==(1.,'mm')
    assert plot_guides(r,'all_muscles')==((600.,'Min'),(800.,'Max'))


def test_task_inspector_constructs_original_renderers_not_progress_bars():
    tree=ast.parse((PLAYER/'task_workspace.py').read_text())
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='TaskInspector')
    builder=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='_make_review_page')
    calls=[n for n in ast.walk(builder) if isinstance(n,ast.Call)]
    assert not any(isinstance(n.func,ast.Name) and n.func.id=='QProgressBar' for n in calls)
    bars=[n.args[0].value for n in calls if isinstance(n.func,ast.Name) and n.func.id=='ResponsiveBars']
    assert bars==[True,False]


def test_existing_actions_are_routed_to_the_original_controller_methods():
    source=(PLAYER/'player_ui.py').read_text()
    for signal,handler in [('examine_requested','_examine_anomaly'),('baseline_requested','_set_baseline'),
        ('restore_baseline_requested','_restore_baseline'),('save_experiment_requested','_save_experiment'),
        ('load_experiment_requested','_load_experiment')]:
        assert f'self.workspace_inspector.{signal}.connect(self.{handler})' in source

class Table:
    def __init__(self,columns): self.columns=columns;self.cells=[[Value('-') for _ in range(columns)] for _ in range(6)];self.visible=True
    def item(self,r,c):return self.cells[r][c]
    def columnCount(self):return self.columns
    def setHorizontalHeaderLabels(self,labels):self.labels=labels
    def setVisible(self,v):self.visible=v


def readout_inspector(r):
    C=methods('task_workspace.py','TaskInspector',['set_current_time'],{'comparison_pair':comparison_pair})
    p=C();p.recording=r;p._active_slot='A';p._comparison=None
    p.motion_table=Table(4);p.compare_table=Table(3);p.dynamic_table=Table(3)
    p.motion_note=Value();p.dynamic_status=Value()
    return p


def test_b_active_table_values_remain_in_b_column():
    a=recording();b=replace(a,frames=tuple(replace(f,processed_axes=(.9,)*6) for f in a.frames),variant_label='B')
    p=readout_inspector(b);p._active_slot='B';p._comparison=a
    p.set_current_time(.05)
    assert p.compare_table.item(0,1).text()=='+40.0%'
    assert p.compare_table.item(0,2).text()=='+90.0%'


def test_dynamic_readout_reports_actual_applied_vs_curve_and_missing_metrics():
    r=recording();d=dict(multipliers=(.8,)*6,profile_multipliers=(1.2,)*6,
        ride_time_s=2.,phase_s=1.8,status='stationary_time',fallback_active=True,
        profile_lag_s=None,position_residual_m=None)
    r=replace(r,dynamic_profile={'label':'Dynamic'},frames=tuple(
        replace(f,analysis={**f.analysis,'dynamic':d}) for f in r.frames))
    p=readout_inspector(r);p.set_current_time(.1)
    assert p.motion_table.labels==['DOF','Base filtered','Applied','Request']
    assert p.dynamic_table.item(0,1).text()=='80.0%'
    assert p.dynamic_table.item(0,2).text()=='120.0%'
    assert 'not recorded' in p.dynamic_status.text()
    assert 'Fallback/blend active' in p.dynamic_status.text()


def test_dynamic_readout_preserves_recorded_lag_and_residual_units():
    r=recording();d=dict(multipliers=(1.,)*6,profile_multipliers=(1.,)*6,
        ride_time_s=2.,phase_s=1.8,status='xyz',fallback_active=False,
        profile_lag_s=.025,position_residual_m=.003)
    r=replace(r,dynamic_profile={'label':'Dynamic'},frames=tuple(
        replace(f,analysis={**f.analysis,'dynamic':d}) for f in r.frames))
    p=readout_inspector(r);p.set_current_time(.1)
    assert '+25.0 ms' in p.dynamic_status.text() and '3.0 mm' in p.dynamic_status.text()


def test_event_list_does_not_drop_after_90_items():
    class Item:
        def __init__(self,text):self.text=text
        def setData(self,role,data):self.value=data
        def setToolTip(self,text):self.tip=text
    class List:
        def __init__(self):self.items=[]
        def currentItem(self):return None
        def clear(self):self.items=[]
        def addItem(self,item):self.items.append(item)
    C=methods('task_workspace.py','TaskInspector',['_refresh_events'],{
        'QListWidgetItem':Item,'Qt':NS(ItemDataRole=NS(UserRole=1)),'event_description':event_description})
    p=C();p.recording=recording();p.anomalies=tuple(replace(event(),seconds=i) for i in range(120))
    p.event_list=List();p.event_summary=Value();p.event_detail=Value();p.examine_button=Value()
    p._refresh_events()
    assert len(p.event_list.items)==120
    assert all('Peak at' in item.tip for item in p.event_list.items)
    assert p.event_list.items[-1].value is p.anomalies[-1]


def test_plot_comparison_bounds_include_both_curves_without_altering_data():
    from tools.sim_player.task_workspace_models import plot_guides
    C=methods('task_workspace.py','PlotCanvas',['set_comparison','_recompute'],{
        'plot_series':plot_series,'plot_guides':plot_guides})
    p=C();p.recording=recording();p.comparison=None;p.plot_key='all_axes';p.update=lambda:None
    b=replace(p.recording,frames=tuple(replace(f,processed_axes=(1.8,)*6) for f in p.recording.frames))
    before=deepcopy(b)
    p.set_comparison(b)
    assert len(p._comparison_series)==6 and p._bounds[1]>1.8
    assert b==before
