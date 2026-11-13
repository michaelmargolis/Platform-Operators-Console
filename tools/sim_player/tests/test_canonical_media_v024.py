"""Real CSV/NPZ/file import and actual B+C matching; no Qt needed."""
from __future__ import annotations
from types import SimpleNamespace as NS
from pathlib import Path
from copy import deepcopy
import csv
import json
import math
import numpy as np
import pytest
from tools.sim_player.canonical_media import (register_media, load_media, checked_anchors,
    calibrated_time, initial_calibration, update_calibration, media_path)
from tools.sim_player.media_trajectory import (MediaError, MediaCancelled, read_trajectory,
    file_sha256, compare_identity, safe_key)
from tools.sim_player.canonical_sync import (bind_recording, MediaBinding, recording_media_key,
    _interpolate_map)
from tools.sim_player.task_workspace_models import plot_styles, AXIS_COLORS
from tools.nl2_sync.profile import DynamicProfile


def capture(path, *, delay=1., pace=1., offset=0., gap=None, hold=False):
    times=np.arange(0,12.001,.05)
    phase=(times-delay)*pace
    route=np.maximum(0.,phase)
    if hold:
        route=np.where(phase<4,np.maximum(phase,0),np.where(phase<7,4,phase-3))
    speed=np.where(times<delay,0.,2.)
    if hold:speed[(phase>=4)&(phase<7)]=0
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',newline='') as f:
        writer=csv.writer(f)
        writer.writerow(['ride','elapsed_s','valid','frame','state','speed','pos_x','pos_y','pos_z','quat_x','quat_y','quat_z','quat_w'])
        for i,t in enumerate(times):
            if gap and gap[0]<t<gap[1]:continue
            writer.writerow(['Blackbird',t,1,i,3,speed[i],route[i]*2+offset,0,0,0,0,0,1])
    return path


def rec(path):
    return NS(csv_path=path,ride='Blackbird',dynamic_profile=None,variant_snapshot={'park':'blackbird'})


@pytest.fixture
def library(tmp_path):
    source=capture(tmp_path/'source'/'run.csv')
    video=tmp_path/'source'/'run.mp4';video.write_bytes(b'test-video-payload')
    root=tmp_path/'canonical_rides'
    media=register_media('blackbird',source,video,root=root,anchors=[[0,2]],calibrated=True,confirmed_pair=True)
    return root,source,video,media


def test_import_has_portable_paths_preserves_originals_and_copies_sidecars(tmp_path):
    csv_path=capture(tmp_path/'source'/'run.csv')
    video=csv_path.with_suffix('.mp4');video.write_bytes(b'original-video')
    events=csv_path.with_name('run_state_events.csv');events.write_text('elapsed_s,event\n0,start\n')
    before={p:p.read_bytes() for p in (csv_path,video,events)}
    media=register_media('blackbird',csv_path,video,root=tmp_path/'canonical_rides',confirmed_pair=True)
    assert media.manifest_path.parent.name=='blackbird'
    assert media.video_path.read_bytes()==before[video]
    assert (media.video_path.parent/'source'/events.name).exists()
    assert all(p.read_bytes()==b for p,b in before.items())
    assert not Path(media.data['video']['path']).is_absolute()


def test_same_capture_maps_directly_without_candidate_changes(library):
    root,path,_,media=library
    binding=bind_recording(rec(path),root=root)
    assert binding.source=='original reference capture'
    assert binding.sample(5).video_s==pytest.approx(7.)
    assert binding.sample(5).canonical_phase_s==pytest.approx(4.)
    assert binding.sample(99).available is False


def test_changed_pace_is_position_aligned_not_equal_elapsed(library,tmp_path):
    root,_,_,media=library
    path=capture(tmp_path/'target.csv',delay=2.,pace=1.10)
    binding=bind_recording(rec(path),root=root)
    # target t=6: progress=(6-2)*1.1=4.4; canonical CSV=5.4; PTS=7.4
    assert binding.sample(6).video_s==pytest.approx(7.4,abs=.025)
    assert binding.rate(6)==pytest.approx(1.1,abs=.025)
    assert binding.summary['moving_match_fraction']>.95


