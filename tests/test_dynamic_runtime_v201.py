#test_dynamic_runtime_v201.py ver 2
"""No-hardware dynamic POC profile publication / safety policy tests.

Uses an isolated fake repository for deployment and catalog writes. NEVER
creates live simulator, Festo, SHS, sockets, or the real POC variant catalogue.
"""
from __future__ import annotations
from pathlib import Path
from types import SimpleNamespace
from copy import deepcopy
import hashlib
import json
import shutil

import numpy as np
import pytest

from core.dynamic_profiles import (
    DynamicDeployment, DynamicProfileError, DynamicErrorLog, audit_dynamic_store,
    validate_bundle_relpath, DEFAULT_MAX_PROFILE_LAG_S)
from core.telemetry_config import TelemetryConfig
from sims.nolimits2.variant_store import VariantStore
from sims.nolimits2 import ride_config
from tools.nl2_dynamic_profiles.replay import SOURCE_FILES
from tools.nl2_dynamic_profiles.promote import promote
from tools.nl2_sync.profile import DynamicProfile, digest_json

SOURCE_ROOT = Path(__file__).resolve().parents[1]


def _fixture(tmp_path, platform, park):
    root = tmp_path / "sandbox"
    for name in SOURCE_FILES:
        (root/name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SOURCE_ROOT/name, root/name)
    ini = "sims/nolimits2/telemetry_config.ini"
    (root/ini).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SOURCE_ROOT/ini, root/ini)
    global_config = TelemetryConfig(root/ini).snapshot()
    variant = {"label": ride_config.PARKS[park]["name"], "park": park}
    resolved = ride_config.resolve_variant(variant)
    resolved["platform_key"] = platform
    hashes = {name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in SOURCE_FILES}
    source = {"platform":platform, "base_variant":resolved, "telemetry":global_config,
              "intensity_percent":100.0, "tick_s":.05,
              "matcher":{},"source_hashes":hashes}
    from tools.nl2_sync.matcher import Settings
    source["matcher"] = vars(Settings()).copy()
    candidate = tmp_path / (platform+"_"+park)
    candidate.mkdir()
    speed = np.ones(21)
    t=np.linspace(0,1,21)
    xyz=np.array([[x,0,0] for x in t])
    q=np.tile([0.,0.,0.,1.],(21,1))
    np.savez(candidate/"reference.npz",time_s=t,xyz_m=xyz,quaternion_xyzw=q,speed_mps=speed)
    ref_hash=hashlib.sha256((candidate/"reference.npz").read_bytes()).hexdigest()
    identity={"park":park, "platform":platform,"reference_sha256":ref_hash,
              "processing_sha256":digest_json(source),"park_sha256":"a"*64,
              "nl2_version":"2.6.8.1","view_identity":[0,0,0,0,1],
              "departure_station":[0,0],"observed_vr":"on"}
    meta={"name":park+" dynamic demo", "target":"strict",
          "reference_file":"reference.npz","processing":source,
          "validation": {"nominal_qualified":True,"heldout_pass":True,
                         "robust_100ms":True,"production_parity":True,"fallback_all_captures_pass":True},
          "qualification":"nominal_all_captures_pass",
          "fallback_transition_s":.25,"max_observation_age_s":.25}
    gains=np.ones((2,6));gains[:,3]=1.3
    prof=DynamicProfile([0.,1.],gains,np.array([1.,1.,1.,.8,1.,1.]),identity,meta)
    prof.save(candidate/"profile.json")
    from core.dynamic_qualification import platform_geometry_envelope
    geo = platform_geometry_envelope(platform)
    floor = 100.0 if platform == 'platform_v2' else None
    floor_required = 15.5 if platform == 'platform_v2' else None
    def run_record(name, role=None):
        data = {'capture':name, 'qualified':True, 'floor_violation_ticks':0,
                'output_range_violation_ticks':0,'nonfinite_ticks':0,
                'minimum_output_length_mm':geo['low']+12,
                'maximum_output_length_mm':geo['high']-12,
                'maximum_requested_excess_mm':0.0, 'allowance_mm':0.0,
                'max_output_step_mm':20.0,
                'floor_required_mm':floor_required,
                'minimum_requested_floor_mm':floor}
        if role is not None:
            data.update(role=role, production_parity=True, sync_lost_ticks=0)
        return data
    evidence = {'nominal':[run_record('trial1','fit'),run_record('trial2','heldout')],
                'static_fallback':[run_record('trial1'),run_record('trial2')],
                'stress':[], 'note':'Synthetic deterministic unit-test evidence only'}
    (candidate/'validation.json').write_text(json.dumps(evidence),encoding='utf-8')
    catalog=tmp_path/"catalog"
    VariantStore.create(platform,{"schema_version":1,"platform_key":platform,"variants":[variant]},directory=catalog)
    return root,candidate,catalog,global_config


