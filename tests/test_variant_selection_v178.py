# test_variant_selection_v178.py ver 2
"""Real NL2 adapter with a fake network client: no hardware and no live NL2."""
from types import SimpleNamespace
import ast
from pathlib import Path
import logging
import pytest

from core.types import PlatformState, ExperienceState
from core.telemetry_config import TelemetryConfig
from sims.nolimits2.sim import Sim
from tests.test_nolimits2_adapter import FakeNL2Client
from tests.test_variant_store_v178 import writable


def load(sim, client, label):
    sim.select_experience(sim.get_experiences().index(label))
    if sim._ride_load_pending:
        client.complete_request(sim._ride_load_request_id, ok=True)
        sim._service_ride_load_result()


def connected(tmp_path):
    store = writable(tmp_path)
    client = FakeNL2Client()
    sim = Sim(client=client, variant_store=store)
    sim.connect(); sim.service()
    client.complete_request(sim._ride_load_request_id, ok=True)
    sim._service_ride_load_result()
    return sim, client


def test_reject_loaded_variant_preserves_settings_and_stable_indices(tmp_path):
    sim, client=connected(tmp_path)
    load(sim,client,'Blackbird [Sine]')
    sim._ride_station=(3,4)
    sim._ready_station=(3,4)
    before=(sim._loaded_index,sim._selected,sim._angular_cue_mode,sim._heave_mode,
            sim._ride_station,sim._ready_station)
    commands=list(client.commands)
    sim.review_current_variant('rejected','Blackbird [Sine]')
    assert 'Blackbird [Sine]' not in sim.get_experiences()
    assert sim.get_selected_experience_index() == -1
    assert sim.loaded_variant_label == 'Blackbird [Sine]'
    assert sim.loaded_variant_snapshot()['angular_cue'] == 'sine'
    assert before==(sim._loaded_index,sim._selected,sim._angular_cue_mode,sim._heave_mode,
                    sim._ride_station,sim._ready_station)
    assert client.commands==commands
    assert sim.get_status().entity_name=='Blackbird [Sine]'
    sim.state=ExperienceState.READY
    sim._stopped_in_station=lambda: True
    sim._manual_mode_is_set=lambda: True
    assert sim.is_ready_to_start()  # No new activation interlock introduced.


def test_visible_index_maps_correctly_after_removing_prior_items(tmp_path):
    sim,client=connected(tmp_path)
    sim.review_current_variant('rejected',sim.loaded_variant_label)
    assert sim.get_experiences()[0]=='Blackbird [Sine]'
    assert sim.park_name_for_experience(0)=='Blackbird'
    commands=len(client.commands)
    sim.select_experience(0)
    assert sim.loaded_variant_label=='Blackbird [Sine]'
    assert len(client.commands)==commands  # Same underlying park, no reload.
    load(sim,client,'GrandNational')
    assert sim.loaded_variant_label=='GrandNational'
    assert sim.get_selected_experience_index()==sim.get_experiences().index('GrandNational')


def test_preferred_rejects_competitors_no_station_reset(tmp_path):
    sim,client=connected(tmp_path)
    load(sim,client,'Blackbird [Gravity]')
    sim._ride_station=(7,2)
    commands=list(client.commands)
    sim.review_current_variant('preferred','Blackbird [Gravity]')
    assert sim.get_experiences()==('Blackbird [Gravity]','GrandNational')
    assert sim.get_selected_experience_index()==0
    assert sim._ride_station==(7,2)
    assert client.commands==commands
    restarted=Sim(client=FakeNL2Client(),variant_store=sim.variant_store)
    assert restarted._experiences[restarted._selected]=='Blackbird [Gravity]'


def test_no_review_while_loading_or_with_old_confirmation_label(tmp_path):
    sim,client=connected(tmp_path)
    sim.select_experience(sim.get_experiences().index('GrandNational'))
    old=sim.variant_store.path.read_bytes()
    with pytest.raises(RuntimeError):
        sim.review_current_variant('rejected','Blackbird')
    assert sim.variant_store.path.read_bytes()==old
    client.complete_request(sim._ride_load_request_id,ok=True);sim._service_ride_load_result()
    with pytest.raises(RuntimeError):
        sim.review_current_variant('rejected','Blackbird')
    assert sim.variant_store.path.read_bytes()==old


