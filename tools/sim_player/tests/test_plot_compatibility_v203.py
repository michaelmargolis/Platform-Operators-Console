# test_plot_compatibility_v203.py ver 1
from types import SimpleNamespace
from tools.sim_player.task_workspace_models import plot_series, resolve_plot_key


def _recording(dynamic=False):
    frames=[]
    for n in range(4):
        analysis={}
        if dynamic:
            analysis={'dynamic':{'multipliers':(1.0,1.1,0.9,1.0,1.0,1.0)}}
        frames.append(SimpleNamespace(
            processed_axes=(.1*n,.2*n,.3*n,.01*n,.02*n,.03*n),
            source_axes=(.09*n,.18*n,.27*n,.009*n,.018*n,.027*n),
            muscle_lengths_mm=(800+n,810+n,820+n,830+n,840+n,850+n),
            analysis=analysis))
    return SimpleNamespace(frames=tuple(frames), dynamic_profile=({'label':'test'} if dynamic else None),
                           min_length_mm=750.0,max_length_mm=1000.0,video_offset_s=0.0)


def test_raw_dynamic_source_has_normal_plot_data_but_no_multiplier_plot():
    recording=_recording(False)
    assert len(plot_series(recording,'all_axes')) == 6
    assert len(plot_series(recording,'all_muscles')) == 6
    assert len(plot_series(recording,'source_vs_processed_surge')) == 2
    assert plot_series(recording,'profile_gains') == ()


def test_dynamic_profile_exposes_multiplier_plot():
    recording=_recording(True)
    gains=plot_series(recording,'profile_gains')
    assert len(gains) == 6
    assert gains[1][1] == (1.1,1.1,1.1,1.1)


def test_stale_dynamic_multiplier_selection_falls_back_for_source_recording():
    recording=_recording(False)
    assert resolve_plot_key(recording,'profile_gains') == 'all_axes'
    assert resolve_plot_key(recording,'all_muscles') == 'all_muscles'

def test_dynamic_multiplier_selection_is_retained_for_profile_recording():
    recording=_recording(True)
    assert resolve_plot_key(recording,'profile_gains') == 'profile_gains'