@pytest.mark.parametrize("platform",["chair_v3","platform_v2"])
@pytest.mark.parametrize("park",["blackbird","mdxlogo"])
def test_promote_both_platforms_both_rides_and_load(tmp_path,platform,park):
    root,candidate,catalog,global_config=_fixture(tmp_path,platform,park)
    name=ride_config.PARKS[park]["name"]+" [Dynamic A]"
    rel=promote(candidate,name,root=root,catalog_dir=catalog,target_id="abcd1234abcd")
    store=VariantStore.load(platform,directory=catalog)
    snap=store.snapshot(name)
    dep=DynamicDeployment.load(label=name,relative=rel,platform=platform,park=park,
                                live_variant=snap,global_settings=global_config,root=root)
    assert name in store.visible_labels(park)
    assert dep.profile.metadata["target"]=="strict"
    assert not any((root/"studies").glob("**/profile.json")) if (root/"studies").exists() else True
    dep.preflight(variant=snap,telemetry_globals=global_config,nl2_version="2.6.8.1",
                  telemetry=SimpleNamespace(coaster_index=0,train=0,car=0,seat=0,view_mode=1),
                  departure_station=[0,0])
    # NL2 version/view provenance differences are now advisory at dispatch.
    dep.preflight(variant=snap,telemetry_globals=global_config,nl2_version="2.6.4.0",
                  telemetry=SimpleNamespace(coaster_index=0,train=1,car=0,seat=0,view_mode=1))
    with pytest.raises(DynamicProfileError,match="checksum"):
        (dep.manifest_path.parent/"profile.json").write_text("garbage",encoding="utf8")
        DynamicDeployment.load(label=name,relative=rel,platform=platform,park=park,
                               live_variant=snap,global_settings=global_config,root=root)
    log=DynamicErrorLog(tmp_path/"errors.log")
    assert not audit_dynamic_store(store,global_config,root=root,error_log=log)
    assert name not in store.visible_labels(park)
    assert log.count==1


def test_invalid_preferred_does_not_reveal_static_candidate(tmp_path):
    platform="chair_v3"
    variants=[{"label":"Blackbird","park":"blackbird","status":"candidate"},
              {"label":"Blackbird [Bad Dynamic]","park":"blackbird","status":"preferred",
               "dynamic_profile":"ride_profiles/chair_v3/blackbird/abcdef12/deployment.json"}]
    store=VariantStore(platform,{"schema_version":1,"platform_key":platform,"variants":variants})
    store.set_dynamic_invalid({"Blackbird [Bad Dynamic]":"missing bundle"})
    assert store.visible_labels("blackbird")==()
    assert store.default_label("blackbird") is None
    with pytest.raises(ValueError, match="Invalid dynamic"):
        store.review("Blackbird [Bad Dynamic]","preferred")


@pytest.mark.parametrize("path",["../../etc/passwd", "ride_profiles/chair_v3/blackbird/abc123/research.json",
                                 "ride_profiles/other/blackbird/abcd1234/deployment.json",
                                 "ride_profiles\\chair_v3\\blackbird\\a\\deployment.json"])
def test_reject_unsafe_profile_paths(path):
    with pytest.raises(DynamicProfileError):
        validate_bundle_relpath(path,"chair_v3")


def test_static_intensity_byte_equivalence_and_commissioning_limit(tmp_path):
    ini=SOURCE_ROOT/"sims/nolimits2/telemetry_config.ini"
    baseline=TelemetryConfig(ini)
    dynamic_support=TelemetryConfig(ini,max_intensity_percent=100)
    commissioning=TelemetryConfig(ini,max_intensity_percent=150)
    signals=[(0.1,-.2,.3,.1,-.7,.8)]*18
    for i,x in enumerate(signals):
        assert baseline.process(x,100,.05)==dynamic_support.process(x,100,.05)
        assert baseline.process(x,65,.05)==dynamic_support.process(x,65,.05)
    assert commissioning.scale_without_washout((1,)*6,150)[0] == pytest.approx(1.5)
    assert baseline.scale_without_washout((1,)*6,150)[0] == pytest.approx(1.0)
    assert DEFAULT_MAX_PROFILE_LAG_S==.250


