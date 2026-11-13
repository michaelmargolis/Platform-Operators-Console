# test_optimizer_v192.py ver 1
"""Numerical, export, replay and isolation checks; all recordings are synthetic."""
from __future__ import annotations

import ast
from copy import deepcopy
import csv
from dataclasses import replace
import json
import math
from pathlib import Path
import socket
from types import SimpleNamespace

import numpy as np
import pytest

from sims.nolimits2 import ride_config
from sims.nolimits2.variant_store import VariantStore
from tools.sim_player.optimizer import (analyse_candidate, choose_cues, discover_inputs,
    export_candidate, main, optimise_group, read_input, recording_summary, snapshot_for_parameters)
from tools.sim_player.optimization_search import (Capture, Evaluation, Evaluator, Parameters,
    SearchOptions, prepare_normalized, priority_scores, rotation_evidence, search_candidates)
from tools.sim_player.recording import load_recording
from tools.sim_player.study_bundle import bundle_path, checked_global_settings, model_fingerprint
from tools.sim_player.tuning import current_poc_global_settings
from tools.sim_player.variant_presets import default_label_for_capture


def capture_file(folder, name="synthetic.csv", *, park="blackbird", n=80,
                 turns=False, quaternions=True, spike=False, amplitude=1.0):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    fields = ["ride", "elapsed_s", "wall_time", "pos_y", "vertical_g_heave"]
    if quaternions:
        fields += ["quat_x", "quat_y", "quat_z", "quat_w"]
    fields += ["norm_" + a for a in ("surge", "sway", "heave", "roll", "pitch", "yaw")]
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for i in range(n):
            phase = i / max(1, n - 1) * 2 * math.pi
            angle = phase if turns else .85 * math.sin(phase)
            row = dict(ride=ride_config.PARKS[park]["name"], elapsed_s=i*.05 + (i % 3)*.001,
                wall_time="2026-11-05T00:00:00", pos_y=22 + 15 * math.sin(phase),
                vertical_g_heave=.2 * math.sin(phase * 1.5),
                norm_surge=.18 * math.sin(phase), norm_sway=.22 * math.cos(phase),
                norm_heave=.4 * math.sin(phase), norm_roll=-amplitude * .6 * math.atan2(math.sin(angle),math.cos(angle)),
                norm_pitch=.3 * math.cos(phase * .8), norm_yaw=.3 * math.sin(phase * .7))
            if spike and i == n // 2 + 7:
                row["norm_roll"], row["norm_pitch"], row["norm_yaw"] = 1.7, 1.4, 1.1
            if quaternions:
                row.update(quat_x=0,quat_y=0,quat_z=math.sin(angle/2),quat_w=math.cos(angle/2))
            writer.writerow(row)
    return path


def base(platform="chair_v3"):
    store = VariantStore.load_readonly(platform)
    return store, store.snapshot("Blackbird")


@pytest.mark.parametrize("platform", ["chair_v3", "platform_v2"])
@pytest.mark.parametrize("cue", ["euler", "sine", "gravity"])
@pytest.mark.parametrize("filter_kind", ["disabled", "classical", "legacy"])
def test_precomputed_motion_matches_normal_player_exact_pipeline(tmp_path, platform, cue, filter_kind):
    path = capture_file(tmp_path, turns=True, spike=True)
    capture = read_input(path)
    store, snapshot = base(platform)
    settings = current_poc_global_settings()
    settings.update(washout_type=filter_kind, enabled=[True]*6)
    snapshot["roll_washout"], snapshot["pitch_washout"] = True, False
    gains = (5, 65, 135, 195, 0, 110)
    prepared = prepare_normalized(capture, snapshot, cue, settings)
    record = load_recording(path, platform=platform, variant_store=store,
                           variant_label="Blackbird", variant_snapshot=snapshot,
                           cue_mode=cue, gain_percent=gains, global_settings=settings)
    expected = np.asarray([f.processed_axes for f in record.frames])
    assert np.allclose(prepared * (np.array(gains)/100), expected, atol=2.e-12, rtol=0)
    # Filtering was performed on every original, nonuniformly timed sample.
    assert len(expected) == len(capture.rows)


@pytest.mark.parametrize("axis", ["roll", "pitch"])
def test_full_rotation_gate_and_yaw_exclusion(axis):
    angles = np.linspace(0, 2*math.pi, 181)
    qs = [(math.sin(a/2),0,0,math.cos(a/2)) if axis == "pitch" else
          (0,0,math.sin(a/2),math.cos(a/2)) for a in angles]
    assert rotation_evidence(qs)["full_rotation"]
    ordinary = [(0,0,math.sin(a/2),math.cos(a/2)) for a in np.linspace(-.6,.6,60)]
    yaw = [(0,math.sin(a/2),0,math.cos(a/2)) for a in angles]
    assert not rotation_evidence(ordinary)["full_rotation"]
    assert not rotation_evidence(yaw)["full_rotation"]
    assert not rotation_evidence(None)["known"]


