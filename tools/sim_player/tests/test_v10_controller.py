# test_v10_controller.py ver 1
"""Headless controller seam tests for single marker lane and Examine workflow."""
import ast
from pathlib import Path
from types import SimpleNamespace
from tools.sim_player.anomaly_detection import AnomalyEvent, AnomalyThresholds, detect_anomalies
from tools.sim_player.loop_range import PlaybackRange

APP = Path(__file__).resolve().parents[1] / 'app.py'
METHODS = {'_refresh_marker_lane', '_marker_mode_changed', '_marker_selected',
           '_examine_anomaly', '_refresh_anomalies', '_anomaly_thresholds_changed'}


def extract():
    module = ast.parse(APP.read_text(encoding='utf-8'))
    cls = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == 'ReplayWindow')
    selected = [node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in METHODS]
    assert {node.name for node in selected} == METHODS
    namespace = {'AnomalyThresholds': AnomalyThresholds, 'AnomalyEvent': AnomalyEvent,
                 'detect_anomalies': detect_anomalies,
                 'clock_label': lambda x: f'{x:.2f}'}
    exec(compile(ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[])), str(APP), 'exec'),namespace)
    return {name: namespace[name] for name in METHODS}


class FakeLabel:
    def __init__(self): self.label = ''
    def setText(self,s): self.label=s


class FakeCombo:
    def __init__(self): self.mode='ride'
    def currentData(self): return self.mode


class FakeSpin:
    def __init__(self,value): self.value_=value
    def value(self): return self.value_


class FakePanel:
    def __init__(self):
        self.step_box=FakeSpin(50); self.floor_box=FakeSpin(30)
        self.events=(); self.selection=None
    def set_events(self,events): self.events=events
    def select_event(self,e): self.selection=e


class FakeWindow:
    def __init__(self):
        self.recording=SimpleNamespace(duration=10,events=(SimpleNamespace(seconds=1,name='Departed'),))
        self.anomalies=()
        self.anomaly_panel=FakePanel()
        self.markers=SimpleNamespace(set_recording=lambda events,duration: setattr(self,'displayed',(events,duration)))
        self.marker_mode_box=FakeCombo(); self.marker_title=FakeLabel()
        self.loop_range=PlaybackRange(duration=10)
        self.seeks=[]; self.play_states=[]; self.refreshes=0; self.status=[]
        for name,func in extract().items(): setattr(self,name,func.__get__(self))
    def _update_tuning_feedback(self): pass  # v11 adds feedback when thresholds change
    def _set_playing(self,active): self.play_states.append(active)
    def _refresh_range_controls(self): self.refreshes+=1
    def seek_seconds(self,seconds,**kwargs): self.seeks.append(seconds)
    def statusBar(self): return SimpleNamespace(showMessage=lambda message,*args: self.status.append(message))


def event():
    return AnomalyEvent(category='muscle_step',start_s=5,end_s=5.05,seconds=5.05,
                        name='Large muscle step',color='#f00',details='M1',
                        muscles='M1',magnitude=89,units='mm',frame_count=1,severity='alert')


def test_marker_category_switch_displays_exactly_one_lane():
    app=FakeWindow(); ev=event(); app.anomalies=(ev,)
    app._refresh_marker_lane()
    assert app.displayed[0] == app.recording.events
    app.marker_mode_box.mode='kinematic';app._marker_mode_changed()
    assert app.displayed[0] == (ev,)
    assert 'KINEMATIC' in app.marker_title.label
    app._marker_selected(ev)
    assert app.anomaly_panel.selection == ev
    app.marker_mode_box.mode='ride'; app._marker_mode_changed()
    assert app.displayed[0] == app.recording.events


def test_examine_loops_event_with_context_without_new_processing():
    app=FakeWindow(); ev=event(); app.anomalies=(ev,)
    app._examine_anomaly(ev)
    assert (app.loop_range.start,app.loop_range.end)==(4,6.05)
    assert app.loop_range.mode=='loop'
    assert app.play_states==[False, True]
    assert app.seeks==[4]
    assert app.refreshes==1
    app.anomalies=()
    app._examine_anomaly(ev)
    assert app.seeks==[4]


def test_threshold_update_handles_empty_recording():
    app=FakeWindow(); app.recording=None
    app.marker_mode_box.mode='kinematic'
    app._anomaly_thresholds_changed(50,30)
    assert app.anomaly_panel.events == ()
    assert app.displayed == ((),1.0)


def test_threshold_rebuilds_event_list_from_precomputed_data():
    from tools.sim_player.recording import PlaybackFrame
    app=FakeWindow()
    def pf(t,l):
        return PlaybackFrame(seconds=t, input_axes=(0,)*6,source_axes=(0,)*6,
             processed_axes=(0,)*6, muscle_lengths_mm=(l,800,800,800,800,800),
             workspace_limited=False,modeled_clearance_mm=95,
             analysis={"requested":(l,800,800,800,800,800),"workspace_lengths":(l,800,800,800,800,800)})
    app.recording=SimpleNamespace(duration=.05,events=(),frames=(pf(0,800),pf(.05,889)),
            min_length_mm=750,max_length_mm=1000)
    app.marker_mode_box.mode='kinematic'
    app._refresh_anomalies()
    assert len(app.anomalies)==1 and app.anomalies[0].category=='muscle_step'
    assert app.displayed[0]==app.anomalies
    app.anomaly_panel.step_box.value_=100
    app._anomaly_thresholds_changed(100,30)
    assert app.anomalies == ()
    assert app.displayed[0] == ()
