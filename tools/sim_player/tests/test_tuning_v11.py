# test_tuning_v11.py ver 1
"""Qt-independent motion tuning tests using existing POC processing."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from tools.sim_player.tuning import (
    DEFAULT_GAINS, ExperimentSettings, load_experiment, save_experiment,
    muscle_flags, scale_capture_rows, tuning_metrics, validate_gain_percent,
)
from tools.sim_player.recording import PlaybackFrame, load_recording
from tools.sim_player.anomaly_detection import detect_anomalies, AnomalyThresholds
from tools.sim_player.tests.test_recording import make_capture


def test_validate_gains_rejects_invalid_and_accepts_boundaries():
    assert validate_gain_percent(DEFAULT_GAINS) == tuple(float(x) for x in DEFAULT_GAINS)
    assert validate_gain_percent((0, 5, 100, 175, 200, 99)) == (0., 5., 100., 175., 200., 99.)
    for wrong in ((100,) * 5, (100,) * 7, (100,)*5+(201,), (100,)*5+(-1,),
                  (100,)*5+(float('nan'),), (100,)*5+(99.5,), (100,)*5+(True,)):
        with pytest.raises(ValueError):
            validate_gain_percent(wrong)


def test_scale_rows_exact_and_preserves_source_rows():
    rows = [(0, 7, (.1,.2,.3,.4,.5,.6), .8), (.05,7.1,(-.1,.2,.3,.4,.5,.6),None)]
    saved = tuple(rows)
    assert scale_capture_rows(rows, DEFAULT_GAINS) is rows
    changed = scale_capture_rows(rows, (0,50,100,150,200,25))
    assert changed[0][:2] == (0,7)
    assert changed[0][2] == pytest.approx((0,.1,.3,.6,1,.15))
    assert changed[0][3] == .8
    assert rows == list(saved)


def test_pre_scaling_equal_to_poc_axis_gain_override():
    """Experimental pre-scaling and production per-axis gain multiplication commute."""
    from core.telemetry_config import TelemetryConfig
    from pathlib import Path
    ini = Path(__file__).resolve().parents[3] / 'sims/nolimits2/telemetry_config.ini'
    left = TelemetryConfig(ini)
    right = TelemetryConfig(ini)
    factors = (0.50, 1.25, 1.0, 1.6, 0.85, 2.0)
    right.values['gains'] = [gain*factor for gain, factor in zip(right.values['gains'], factors)]
    for t in range(20):
        values = (.1 + t*.01, -.2, .3, -.4 + t*.01, -.1, .2)
        actual = left.process(tuple(v*g for v,g in zip(values,factors)), 100, .05)
        expected = right.process(values, 100, .05)
        assert actual == pytest.approx(expected, abs=1.e-12)


def test_red_flags_show_request_even_when_workspace_recovers():
    def frame(requested, workspace, permitted):
        return PlaybackFrame(.1, (0,)*6, (0,)*6, (0,)*6,
            tuple(permitted), True, 44, analysis={
                'requested': tuple(requested), 'workspace_lengths': tuple(workspace)})
    f=frame([740,1005,850,850,850,850], [750,1000,850,850,850,850],
            [750,1000,850,850,850,850])
    oob, clipped = muscle_flags(f,750,1000)
    assert oob == (True,True,False,False,False,False)
    assert not any(clipped)   # correctly workspace-limited, no final clamp
    f=frame([800]*6,[740,800,800,800,800,800], [750,800,800,800,800,800])
    oob,clipped=muscle_flags(f,750,1000)
    assert not any(oob) and clipped[0]


def test_loading_and_gain_replay_changes_true_poc_analysis(tmp_path):
    path=make_capture(tmp_path)
    original=path.read_bytes()
    base=load_recording(path)
    # Change only Roll; original capture, source values, and other axes unchanged.
    tuned=load_recording(path,gain_percent=(100,100,100,180,100,100))
    assert tuned.frames[2].source_axes == base.frames[2].source_axes
    assert tuned.frames[2].input_axes[3] == pytest.approx(base.frames[2].input_axes[3]*1.8)
    assert tuned.frames[2].input_axes[4] == pytest.approx(base.frames[2].input_axes[4])
    assert tuned.frames[2].processed_axes[3] != base.frames[2].processed_axes[3]
    assert tuned.frames[2].muscle_lengths_mm != base.frames[2].muscle_lengths_mm
    assert tuned.gain_percent == (100.,100.,100.,180.,100.,100.)
    assert path.read_bytes() == original
    assert tuning_metrics(base).frames == len(base.frames)
    assert isinstance(detect_anomalies(tuned,AnomalyThresholds()),tuple)


def test_experiment_file_is_separate_and_strict(tmp_path):
    settings=ExperimentSettings('gravity',(100,90,120,150,75,100),45,33)
    path=tmp_path/'independent_research_settings.json'
    save_experiment(path,settings)
    assert load_experiment(path)==settings
    assert json.loads(path.read_text())['schema']=='sim_player_experiment_v3'
    contents=json.loads(path.read_text()); contents['angular_cue']='invalid'
    path.write_text(json.dumps(contents))
    with pytest.raises(ValueError):
        load_experiment(path)
    with pytest.raises(ValueError):
        ExperimentSettings(gain_percent=(100,)*5)


def test_requested_and_workspace_metrics_are_not_conflated():
    def f(t,req,workspace,final,limited):
        return PlaybackFrame(t, (0,)*6,(0,)*6,(0,)*6,
            tuple(final),limited,40,analysis={'requested':tuple(req),'workspace_lengths':tuple(workspace)})
    ride=SimpleNamespace(
        min_length_mm=750,max_length_mm=1000,
        frames=(f(0,[800]*6,[800]*6,[800]*6,False),
                f(.05,[740]+[800]*5,[750]+[800]*5,[750]+[800]*5,True),
                f(.1,[800]*6,[740]+[800]*5,[750]+[800]*5,False)),
    )
    m=tuning_metrics(ride)
    assert m.requested_oob_frames==1
    assert m.workspace_frames==1
    assert m.final_clipped_frames==1
    assert m.worst_requested_excess_mm==10