def test_cue_search_only_rotating_and_missing_quaternion_not_silently_faked(tmp_path):
    normal = read_input(capture_file(tmp_path, "ordinary.csv"))
    rotating = read_input(capture_file(tmp_path,"rotating.csv",turns=True))
    old = read_input(capture_file(tmp_path,"old.csv",quaternions=False))
    _, snapshot = base()
    assert choose_cues([normal], snapshot)[0] == ("euler",)
    assert set(choose_cues([rotating], snapshot)[0]) == {"euler","sine","gravity"}
    assert choose_cues([old], snapshot)[0] == ("euler",)
    with pytest.raises(ValueError,match="quaternion"):
        choose_cues([old],snapshot,force=True)
    with pytest.raises(ValueError,match="quaternion"):
        choose_cues([old],dict(snapshot,angular_cue="gravity"))


@pytest.mark.parametrize("stroke,allowed", [(200,10),(250,12.5)])
def test_five_percent_is_worst_magnitude_not_duration(stroke, allowed):
    p = Parameters("euler",(100,)*6,0)
    e = Evaluation(p,(.2,.1,.05),(.1,)*6,allowed,0,0,0)
    assert e.feasible(.05*stroke)
    assert not e.feasible(0)
    assert not replace(e,excess_mm=allowed+.01).feasible(.05*stroke)
    assert not replace(e,floor_deficit_mm=.001).feasible(.05*stroke)
    assert not replace(e,neutral_valid=False).feasible(.05*stroke)


def test_primary_tier_cannot_be_bought_by_huge_low_priority_motion():
    p = Parameters("euler",(100,)*6,0)
    a=Evaluation(p,(.40,.01,.01),(.1,)*6,0,0,0,0)
    b=replace(a,scores=(.38,.9,.9))
    assert a.rank(0)>b.rank(0)
    c=replace(a,scores=(.40,.11,.0))
    d=replace(a,scores=(.40,.09,.99))
    assert c.rank(0)>d.rank(0)


@pytest.mark.parametrize("platform", ["chair_v3", "platform_v2"])
def test_all_frame_search_export_reopen_and_manual_tuning(tmp_path,monkeypatch,platform):
    path=capture_file(tmp_path/"inputs",n=120,spike=True)
    original=path.read_bytes()
    live=tmp_path/"live_catalog"
    monkeypatch.setenv("POC_RIDE_VARIANTS_DIR",str(live))
    # Any attempted network output would make the test fail.
    monkeypatch.setattr(socket,"socket",lambda *a,**k: (_ for _ in ()).throw(AssertionError("Network output")))
    capture=read_input(path)
    report=optimise_group([capture],platform,tmp_path/"result",
                          SearchOptions(evaluations=150,sample_frames=12,alternatives=0),progress=lambda s:None)
    assert not report["failures"]
    assert {c["target"] for c in report["candidates"]} == {"strict","relaxed5"}
    assert not live.exists()
    assert path.read_bytes()==original
    for result in report["candidates"]:
        metric=result["metrics"]["captures"][0]
        assert metric["qualifies"] and metric["frames"]==120
        assert metric["floor_violation_frames"]==0
        assert metric["maximum_excess_mm"] <= (0 if result["target"]=="strict" else .05*metric["stroke_mm"])+1e-7
        study=Path(result["csv_files"][0])
        replay=load_recording(study,default_standard_variant=True)
        assert replay.platform_key==platform
        assert replay.variant_label==result["label"]
        assert replay.gain_percent==tuple(result["parameters"]["gains"])
        assert replay.variant_snapshot["neutral_height_delta_mm"]==result["parameters"]["neutral_mm"]
        with study.open(newline="") as stream: rows=list(csv.DictReader(stream))
        for original_row,output_row,frame in zip(capture.csv_rows,rows,replay.frames):
            for key in capture.csv_fields:
                assert output_row[key]==original_row[key]
            for muscle in range(6):
                assert float(output_row[f"opt_M{muscle+1}_requested_mm"])==pytest.approx(frame.analysis["requested"][muscle],abs=1e-8)
        # Changing a slider uses explicit options, NOT restored candidate defaults.
        manual=load_recording(study,platform=platform,variant_label=replay.variant_label,
              variant_snapshot=replay.variant_snapshot,cue_mode=replay.cue_mode,
              gain_percent=(0,)*6,global_settings=replay.global_settings_snapshot)
        assert all(max(abs(x) for x in f.processed_axes)==0 for f in manual.frames)
        assert manual.gain_percent==(0,)*6
        # Opening the candidate uses frozen global gains, not a later live INI.
        import tools.sim_player.tuning as tuning
        changed=current_poc_global_settings();changed["master_gain"]=10
        monkeypatch.setattr(tuning,"current_poc_global_settings",lambda:changed)
        again=load_recording(study,default_standard_variant=True)
        assert [f.muscle_lengths_mm for f in again.frames]==[f.muscle_lengths_mm for f in replay.frames]


