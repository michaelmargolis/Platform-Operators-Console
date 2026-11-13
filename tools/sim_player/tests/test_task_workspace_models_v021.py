"""Tests of the task workspace's Qt-independent state and data transformations."""
from dataclasses import dataclass
from types import SimpleNamespace
import copy
import math
import pytest

from tools.sim_player.task_workspace_models import (
    AXES, read_washout, with_washout, plot_series
)


GLOBAL = dict(
    master_gain=100,
    gains=[100, 75, 100, 100, 100, 100],
    washout_type='classical',
    washout_gain=1.0,
    enabled=[True, True, False, False, False, False],
    tau=[1.2, 1.2, 1.8, 1.0, 1.0, 2.2],
    legacy_duration=[12.] * 6,
)


def recording():
    frames = [
        SimpleNamespace(source_axes=tuple(float(i + t) / 100. for i in range(6)),
                        processed_axes=tuple(float(i + 2*t) / 100. for i in range(6)),
                        muscle_lengths_mm=tuple(770.+i*5+t for i in range(6)))
        for t in range(3)
    ]
    return SimpleNamespace(frames=frames, times=(0.,.05,.1),video_offset_s=.3)


def test_changes_only_two_washout_channels_and_does_not_mutate_source():
    original = copy.deepcopy(GLOBAL)
    updated = with_washout(GLOBAL, surge_enabled=False, sway_enabled=True,
                           surge_tau=2.5, sway_tau=3.0)
    assert GLOBAL == original
    assert updated['enabled'] == [False, True, False, False, False, False]
    assert updated['tau'] == [2.5, 3.0, 1.8, 1.0, 1.0, 2.2]
    assert updated['gains'] == GLOBAL['gains']
    assert updated['washout_type'] == 'classical'
    assert updated['legacy_duration'] == GLOBAL['legacy_duration']


@pytest.mark.parametrize('tau', [0.0, -1, 61, float('inf'), float('nan'), None, 'bad'])
def test_invalid_tau_rejected(tau):
    with pytest.raises(ValueError):
        with_washout(GLOBAL, surge_enabled=True, sway_enabled=True,
                     surge_tau=tau, sway_tau=1.2)


def test_incomplete_snapshot_rejected():
    with pytest.raises(ValueError):
        with_washout({'enabled':[True]*6,'tau':[1]},surge_enabled=True,
                     sway_enabled=True,surge_tau=1.,sway_tau=1.)


def test_washout_readback_is_from_recording_snapshot():
    info=read_washout(GLOBAL)
    assert info['kind']=='classical'
    assert info['surge_enabled'] is True and info['sway_enabled'] is True
    assert info['surge_tau']==info['sway_tau']==1.2


def test_axis_and_muscle_views_use_replay_values_without_processing():
    data=recording()
    traces=plot_series(data,'all_axes')
    assert tuple(label for label,_ in traces)==AXES
    assert traces[0][1] == (.0,.02,.04)
    muscle=plot_series(data,'m3')
    assert muscle[0][1]==(780.,781.,782.)
    raw,processed=plot_series(data,'source_vs_processed_surge')
    assert raw[1] == (0.,.01,.02)
    assert processed[1] == (0.,.02,.04)


def test_missing_data_safe():
    assert plot_series(None,'all_axes')==()
    assert plot_series(recording(),'not_a_plot')==()
    assert read_washout(None)['kind']=='unknown'


def test_profile_gains_are_in_saved_replay_data_without_recomputation():
    data=recording()
    data.dynamic_profile={'label':'candidate'}
    data.frames=[SimpleNamespace(**vars(f),analysis={
        'dynamic':{'multipliers':tuple(1.0 + i/10. for i in range(6))}})
        for f in data.frames]
    values=plot_series(data,'profile_gains')
    assert len(values)==6
    assert values[0][1]==(1.,1.,1.)
    assert values[5][1]==(1.5,1.5,1.5)
