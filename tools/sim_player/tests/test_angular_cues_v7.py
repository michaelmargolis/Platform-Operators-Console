# test_angular_cues_v7.py ver 1
"""Research cue math: roll wrapping, gimbal, yaw invariance, signs and replay."""
from __future__ import annotations

import csv
import math
from pathlib import Path

import pytest

from tools.sim_player.angular_cues import (
    EULER, SINE, GRAVITY, map_angular_cue, gravity_up_local,
    normalize_quaternion, remap_capture_rows,
)
from tools.sim_player.metrics import measure_recording
from tools.sim_player.recording import load_recording

RG = 0.6 / math.pi
PG = 0.6
NEUTRAL = (0.1, -0.2, 0.3, 0.0, 0.0, 0.4)


def quaternion(axis, angle):
    sine, cosine = math.sin(angle/2), math.cos(angle/2)
    if axis == "roll":
        return (0.0, 0.0, sine, cosine)
    if axis == "pitch":
        return (sine, 0.0, 0.0, cosine)
    if axis == "yaw":
        return (0.0, sine, 0.0, cosine)
    raise ValueError(axis)


def mul(a, b):
    x,y,z,w=a; X,Y,Z,W=b
    return (w*X + x*W + y*Z - z*Y,
            w*Y - x*Z + y*W + z*X,
            w*Z + x*Y - y*X + z*W,
            w*W - x*X - y*Y - z*Z)


def mapped(q, mode, axes=NEUTRAL):
    return map_angular_cue(axes, q, mode, roll_gain=RG, pitch_gain=PG)


def test_euler_is_exact_pass_through_even_without_quaternion():
    axes=(.1,-.2,.3,.6,-.55,.4)
    assert mapped(None, EULER, axes) == axes


@pytest.mark.parametrize("mode", (SINE, GRAVITY))
def test_full_rotation_is_bounded_continuous_inversion(mode):
    angles = [math.radians(x) for x in range(0,361,1)]
    roll = [mapped(quaternion("roll", theta), mode)[3] for theta in angles]
    assert max(abs(v) for v in roll) <= RG + 1.e-8
    assert max(abs(roll[n]-roll[n-1]) for n in range(1,len(roll))) < 0.01
    assert roll[0] == pytest.approx(roll[-1], abs=1e-10)
    assert roll[180] == pytest.approx(0, abs=1e-10)
    assert roll[90] == pytest.approx(-RG)
    assert roll[270] == pytest.approx(+RG)


def test_current_euler_has_known_wrapping_jump_for_comparison():
    values=[]
    for deg in (179,181):
        theta=math.radians(deg)
        roll=math.atan2(math.sin(theta),math.cos(theta))
        values.append(-RG*roll)
    assert abs(values[1]-values[0]) > 1.1
    assert abs(mapped(quaternion("roll",math.radians(179)),GRAVITY)[3] -
               mapped(quaternion("roll",math.radians(181)),GRAVITY)[3]) < 0.01


def test_pitch_gravity_has_correct_small_angle_direction_and_is_bounded():
    for degrees in (-10,-1,0,1,10,90,180,270,360):
        theta=math.radians(degrees)
        p=mapped(quaternion("pitch",theta),GRAVITY)[4]
        assert p == pytest.approx(-PG*math.sin(theta),abs=1e-10)
        assert abs(p)<=PG + 1e-8
        if abs(degrees)<=10:
            assert p == pytest.approx(-PG*theta, abs=.004)


def test_yaw_invariance_of_gravity_vector_and_quaternion_sign():
    combined=mul(quaternion("pitch",.7),quaternion("roll",1.2))
    reference = mapped(combined,GRAVITY)
    for yaw in (-3,-1,0,1.5,3):
        rotated=mul(quaternion("yaw",yaw),combined)
        assert mapped(rotated,GRAVITY) == pytest.approx(reference, abs=1e-10)
        assert mapped(tuple(-v for v in rotated),GRAVITY) == pytest.approx(reference,abs=1e-10)


def test_gravity_stays_finite_around_pitch_gimbal_lock():
    values=[]
    for degrees in (89.9,89.99,90,90.01,90.1):
        combined=mul(quaternion("pitch",math.radians(degrees)),quaternion("roll",.3))
        values.append(mapped(combined,GRAVITY))
    assert all(all(math.isfinite(v) for v in frame) for frame in values)
    assert max(abs(values[i][3]-values[i-1][3]) for i in range(1,len(values)))<.01


def test_unchanged_four_axes_and_sine_retains_recorded_pitch():
    q=mul(quaternion("pitch",.2),quaternion("roll",.7))
    for mode in (SINE,GRAVITY):
        value=mapped(q,mode)
        assert [value[i] for i in (0,1,2,5)] == [NEUTRAL[i] for i in (0,1,2,5)]
    assert mapped(q,SINE)[4]==NEUTRAL[4]


def test_malformed_quaternion_is_rejected():
    with pytest.raises(ValueError):
        normalize_quaternion((0,0,0,0))
    with pytest.raises(ValueError):
        mapped((math.nan,0,0,1), GRAVITY)


