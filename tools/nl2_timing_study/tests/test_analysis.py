"""Synthetic evidence only: exact known timing/trajectory transformations."""
import csv
import json
import math
from pathlib import Path
import shutil
import numpy as np
import pytest
from tools.nl2_timing_study.analysis import read_trial, compare, analyse_paths, group_key, quaternion_angle
from tools.nl2_timing_study.common import SCHEMA, sha256, write_json, write_table
from tools.nl2_timing_study.__main__ import parser
from tools.nl2_timing_study.capture import park_choices


def make_trial(root, name="run", machine="fast", stretch=1., delay=.25, local=0.,
               complete=True, warmup=False, duplicate=False, pause=False, gap=False, lateral=0., station=0):
    root = Path(root)/name
    root.mkdir(parents=True)
    phase = np.arange(0, 60.0001, .05)
    if gap:
        phase = phase[(phase < 28) | (phase > 35)]
    motion = 2+delay
    rows = []
    for i, p in enumerate(phase):
        t = motion + stretch*p + local*np.sin(np.pi*p/60)**2
        row = dict(elapsed_s=t, receive_monotonic_s=100+t,
                   process_monotonic_s=100+t+.01, send_monotonic_s=100+t-.002,
                   send_end_monotonic_s=100+t-.0018, rtt_ms=2., queue_delay_ms=10.,
                   phase="riding", state=7 if pause and 30<p<31 else 3, frame=i*3,
                   coaster_index=0, train=0, car=0, seat=0, view_mode=1,
                   speed=5., pos_x=5*p, pos_y=3*np.sin(p/5)+lateral, pos_z=4*np.sin(p/7),
                   quat_x=0, quat_y=0, quat_z=0, quat_w=1,
                   g_force_x=0, g_force_y=1, g_force_z=0, is_duplicate_frame=0, request_id=i)
        rows.append(row)
        if duplicate and i%3==0:
            rows.append(dict(row, elapsed_s=t+.01, receive_monotonic_s=100+t+.01,
                             is_duplicate_frame=1, request_id=100000+i))
    raw = root/(name+"_timing.csv")
    write_table(raw, rows)
    meta = dict(schema=SCHEMA, status="complete" if complete else "incomplete",
                started_utc=name, finished_utc=name, ride="Synthetic Ride", park_key="synthetic",
                repeat=1, warmup=warmup, machine={"label":machine}, conditions="synthetic only",
                source_hashes={"a":"1"}, park_sha256="SYNTHETIC", nl2_version="fake",
                view_identity=[0,0,0,0], departure_station=[0,station],
                marks=dict(dispatch_tx=2., first_motion=motion,
                           view_train_departed=motion+.5, view_train_returned=motion+60*stretch),
                files={"timing":raw.name}, file_sha256={"timing":sha256(raw)}, quality_issues=[])
    path = root/(name+"_trial.json")
    write_json(path, meta)
    return path


def test_five_repeats_local_defaults():
    args=parser().parse_args(["capture"])
    assert args.repeats==5 and args.hz==20 and args.port==15151
    assert args.warmups==0 and args.rides=="all"
    assert "host" not in vars(args)


def test_all_supported_excludes_hidden():
    assert "grandnational" not in [k for _,k,_ in park_choices()]
    assert {"blackbird","flyingbeast","scenicrun"} <= {k for _,k,_ in park_choices()}
    assert park_choices("blackbird")[0][1]=="blackbird"
    assert park_choices("Flying Beast")[0][1]=="flyingbeast"
    with pytest.raises(ValueError):park_choices("GrandNational")


@pytest.mark.parametrize("bad", ["0","-2","nan","inf"])
def test_bad_rate_rejected(bad):
    with pytest.raises(SystemExit):parser().parse_args(["capture","--hz",bad])


def test_constant_dispatch_delay_separated_from_ride_drift(tmp_path):
    a=read_trial(make_trial(tmp_path,"a"))
    b=read_trial(make_trial(tmp_path,"b",delay=1.5))
    c,h,_=compare(a,b)
    assert c["sufficient_coverage"]
    assert c["dispatch_clock_max_abs_s"]==pytest.approx(1.25,abs=.005)
    assert c["motion_clock_max_abs_s"]<.0001
    assert c["within_motion_clock_budget"]


def test_uniform_clock_drift_is_detected_not_fitted_away(tmp_path):
    a=read_trial(make_trial(tmp_path,"a"))
    b=read_trial(make_trial(tmp_path,"b",machine="slow",stretch=1.02))
    c,h,_=compare(a,b)
    assert c["sufficient_coverage"]
    assert c["motion_clock_max_abs_s"]>1.
    assert not c["within_motion_clock_budget"]
    assert c["best_fit_clock_ratio"]==pytest.approx(1.02,abs=1e-5)
    assert c["affine_max_abs_s"]<.001
    assert c["previous_landmark_max_abs_s"]<.17
    assert c["distance_phase_max_abs_s"]<.001
    corrected=[x for x in h if x["reanchor_after_measurement"]]
    assert corrected and corrected[0]["previous_landmark_error_s"]>.08