def test_missing_or_tampered_companion_is_not_raw_replay(tmp_path):
    path=capture_file(tmp_path/"input",n=30)
    report=optimise_group([read_input(path)],"chair_v3",tmp_path/"out",
                          SearchOptions(evaluations=80,sample_frames=12,alternatives=0),progress=lambda s:None)
    study=Path(report["candidates"][0]["csv_files"][0])
    sidecar=bundle_path(study)
    stored=sidecar.read_bytes(); sidecar.unlink()
    with pytest.raises(ValueError,match="Missing"):
        load_recording(study,default_standard_variant=True)
    sidecar.write_bytes(stored)
    study.write_text(study.read_text()+"\n")
    with pytest.raises(ValueError,match="changed"):
        load_recording(study,default_standard_variant=True)


def test_joint_fit_checks_outlier_capture_and_reports_both(tmp_path):
    first=read_input(capture_file(tmp_path,"first.csv",n=80))
    second=read_input(capture_file(tmp_path,"spike.csv",n=80,spike=True,amplitude=1.9))
    result=optimise_group([first,second],"chair_v3",tmp_path/"out",
                         SearchOptions(evaluations=160,sample_frames=12,alternatives=0),progress=lambda s:None)
    assert result["capture_count"]==2 and result["joint_validation"]
    for item in result["candidates"]:
        assert len(item["csv_files"])==2
        assert all(m["qualifies"] for m in item["metrics"]["captures"])


def test_readonly_catalog_does_not_seed_and_bad_working_catalog_not_replaced(tmp_path):
    root=tmp_path/"catalogs"
    catalog=VariantStore.load_readonly("chair_v3",directory=root)
    assert catalog.path is None and not root.exists()
    root.mkdir();bad=root/"chair_v3.json";bad.write_text("not JSON")
    with pytest.raises(ValueError):VariantStore.load_readonly("chair_v3",directory=root)
    assert bad.read_text()=="not JSON"


def test_grandnational_hidden_from_operator_not_research_or_capture():
    catalog=VariantStore.load_readonly("chair_v3")
    assert "GrandNational" not in catalog.visible_labels()
    assert "GrandNational" in catalog.player_labels()
    assert default_label_for_capture("GrandNational",catalog)=="GrandNational"
    assert "GrandNational" in VariantStore.for_capture("chair_v3").visible_labels()
    from sims.nolimits2.sim import Sim
    from tests.test_nolimits2_adapter import FakeNL2Client
    operator = Sim(client=FakeNL2Client(), platform_key="chair_v3", variant_store=catalog)
    capture = Sim(client=FakeNL2Client(), capture_mode=True)
    assert "GrandNational" not in operator.get_experiences()
    assert "GrandNational" in capture.get_experiences()