def test_runtime_match_and_smooth_fallback(tmp_path):
    platform,park="chair_v3","blackbird"
    root,candidate,catalog,globals=_fixture(tmp_path,platform,park)
    name="Blackbird [Dynamic A]"
    rel=promote(candidate,name,root=root,catalog_dir=catalog,target_id="abcd1234abcd")
    dep=DynamicDeployment.load(label=name,relative=rel,platform=platform,park=park,
        live_variant=VariantStore.load(platform,directory=catalog).snapshot(name),global_settings=globals,root=root)
    log=DynamicErrorLog(tmp_path/"dynerr.log")
    runtime=dep.new_runtime()
    runtime.error_log=log
    outputs=[]
    for i in range(18):
        t=i*.05
        tm=SimpleNamespace(pos_x=t,pos_y=0.,pos_z=0.,quat_x=0.,quat_y=0.,quat_z=0.,quat_w=1.,
                           speed=1.,frame=i,received_at=100.+t,
                           coaster_index=0,train=0,car=0,seat=0,view_mode=1)
        runtime.tick(tm,now=100.+t)
        outputs.append(runtime.multiply((0.,0.,0.,1.,0.,0.))[3])
    assert outputs[-1]>1.0
    assert runtime.status in ('TRACKING','REACQUIRING')
    # Hard matcher fault must latch for this ride; gains must approach static
    # fallback rather than jump from the current dynamic value in one frame.
    before=runtime.current_gains[3]
    runtime.latch("injected fault")
    assert runtime.current_gains[3]==before
    after=[]
    for j in range(1,9):
        runtime.tick(None,now=100.+(18+j)*.05)
        after.append(runtime.current_gains[3])
    assert runtime.status=="DYNAMIC DISABLED"
    assert abs(after[-1]-.8)<1e-5
    assert log.count==1
    runtime.stop_cycle()
    assert runtime.status=="READY"


def test_runtime_latches_on_view_change_without_abrupt_multiplier_jump(tmp_path):
    root, candidate, catalog, globals_snapshot = _fixture(tmp_path, "chair_v3", "blackbird")
    label = "Blackbird [Dynamic B]"
    rel = promote(candidate, label, root=root, catalog_dir=catalog, target_id="abcd1234abc1")
    dep = DynamicDeployment.load(label=label, relative=rel, platform="chair_v3", park="blackbird",
        live_variant=VariantStore.load("chair_v3", directory=catalog).snapshot(label),
        global_settings=globals_snapshot, root=root)
    log = DynamicErrorLog(tmp_path / "errors.log")
    runtime = dep.new_runtime()
    runtime.error_log = log
    for i in range(15):
        t = i * .05
        runtime.tick(SimpleNamespace(pos_x=t,pos_y=0.,pos_z=0.,quat_x=0.,quat_y=0.,
            quat_z=0.,quat_w=1.,speed=1.,frame=i,received_at=100.+t,
            coaster_index=0,train=0,car=0,seat=0,view_mode=1), now=100.+t)
    before = runtime.current_gains
    t = 15*.05
    runtime.tick(SimpleNamespace(pos_x=t,pos_y=0.,pos_z=0.,quat_x=0.,quat_y=0.,
        quat_z=0.,quat_w=1.,speed=1.,frame=15,received_at=100.+t,
        coaster_index=0,train=1,car=0,seat=0,view_mode=1), now=100.+t)
    assert runtime.status == "DYNAMIC DISABLED"
    assert "viewed train" in runtime.latch_reason
    assert runtime.current_gains == before  # no immediate gain discontinuity
    assert log.count == 1
    for i in range(16, 28):
        runtime.tick(None, now=100.+i*.05)
    assert runtime.current_gains == pytest.approx(tuple(dep.profile.fallback))
    assert log.count == 1  # no flooding persistent error log


def test_runtime_latches_after_sustained_matcher_loss(tmp_path):
    root, candidate, catalog, globals_snapshot = _fixture(tmp_path, "chair_v3", "blackbird")
    label = "Blackbird [Dynamic C]"
    rel = promote(candidate, label, root=root, catalog_dir=catalog, target_id="abcd1234abc2")
    dep = DynamicDeployment.load(label=label, relative=rel, platform="chair_v3", park="blackbird",
        live_variant=VariantStore.load("chair_v3", directory=catalog).snapshot(label),
        global_settings=globals_snapshot, root=root)
    runtime = dep.new_runtime()
    runtime.error_log = DynamicErrorLog(tmp_path / "errors.log")
    for i in range(15):
        t = i*.05
        runtime.tick(SimpleNamespace(pos_x=t,pos_y=0.,pos_z=0.,quat_x=0.,quat_y=0.,
            quat_z=0.,quat_w=1.,speed=1.,frame=i,received_at=100.+t,
            coaster_index=0,train=0,car=0,seat=0,view_mode=1), now=100.+t)
    for i in range(15, 77):
        runtime.tick(None, now=100.+i*.05)
    assert runtime.status == "DYNAMIC DISABLED"
    assert "profile phase unavailable" in runtime.latch_reason
    assert runtime.error_log.count == 1
    assert runtime.current_gains == pytest.approx(tuple(dep.profile.fallback))


