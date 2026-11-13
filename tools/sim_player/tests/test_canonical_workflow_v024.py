"""Execute actual media/controller methods with decoder doubles (NOT Qt rendering)."""
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock
import ast
import pytest
from tools.sim_player.canonical_sync import MediaSample

PLAYER=Path(__file__).resolve().parents[1]


def method(filename,cls,name,extra=None):
    tree=ast.parse((PLAYER/filename).read_text())
    c=next(x for x in tree.body if isinstance(x,ast.ClassDef) and x.name==cls)
    n=next(x for x in c.body if isinstance(x,ast.FunctionDef) and x.name==name)
    env={};env.update(extra or {})
    exec(compile(ast.fix_missing_locations(ast.Module(body=[n],type_ignores=[])),filename,'exec'),env)
    return env[name]


class Decoder:
    def __init__(self):self.position_ms=0;self.calls=[];self.player=None
    def position(self):return self.position_ms
    def play(self):self.calls.append(('play',))
    def pause(self):self.calls.append(('pause',))
    def seek(self,t):self.calls.append(('seek',t));self.position_ms=t
    def set_speed(self,r):self.calls.append(('speed',r))


def host():
    from tools.sim_player.video_sync import VideoSeekGuard
    b=NS(media=NS(key='blackbird',data={'calibration':{'verified_by_user':True}},video_path=Path('ref.mp4')),
         warnings=(),source='test',sample=lambda s:MediaSample(True,s+2,s,'xyz','test'),rate=lambda s:1.)
    h=NS(_canonical_binding=b,_canonical_available=True,_canonical_decoder_playing=False,
         _canonical_rate=None,_canonical_rate_time=-1e20,current_s=3.,playing=True,_loop_holding=False,
         speed_ratio=1.,video_backend=Decoder(),video_seek_guard=VideoSeekGuard(),
         _media_status=Mock(),_update_view_stack=Mock())
    return h


def drive(h,force=False):
    return method('canonical_workflow.py','CanonicalVideoMixin','_drive_canonical_video',
                  {'time':NS(monotonic=lambda:10.)})(h,force)


def test_play_uses_mapping_and_does_not_repeat_decoder_pulses():
    h=host();drive(h,True)
    assert ('seek',5000) in h.video_backend.calls
    assert h.video_backend.calls.count(('play',))==1
    for _ in range(10):drive(h)
    assert h.video_backend.calls.count(('play',))==1
    assert len([c for c in h.video_backend.calls if c[0]=='seek'])==1


def test_paused_seek_never_emits_play_pause_pulse():
    h=host();h.playing=False;drive(h,True)
    assert ('seek',5000) in h.video_backend.calls
    assert ('play',) not in h.video_backend.calls
    assert ('pause',) not in h.video_backend.calls


def test_unmatched_interval_hides_video_not_motion_clock():
    h=host();drive(h,True);h.video_backend.calls.clear()
    h._canonical_binding.sample=lambda s:MediaSample(False,None,None,'lost','test')
    before=h.current_s;drive(h)
    assert h.current_s==before and h.playing
    assert h.video_backend.calls==[('pause',)]
    assert not h._canonical_available
    h._update_view_stack.assert_called_once()


def test_reacquisition_seeks_once_then_resumes():
    h=host();h._canonical_available=False;drive(h)
    assert ('seek',5000) in h.video_backend.calls
    assert h.video_backend.calls.count(('play',))==1


def test_rate_tracks_media_pace_and_selected_speed():
    h=host();h.speed_ratio=2.;h._canonical_binding.rate=lambda s:1.1;drive(h)
    assert ('speed',2.2) in h.video_backend.calls


def test_stationary_frozen_map_holds_decoder():
    h=host();h._canonical_decoder_playing=True;h._canonical_binding.rate=lambda s:0.;drive(h,True)
    assert ('pause',) in h.video_backend.calls and ('play',) not in h.video_backend.calls


def test_after_video_end_is_unavailable():
    h=host();h.video_backend.player=NS(duration=lambda:4000)
    drive(h,True)
    assert not h._canonical_available
    assert ('play',) not in h.video_backend.calls


def test_superseded_worker_cannot_replace_current_binding():
    h=NS(_canonical_token=2,recording=object(),_canonical_cache={},_install_binding=Mock())
    method('canonical_workflow.py','CanonicalVideoMixin','_media_map_loaded')(h,NS(token=1),'a',object())
    assert not h._canonical_cache;h._install_binding.assert_not_called()


def test_native_clock_remains_original_offset():
    h=NS(current_s=12.,video_offset_s=1.5,_canonical_binding=None)
    assert method('app.py','ReplayWindow','_video_target_ms')(h)==13500


def test_canonical_clock_does_not_double_apply_original_offset():
    h=NS(current_s=12.,video_offset_s=55.,_canonical_binding=object(),_canonical_clock_ms=lambda:24000)
    assert method('app.py','ReplayWindow','_video_target_ms')(h)==24000


def test_canonical_sync_does_not_use_native_drift_seek_path():
    h=NS(_canonical_binding=object(),_drive_canonical_video=Mock())
    method('app.py','ReplayWindow','_sync_video')(h,True)
    h._drive_canonical_video.assert_called_once_with(True)


def test_same_source_reanalysis_keeps_map_decoder_and_clock():
    h=NS(_canonical_source_key='unchanged')
    activate=method('canonical_workflow.py','CanonicalVideoMixin','_activate_media_for',
                    {'recording_media_key':lambda r:'unchanged','recording_key':lambda r:'blackbird'})
    activate(h,object())  # no decoder/clock mutation or other fields needed


def test_menu_entry_and_reference_switch_are_wired():
    from test_workspace_menu_contract_v022 import menu_host,actions
    h=menu_host()
    actions(h.open_menu)['Ride reference video...'].triggered.emit()
    h.manage_canonical_video.assert_called_once_with()
    h.canonical_video_action.setChecked(False)
    h._canonical_toggled.assert_called_once_with(False)


def test_preroll_reports_video_available_and_countdown():
    h=host();h.current_s=0.;h._canonical_first_available_s=3.4;h.view_mode='video'
    h._canonical_binding.sample=lambda s:MediaSample(False,None,None,'unmatched / telemetry gap','test')
    drive(h)
    assert h._media_status.call_args.args[0]=='Reference video available - begins at 00:03.4'
    assert h._canonical_unavailable_message.startswith('Video starts in 3.4 s.')