def test_source_validation_malformed_time_nan_and_wrong_ride(tmp_path):
    good=capture_file(tmp_path)
    text=good.read_text()
    good.write_text(text.replace("Blackbird","Unsupported Ride"))
    with pytest.raises(ValueError,match="Unrecognised"):read_input(good)
    good=capture_file(tmp_path)
    rows=list(csv.DictReader(good.open()))
    rows[1]["elapsed_s"]=rows[0]["elapsed_s"]
    with good.open("w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    with pytest.raises(ValueError,match="increase"):read_input(good)
    good=capture_file(tmp_path)
    good.write_text(good.read_text().replace("22.0","nan",1))
    with pytest.raises(ValueError,match="nonfinite"):read_input(good)


def test_discovery_ignores_events_results_and_lists_missing_without_writes(tmp_path,capsys):
    inp=tmp_path/"inputs";capture_file(inp,n=20)
    (inp/"some_state_events.csv").write_text("ride,elapsed_s\n")
    (inp/"summary.csv").write_text("park,target,score\n")
    paths,issues=discover_inputs([inp])
    assert len(paths)==1 and any(i["reason"]=="Not an NL2 ride-study CSV" for i in issues)
    assert main([str(inp),"--list","--output",str(tmp_path/"out")])==0
    assert not (tmp_path/"out").exists()
    assert "Missing captures" in capsys.readouterr().out


def test_deterministic_search_for_same_capture(tmp_path):
    capture=read_input(capture_file(tmp_path,n=40))
    _,snapshot=base()
    options=SearchOptions(evaluations=90,sample_frames=12,alternatives=0)
    baseline=Parameters("euler",(100,)*6,0)
    results=[]
    for _ in range(2):
        evaluator=Evaluator([capture],"chair_v3",snapshot,("euler",),current_poc_global_settings(),options)
        found=search_candidates(evaluator,baseline,[baseline])
        results.append({k:[v.parameters for v in values] for k,values in found.items()})
    assert results[0]==results[1]


def test_reject_corrupt_global_settings_and_include_inherited_geometry():
    settings=current_poc_global_settings()
    assert checked_global_settings(settings)==settings
    bad=deepcopy(settings);bad["tau"][0]=float("nan")
    with pytest.raises(ValueError):checked_global_settings(bad)
    bad=deepcopy(settings);bad["gains"][0]=500
    with pytest.raises(ValueError):checked_global_settings(bad)
    sources=model_fingerprint("chair_v3")
    assert "kinematics.cfg_SuspendedChair" in sources
    assert "kinematics.cfg_SuspendedChairV3" in sources


def test_actual_worker_opens_candidate_with_platform_and_no_gain_reapplication(tmp_path):
    report=optimise_group([read_input(capture_file(tmp_path/"input",n=25))],"chair_v3",tmp_path/"out",
                         SearchOptions(evaluations=80,sample_frames=12,alternatives=0),progress=lambda s:None)
    path=Path(report["candidates"][0]["csv_files"][0])
    source=Path(__file__).resolve().parents[1]/"app.py"
    tree=ast.parse(source.read_text())
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=="LoadWorker")
    method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=="run")
    namespace={"load_recording":load_recording}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[method],type_ignores=[])),str(source),"exec"),namespace)
    loaded=[];failed=[]
    worker=SimpleNamespace(csv_path=path,platform_key="platform_v2",variant_store=None,
        cue_mode="euler",gain_percent=(100,)*6,variant_label=None,variant_snapshot=None,
        default_standard_variant=True,global_settings=None,
        loaded=SimpleNamespace(emit=loaded.append),failed=SimpleNamespace(emit=failed.append))
    namespace["run"](worker)
    assert not failed and loaded[0].platform_key=="chair_v3"
    assert loaded[0].gain_percent==tuple(report["candidates"][0]["parameters"]["gains"])


def test_experiment_application_forwards_archived_globals_without_live_changes():
    source=Path(__file__).resolve().parents[1]/"app.py"
    tree=ast.parse(source.read_text())
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=="ReplayWindow")
    method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=="_apply_experiment_values")
    from tools.sim_player.tuning import validate_gain_percent
    namespace={"validate_gain_percent":validate_gain_percent}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[method],type_ignores=[])),str(source),"exec"),namespace)
    calls=[]
    ui=SimpleNamespace(platform_key="chair_v3",
        tuning_panel=SimpleNamespace(set_gains=lambda x: None),
        cue_box=SimpleNamespace(blockSignals=lambda x: None,findData=lambda x:0,setCurrentIndex=lambda x:None),
        _request_reanalysis=lambda *args,**kwargs:calls.append((args,kwargs)))
    settings=current_poc_global_settings();settings["master_gain"]=50
    before=deepcopy(settings)
    namespace["_apply_experiment_values"](ui,"sine",(50,)*6,
        platform_key="chair_v3",global_settings=settings)
    assert calls[0][1]["global_settings"]==before and settings==before
    # Older UI callers can still omit the snapshot and retain the loaded study.
    namespace["_apply_experiment_values"](ui,"sine",(50,)*6)
    assert "global_settings" not in calls[1][1]


@pytest.mark.parametrize("platform",["chair_v3","platform_v2"])
def test_full_rotation_batch_rechecks_exported_cue_and_target(tmp_path,platform):
    capture=read_input(capture_file(tmp_path/"input",turns=True,n=120))
    result=optimise_group([capture],platform,tmp_path/"output",
        SearchOptions(evaluations=180,sample_frames=14,alternatives=1),progress=lambda s:None)
    assert set(result["cue_modes_tested"])=={"euler","sine","gravity"}
    assert {item["target"] for item in result["candidates"]}=={"strict","relaxed5"}
    for item in result["candidates"]:
        replay=load_recording(item["csv_files"][0],default_standard_variant=True)
        assert replay.cue_mode==item["parameters"]["cue"]
        metrics=recording_summary(replay,item["metrics"]["captures"][0]["floor_required_mm"])
        assert metrics["maximum_excess_mm"]<=(0 if item["target"]=="strict" else .05*(replay.max_length_mm-replay.min_length_mm))+1e-6
        assert metrics["floor_violation_frames"]==0