def test_local_drift_even_with_equal_total_duration(tmp_path):
    a=read_trial(make_trial(tmp_path,"a"))
    b=read_trial(make_trial(tmp_path,"b",local=.6))
    c,_,_=compare(a,b)
    assert c["duration_difference_s"]==pytest.approx(0)
    assert c["motion_clock_max_abs_s"]>.55
    assert c["affine_max_abs_s"]>.2


def test_duplicate_frames_not_extra_geometry_samples(tmp_path):
    a=read_trial(make_trial(tmp_path,"a"))
    b=read_trial(make_trial(tmp_path,"b",duplicate=True))
    assert b.summary["duplicate_replies"]>100
    assert len(a.rows)==len(b.rows)
    c,_,_=compare(a,b)
    assert c["motion_clock_max_abs_s"]<.001


@pytest.mark.parametrize("kwargs,issue", [({"complete":False},"incomplete_trial"),({"warmup":True},"warmup_excluded"),({"pause":True},"invalid_play_state")])
def test_exclusions(tmp_path,kwargs,issue):
    trace=read_trial(make_trial(tmp_path,**kwargs))
    assert issue in trace.issues
    assert not trace.summary["eligible"]


def test_gaps_not_interpolated_as_good_landmarks(tmp_path):
    a=read_trial(make_trial(tmp_path,"a"))
    b=read_trial(make_trial(tmp_path,"b",gap=True))
    c,h,_=compare(a,b)
    assert b.summary["gaps_above_threshold"]==1
    assert c["coverage"]<1
    assert all(x["target_sample_interval_s"]<=.25 for x in h)


def test_different_trajectory_rejected(tmp_path):
    a=read_trial(make_trial(tmp_path,"a"))
    b=read_trial(make_trial(tmp_path,"b",lateral=50))
    c,h,_=compare(a,b)
    assert not c["sufficient_coverage"] and not h


def test_station_identity_separates_routes(tmp_path):
    a=read_trial(make_trial(tmp_path,"a"))
    b=read_trial(make_trial(tmp_path,"b",station=1))
    assert group_key(a)!=group_key(b)


def test_hash_verification(tmp_path):
    p=make_trial(tmp_path)
    raw=p.with_name("run_timing.csv")
    raw.write_text(raw.read_text()+"\n")
    with pytest.raises(ValueError,match="hash"):read_trial(p)


def test_quaternion_sign_is_not_rotation_change():
    assert quaternion_angle([0,0,0,1],[0,0,0,-1])==0


def test_cross_pc_report_and_duplicate_copy(tmp_path):
    a=make_trial(tmp_path,"01",machine="high_spec")
    b=make_trial(tmp_path,"02",machine="low_spec",stretch=1.01)
    make_trial(tmp_path,"03",complete=False)
    shutil.copytree(a.parent,tmp_path/"duplicate")
    out=analyse_paths([tmp_path],tmp_path/"result",reference_machine="high_spec")
    report=json.loads((out/"report.json").read_text())
    assert len(report["runs"])==3
    assert len(report["duplicate_copies_ignored"])==1
    assert len(report["comparisons"])==1
    assert report["comparisons"][0]["reference_machine"]=="high_spec"
    assert "hindsight" in (out/"report.html").read_text()
    assert (out/"landmarks.csv").exists()


def test_incomplete_only_reports_insufficient_evidence(tmp_path):
    make_trial(tmp_path,complete=False)
    out=analyse_paths([tmp_path],tmp_path/"report")
    report=json.loads((out/"report.json").read_text())
    assert not report["comparisons"] and report["runs"][0]["eligible"] is False


def test_legacy_csv_not_misrepresented(tmp_path):
    (tmp_path/"old.csv").write_text("elapsed_s,speed\n0,1\n")
    with pytest.raises(ValueError,match="No .*trial"):analyse_paths([tmp_path],tmp_path/"report")


def test_reanalysis_does_not_overwrite(tmp_path):
    make_trial(tmp_path)
    dest=tmp_path/"report"
    a=analyse_paths([tmp_path],dest)
    b=analyse_paths([tmp_path],dest)
    assert a!=b and b.parent==a


def test_unknown_reference_machine_is_explicit(tmp_path):
    make_trial(tmp_path)
    out=analyse_paths([tmp_path],tmp_path/"report",reference_machine="absent")
    r=json.loads((out/"report.json").read_text())
    assert "absent" in r["groups"][0]["note"]


def test_malicious_companion_not_read(tmp_path):
    p=make_trial(tmp_path)
    m=json.loads(p.read_text());m["files"]["timing"]="../outside.csv";write_json(p,m)
    with pytest.raises(ValueError,match="Companion"):read_trial(p)


def test_stationary_rotation_is_reported_separately(tmp_path):
    from tools.nl2_timing_study.analysis import stationary_sections
    a=read_trial(make_trial(tmp_path))
    chosen=(a.time-a.meta["marks"]["first_motion"]>=20)&(a.time-a.meta["marks"]["first_motion"]<=22)
    a.speed[chosen]=0.
    a.quat[chosen]=[math.sin(.2),0,0,math.cos(.2)]
    first=np.flatnonzero(chosen)[0]
    a.quat[first]=[0,0,0,1]
    sections=stationary_sections(a)
    assert len(sections)==1
    assert sections[0]["stationary_rotation"]
    assert sections[0]["orientation_excursion_deg"]>20
