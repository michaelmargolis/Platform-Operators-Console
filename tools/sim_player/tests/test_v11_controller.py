# test_v11_controller.py ver 2
"""Qt-independent tests of v11 controller using actual extracted methods."""
from __future__ import annotations
import ast
import json
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace

from tools.sim_player.tuning import DEFAULT_GAINS, validate_gain_percent
from tools.sim_player.angular_cues import MODE_LABELS
from tools.sim_player.platform_selection import platform_metadata

APP = Path(__file__).resolve().parents[1] / 'app.py'
METHODS = ('_cache_recording', '_request_reanalysis', '_apply_reanalysis', '_reset_gains')


def extract():
    tree = ast.parse(APP.read_text())
    cls = next(c for c in tree.body if isinstance(c, ast.ClassDef) and c.name == 'ReplayWindow')
    methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in METHODS]
    assert {m.name for m in methods} == set(METHODS)
    namespace = {'validate_gain_percent': validate_gain_percent, 'DEFAULT_GAINS': DEFAULT_GAINS,
                 'MODE_LABELS': MODE_LABELS, 'platform_metadata': platform_metadata}
    exec(compile(ast.fix_missing_locations(ast.Module(body=methods, type_ignores=[])),str(APP),'exec'),namespace)
    return {method: namespace[method] for method in METHODS}


class FakeUI:
    def __init__(self):
        funcs=extract()
        for k, value in funcs.items():
            setattr(self,k,value.__get__(self))
        self.recording=SimpleNamespace(cue_mode='euler',gain_percent=tuple(float(x) for x in DEFAULT_GAINS),
                                      duration=30.)
        self.platform_key="platform_v2"
        self.worker=None
        self.recording.platform_key=self.platform_key
        self.recording.platform_config_sha256=platform_metadata(self.platform_key)["platform_config_sha256"]
        self.loading=False
        self.current_s=12.5
        self.gain_percent=DEFAULT_GAINS
        self._mode_cache=OrderedDict()
        self._variant_cache_token=lambda ride: json.dumps({"platform":getattr(ride, "platform_key", "platform_v2"),
              "geometry":platform_metadata(getattr(ride,"platform_key","platform_v2"))["platform_config_sha256"],
              "variant":getattr(ride,"variant_snapshot",None)},sort_keys=True)
        self._sync_platform_selector=lambda: None
        self._refresh_baseline_label=lambda: None
        self._fill_variant_choices=lambda ride: None
        self._sync_neutral_panel=lambda ride: None
        self.stopped=[]; self.loaded=[]; self.video_calls=[]; self.sent=[]
        self.tuning_panel=SimpleNamespace(set_gains=lambda gains: setattr(self,'shown_gains',gains),
                                         set_analysis_enabled=lambda flag: self.loaded.append(flag))
        self.cue_box=SimpleNamespace(setEnabled=lambda flag: self.loaded.append(flag))
        self.statusBar=lambda: SimpleNamespace(showMessage=lambda *args: None)
        self._set_playing=lambda active: self.stopped.append(active)
        self._refresh_cue_comparison=lambda: None
        self._refresh_anomalies=lambda: None
        self._reset_muscle_extrema=lambda: None
        self._update_tuning_feedback=lambda: None
        self._display_current=lambda: None
        self._send_twin=lambda force: self.sent.append(force)


def test_cached_reanalysis_uses_both_mode_and_six_gains_without_video_seek():
    ui=FakeUI()
    old=ui.recording
    alt=SimpleNamespace(cue_mode='euler',gain_percent=(100.,100.,100.,150.,100.,100.),duration=30,analysis_settings='test', platform_key='platform_v2')
    ui._cache_recording(old)
    ui._cache_recording(alt)
    ui._request_reanalysis('euler',alt.gain_percent)
    assert ui.recording is alt
    assert ui.current_s==12.5
    assert ui.stopped==[False]
    assert ui.sent==[True]
    assert not ui.video_calls


def test_cached_reanalysis_is_bounded_to_five_variants():
    ui=FakeUI()
    for g in (100,110,120,130,140,150,160):
        ui._cache_recording(SimpleNamespace(cue_mode='euler',gain_percent=(100.,100.,100.,float(g),100.,100.)))
    assert len(ui._mode_cache)==5
    assert ('euler',(100.,100.,100.,100.,100.,100.),'null') not in ui._mode_cache


def test_reset_gains_preserves_selected_angular_cue():
    ui=FakeUI()
    ui.recording=SimpleNamespace(cue_mode='gravity',gain_percent=(110,)*6,duration=30.)
    received=[]
    ui._apply_experiment_values=lambda mode,gains: received.append((mode,gains))
    ui._reset_gains()
    assert received==[('gravity',DEFAULT_GAINS)]