def test_capture_requires_quaternion_for_new_modes(tmp_path):
    path=tmp_path/'ride.csv'
    path.write_text('elapsed_s,pos_y,norm_roll\n0,1,.1\n')
    assert remap_capture_rows(path,[(0,1,NEUTRAL,None)],EULER,roll_gain=RG,pitch_gain=PG)[0][2]==NEUTRAL
    with pytest.raises(ValueError,match='quat_'):
        remap_capture_rows(path,[(0,1,NEUTRAL,None)],GRAVITY,roll_gain=RG,pitch_gain=PG)


def test_replay_uses_same_poc_kinematics_for_all_modes(tmp_path):
    path=tmp_path/'Blackbird_repeat01_test.csv'
    columns=['ride','elapsed_s','pos_y','norm_surge','norm_sway','norm_heave',
             'norm_roll','norm_pitch','norm_yaw','quat_x','quat_y','quat_z','quat_w']
    with path.open('w',newline='') as out:
        wr=csv.DictWriter(out,fieldnames=columns)
        wr.writeheader()
        for i in range(18):
            angle=math.radians(170 + i*1.2)
            q=quaternion('roll',angle)
            wrapped=math.atan2(math.sin(angle),math.cos(angle))
            wr.writerow(dict(ride='Blackbird',elapsed_s=i*.05,pos_y=7.5,
                norm_surge=0,norm_sway=0,norm_heave=.1,
                norm_roll=-RG*wrapped,norm_pitch=0,norm_yaw=0,
                **dict(zip(('quat_x','quat_y','quat_z','quat_w'),q))))
    recordings={mode:load_recording(path,cue_mode=mode) for mode in (EULER,SINE,GRAVITY)}
    for mode, rec in recordings.items():
        assert rec.cue_mode==mode
        assert len(rec.frames)==18
        assert all(len(f.muscle_lengths_mm)==6 for f in rec.frames)
        assert all('applied_real' in f.analysis for f in rec.frames)
        assert all(rec.min_length_mm<=length<=rec.max_length_mm
                   for f in rec.frames for length in f.muscle_lengths_mm)
        assert rec.times==recordings[EULER].times
        assert all(f.source_axes==recordings[EULER].frames[i].source_axes
                   for i,f in enumerate(rec.frames))
        metrics=measure_recording(rec)
        assert metrics.samples==18
        assert metrics.peak_step_mm>=0
    # Sine and gravity have the same pure-roll cue. Both must remove Euler's
    # artificial near-180 jump before the common downstream calculation.
    assert max(abs(f.input_axes[3]) for f in recordings[EULER].frames)>.5
    assert max(abs(f.input_axes[3]) for f in recordings[SINE].frames)<.04
    assert recordings[SINE].frames[10].source_axes != recordings[SINE].frames[10].input_axes
    assert measure_recording(recordings[EULER]).peak_step_mm > 10
    assert measure_recording(recordings[SINE]).peak_step_mm < measure_recording(recordings[EULER]).peak_step_mm
    assert measure_recording(recordings[GRAVITY]).peak_step_mm < measure_recording(recordings[EULER]).peak_step_mm
    # No sidecar/profile or CSV modification was performed.
    assert not list(tmp_path.glob('*_sim_player_sync.json'))


def test_visualizer_packets_reflect_selected_cue_without_new_protocol(tmp_path):
    from tools.sim_player.twin_output import ReplayTwinOutput
    from tools.sim_player.settings import VisualizerSettings

    path=tmp_path/'Blackbird_repeat01_123456.csv'
    fields=['elapsed_s','pos_y','norm_surge','norm_sway','norm_heave',
            'norm_roll','norm_pitch','norm_yaw','quat_x','quat_y','quat_z','quat_w']
    with path.open('w',newline='') as f:
        writer=csv.DictWriter(f,fields);writer.writeheader()
        for i,deg in enumerate((175,179,181,185)):
            angle=math.radians(deg)
            q=quaternion('roll',angle)
            roll=math.atan2(math.sin(angle),math.cos(angle))
            writer.writerow(dict(elapsed_s=i*.05,pos_y=7.5,norm_surge=0,
                norm_sway=0,norm_heave=.1,norm_roll=-RG*roll,norm_pitch=0,
                norm_yaw=0,**dict(zip(('quat_x','quat_y','quat_z','quat_w'),q))))
    euler=load_recording(path,cue_mode=EULER)
    gravity=load_recording(path,cue_mode=GRAVITY)

    class MemorySender:
        def __init__(self,**kwargs):
            self.enabled=True; self.last_error='';self.packets=[]
        def send_snapshot(self,**packet):
            self.packets.append(packet);return True
        def close(self):self.enabled=False

    sender=ReplayTwinOutput(VisualizerSettings(True),sender_factory=MemorySender)
    assert sender.send_frame(euler,2,force=True)
    assert sender.send_frame(gravity,2,force=True)
    a,b=sender._sender.packets
    assert a['transform'] != b['transform']
    assert a['muscle_length_mm'] != b['muscle_length_mm']
    assert a['command_pressure_mbar']==b['command_pressure_mbar']==(0.0,)*6
    assert a['applied_transform_real'] is not None
    assert b['applied_transform_real'] is not None
    sender.close()