def test_empty_catalog_does_not_load_or_reset_unrelated_nl2_park(tmp_path):
    s=writable(tmp_path)
    for label in s.labels():s.review(label,'rejected')
    client=FakeNL2Client();sim=Sim(client=client,variant_store=s)
    sim.connect();sim.service();sim.prepare_for_activation()
    assert sim.get_experiences()==()
    assert sim.get_selected_experience_index()==-1
    assert not sim.is_ready_to_start()
    assert not any(cmd[0] in ('load_park','reset_park') for cmd in client.commands)
    assert 'No available' in sim.get_status().text
    assert not sim.read().telemetry_valid


def test_recorder_is_independent_of_all_reviews(tmp_path,monkeypatch):
    s=writable(tmp_path)
    for label in s.labels():s.review(label,'rejected')
    monkeypatch.setenv('POC_RIDE_VARIANTS_DIR',str(tmp_path))
    sim=Sim(client=FakeNL2Client(),capture_mode=True)
    assert len(sim.get_experiences())==10
    assert sim.get_experiences()[1]=='Blackbird'


def extract_core():
    path=Path(__file__).resolve().parents[1]/'core/poc_core.py'
    tree=ast.parse(path.read_text())
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='PocCore')
    names={'can_review_variant','variant_review_info','review_variant','_sync_nl2_angular_washout'}
    methods=[n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name in names]
    ns={'PlatformState':PlatformState,'log':logging.getLogger('test')}
    exec(compile(ast.fix_missing_locations(ast.Module(body=methods,type_ignores=[])),str(path),'exec'),ns)
    return {n:ns[n] for n in names}


def fake_core(tmp_path):
    sim,client=connected(tmp_path)
    height_backend = SimpleNamespace(ride_neutral_height_delta_mm=0.0)
    height_backend.set_ride_neutral_height_delta_mm = lambda value: setattr(
        height_backend, 'ride_neutral_height_delta_mm', float(value))
    core=SimpleNamespace(sim_key='nolimits2',sim=sim,platform_key='platform_v2',
       platform=SimpleNamespace(state=PlatformState.DEACTIVATED,parked=True,
                                backend=height_backend),
       telemetry_config=TelemetryConfig(tmp_path/'global.ini'),_angular_washout_ride=None,
       _motion_transform=(0,)*6)
    core.messages=[];core.selections=[];core.statuses=[]
    core.message=SimpleNamespace(emit=core.messages.append)
    core.experiencesChanged=SimpleNamespace(emit=lambda *a:core.selections.append(a))
    core.simStatusChanged=SimpleNamespace(emit=core.statuses.append)
    core.get_sim_status=sim.get_status
    for name,fn in extract_core().items():setattr(core,name,fn.__get__(core))
    return core,client


@pytest.mark.parametrize('state,parked',[(PlatformState.ACTIVATED,True),(PlatformState.DEACTIVATED,False),
                                       (PlatformState.ACTIVATING,True),(PlatformState.DEACTIVATING,True)])
def test_core_refuses_review_if_platform_no_longer_parked_and_inactive(tmp_path,state,parked):
    core,_=fake_core(tmp_path)
    label=core.sim.loaded_variant_label
    old=core.sim.variant_store.path.read_bytes()
    core.platform.state=state;core.platform.parked=parked
    assert not core.review_variant(label,'preferred')
    assert core.sim.variant_store.path.read_bytes()==old
    assert core.selections==[]


def test_core_warns_last_variant_and_retains_loaded_tuning_after_rejection(tmp_path):
    core,client=fake_core(tmp_path)
    load(core.sim,client,'GrandNational')
    assert core.variant_review_info()['last_for_park'] is True
    load(core.sim,client,'Blackbird [Sine]')
    assert core.variant_review_info()['last_for_park'] is False
    core._sync_nl2_angular_washout()
    assert core._angular_washout_ride=='Blackbird [Sine]'
    assert core.review_variant('Blackbird [Sine]','rejected')
    core._sync_nl2_angular_washout()
    assert core._angular_washout_ride=='Blackbird [Sine]'
    assert core.selections[-1][1]==-1
    assert not core.can_review_variant()