def test_reference_holds_are_labelled_unobservable_time(tmp_path):
    src=capture(tmp_path/'run.csv',hold=True)
    vid=tmp_path/'run.mp4';vid.write_bytes(b'v')
    root=tmp_path/'canonical_rides'
    register_media('blackbird',src,vid,root=root,confirmed_pair=True)
    target=capture(tmp_path/'other.csv',delay=1.1,hold=True)
    binding=bind_recording(rec(target),root=root)
    assert binding.sample(6).available
    assert 'timing estimate' in binding.sample(6).status
    assert binding.sample(9).video_s==pytest.approx(8.9,abs=.05)


def test_no_interpolation_across_missing_telemetry(library,tmp_path):
    root,_,_,_=library
    path=capture(tmp_path/'gap.csv',gap=(4.,4.8))
    binding=bind_recording(rec(path),root=root)
    assert not binding.sample(4.4).available
    assert binding.sample(6.).available


def test_wrong_route_rejected_not_silently_played(library,tmp_path):
    root,_,_,_=library
    path=capture(tmp_path/'wrong.csv',offset=50.)
    with pytest.raises(MediaError,match='incompatible'):
        bind_recording(rec(path),root=root)


@pytest.mark.parametrize('key,a,b',[('park','blackbird','mdxlogo'),('park_sha256','a','b'),
    ('view_identity',[0,0,0,0,1],[0,0,1,0,1]),('departure_station',[0,0],[0,1])])
def test_known_incompatible_identifiers_rejected(key,a,b):
    c={'park':'blackbird',key:a};t={'park':'blackbird',key:b}
    with pytest.raises(MediaError):compare_identity(c,t)


def test_platform_and_vr_are_not_media_compatibility_gates():
    compare_identity({'park':'blackbird','platform':'chair_v3','observed_vr':'off'},
                     {'park':'blackbird','platform':'platform_v2','observed_vr':'on'})


@pytest.mark.parametrize('bad', ['../blackbird','/tmp/ride','A:ride','ride name','', 'foo\\bar'])
def test_invalid_ride_key_rejected(bad):
    with pytest.raises(MediaError):safe_key(bad)


def test_confirmation_required(tmp_path):
    with pytest.raises(MediaError,match='Confirm'):
        register_media('blackbird',tmp_path/'a.csv',tmp_path/'v.mp4')


def test_cancel_keeps_existing_reference(library):
    root,source,video,media=library
    original=media.manifest_path.read_bytes()
    with pytest.raises(MediaCancelled):
        register_media('blackbird',source,video,root=root,confirmed_pair=True,cancel=lambda:True)
    assert media.manifest_path.read_bytes()==original


def test_reimport_retains_previous_assets(library):
    root,source,video,media=library
    old_video=media.video_path
    new=register_media('blackbird',source,video,root=root,confirmed_pair=True)
    assert old_video.exists() and new.video_path.exists() and new.video_path!=old_video


def test_trajectory_tamper_rejected(library):
    root,_,_,media=library
    trajectory=media_path(media.manifest_path.parent,media.data['trajectory']['path'])
    trajectory.write_bytes(b'bad')
    with pytest.raises(MediaError,match='checksum'):load_media('blackbird',root)


def test_video_size_tamper_rejected(library):
    root,_,_,media=library
    media.video_path.write_bytes(b'x')
    with pytest.raises(MediaError,match='size'):load_media('blackbird',root)


def test_calibration_is_separate_and_atomic(library):
    root,source,_,media=library
    before=source.read_bytes()
    new=update_calibration(media,[[0,1],[10,11.1]])
    assert new.video_time(4)==pytest.approx(6.05)  # phase 4 -> capture 5
    assert source.read_bytes()==before
    with pytest.raises(MediaError,match='changed in another'):update_calibration(media,[[0,3]])


@pytest.mark.parametrize('anchors',[[],[[1,2],[1,3]],[[0,2],[1,1]],[[0,0],[1,8]],[[0,float('nan')]]])
def test_invalid_calibration_rejected(anchors):
    with pytest.raises(MediaError):checked_anchors(anchors)


def test_single_and_multi_anchor_interpolation():
    assert calibrated_time(5,[[2,4]])==7
    assert calibrated_time(5,[[0,1],[10,12]])==pytest.approx(6.5)
    assert calibrated_time(12,[[0,1],[10,12]])==14