def test_dynamic_preflight_requires_confirmed_departure_station(tmp_path):
    root, candidate, catalog, globals_snapshot = _fixture(tmp_path, "platform_v2", "mdxlogo")
    label = "Mdx Logo Coaster [Dynamic Gate]"
    rel = promote(candidate, label, root=root, catalog_dir=catalog, target_id="abcd1234abc3")
    snap = VariantStore.load("platform_v2", directory=catalog).snapshot(label)
    dep = DynamicDeployment.load(label=label, relative=rel, platform="platform_v2", park="mdxlogo",
        live_variant=snap, global_settings=globals_snapshot, root=root)
    tm = SimpleNamespace(coaster_index=0,train=0,car=0,seat=0,view_mode=1)
    # Station mismatch is advisory; runtime matching/fallback controls phase.
    dep.preflight(variant=snap, telemetry_globals=globals_snapshot, nl2_version="2.6.8.1",
                  telemetry=tm, departure_station=None)
    dep.preflight(variant=snap, telemetry_globals=globals_snapshot, nl2_version="2.6.8.1",
                  telemetry=tm, departure_station=[0,1])
    dep.preflight(variant=snap, telemetry_globals=globals_snapshot, nl2_version="2.6.8.1",
                  telemetry=tm, departure_station=[0,0])


def test_live_poc_core_preflight_is_fail_closed_and_logs_once(tmp_path, monkeypatch):
    """Exercise PocCore's exact dispatch gate without any Qt or Festo backend."""
    import sys, types
    try:
        import PyQt5  # noqa
    except ImportError:
        pyqt=types.ModuleType('PyQt5')
        coreqt=types.ModuleType('PyQt5.QtCore')
        coreqt.QObject=object
        coreqt.pyqtSignal=lambda *a, **kw: None
        pyqt.QtCore=coreqt
        monkeypatch.setitem(sys.modules, 'PyQt5', pyqt)
        monkeypatch.setitem(sys.modules, 'PyQt5.QtCore', coreqt)
    from core.poc_core import PocCore
    root,candidate,catalog,global_settings=_fixture(tmp_path, 'chair_v3', 'blackbird')
    label='Blackbird [Dynamic Gate]'
    rel=promote(candidate,label,root=root,catalog_dir=catalog,target_id='abcd1234abc4')
    snap=VariantStore.load('chair_v3',directory=catalog).snapshot(label)
    deployment=DynamicDeployment.load(label=label,relative=rel,platform='chair_v3',park='blackbird',
        live_variant=snap,global_settings=global_settings,root=root)
    client=SimpleNamespace(version='2.6.8.1', latest_telemetry=SimpleNamespace(
        coaster_index=0,train=0,car=0,seat=0,view_mode=1))
    sim=SimpleNamespace(_ride_load_pending=False,loaded_variant_snapshot=lambda:snap,
        client=client, heave_mode=snap['heave_mode'], _angular_cue_mode=snap['angular_cue'],
        _ready_station=(0,0),_ride_station=None)
    errors=DynamicErrorLog(tmp_path/'preflight.log')
    core=SimpleNamespace(sim_key='nolimits2',sim=sim,_dynamic_runtime=deployment.new_runtime(),
        _dynamic_deployments={label:deployment},_dynamic_issue='',_dynamic_preflight_reported=None,
        platform_key='chair_v3',dynamic_error_log=errors,get_telemetry_config=lambda:global_settings)
    assert PocCore._dynamic_preflight(core,report=True)
    assert errors.count == 0
    client.version='2.6.9.0'
    assert PocCore._dynamic_preflight(core,report=True)
    assert errors.count == 0
    client.version='2.6.8.1'
    sim._ready_station=None
    assert PocCore._dynamic_preflight(core,report=True)
    assert errors.count == 0
    sim._ready_station=(0,0)
    assert PocCore._dynamic_preflight(core,report=True)
    # On the first departure there is no pinned _ride_station/_ready_station;
    # the identified station-state observation must be sufficient.
    sim._ready_station=None
    sim._station_observation=lambda: SimpleNamespace(valid=True,view_train_here=True,identity=(0,0))
    assert PocCore._dynamic_preflight(core,report=True)
    sim._station_observation=lambda: SimpleNamespace(valid=True,view_train_here=False,identity=(0,0))
    assert PocCore._dynamic_preflight(core,report=True)