def test_legacy_saved_offset_used(library):
    _,path,_,_=library
    path.with_name(path.stem+'_sim_player_sync.json').write_text(json.dumps({
        'schema':1,'capture_csv':path.name,'video_offset_s':1.75}))
    anchors,checked,_=initial_calibration(path)
    assert anchors==[[0,1.75]] and checked


def test_equal_timestamps_do_not_discard_whole_capture(library,tmp_path):
    _,source,_,_=library
    lines=source.read_text().splitlines()
    path=tmp_path/'ties.csv';path.write_text('\n'.join(lines[:22]+[lines[21]]+lines[22:])+'\n')
    t=read_trajectory(path,park_key='blackbird')
    assert len(t.time)==len(lines)-1
    assert any('repeated-time' in w for w in t.warnings)


def test_backwards_clock_rejected(library,tmp_path):
    _,source,_,_=library
    lines=source.read_text().splitlines();lines[30],lines[31]=lines[31],lines[30]
    path=tmp_path/'bad.csv';path.write_text('\n'.join(lines)+'\n')
    with pytest.raises(MediaError,match='backwards'):read_trajectory(path,park_key='blackbird')


def test_npz_and_video_move_with_parent(library,tmp_path):
    root,_,_,_=library
    moved=tmp_path/'moved';root.rename(moved)
    assert load_media('blackbird',moved).video_path.is_file()


def test_dynamic_saved_study_maps_bound_reference(library,tmp_path):
    root,path,_,media=library
    trace=read_trajectory(path,park_key='blackbird')
    profile_dir=tmp_path/'profile';profile_dir.mkdir()
    np.savez_compressed(profile_dir/'reference.npz',time_s=trace.phase,
                        xyz_m=trace.xyz,quaternion_xyzw=trace.quat,speed_mps=trace.speed)
    identity={'park':'blackbird','platform':'chair_v3','reference_sha256':file_sha256(profile_dir/'reference.npz'),
              'processing_sha256':'0'*64}
    profile=DynamicProfile([0,11],np.ones((2,6)),np.ones(6),identity,{'reference_file':'reference.npz'})
    profile.save(profile_dir/'profile.json')
    frames=tuple(NS(analysis={'valid':True,'dynamic':{'ride_time_s':float(t),'phase_s':max(0,float(t)),
                    'matched_phase_s':None,'status':'xyz' if t>=0 else 'waiting'}}) for t in trace.phase)
    r=NS(csv_path=tmp_path/'diagnostic.csv',ride='Blackbird',times=tuple(trace.time),frames=frames,
         dynamic_profile={'profile':profile.to_dict(),'profile_path':str(profile_dir/'profile.json'),
                          'capture_timing_sha256':'a'*64})
    binding=bind_recording(r,root=root)
    assert binding.sample(5).video_s==pytest.approx(7.,abs=.05)
    assert any('archived' in w for w in binding.warnings)
    # Data and motion settings were not mutated by attaching media.
    assert all(frame.analysis['dynamic']['matched_phase_s'] is None for frame in frames)
    assert r.dynamic_profile['profile']==profile.to_dict()


def test_each_single_axis_uses_its_colour_and_stage_dash():
    for i,name in enumerate(('Surge','Sway','Heave','Roll','Pitch','Yaw')):
        assert plot_styles(name.lower(),((name,(0.,)),))[0]==(name,AXIS_COLORS[i],False)
    styles=plot_styles('source_vs_processed_sway',(('NL2 CSV Sway',(0.,)),('Processed Sway',(0.,))))
    assert styles[0][2] and not styles[1][2]
    assert styles[1][1]==AXIS_COLORS[1]


def test_missing_reference_is_not_an_error(library,tmp_path):
    _,path,_,_=library
    assert bind_recording(rec(path),root=tmp_path/'empty') is None


def test_negative_pts_and_outside_coverage_not_claimed(library):
    root,path,_,media=library
    update_calibration(media,[[0,-2]])
    binding=bind_recording(rec(path),root=root)
    assert not binding.sample(0).available
    assert binding.sample(4).video_s==pytest.approx(2.)
    assert not binding.sample(-1).available
