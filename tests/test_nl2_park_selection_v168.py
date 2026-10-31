# test_nl2_park_selection_v168.py ver 1
"""Requested park is distinct from the NL2-acknowledged loaded park."""
import time
from core.types import ExperienceState
from sims.nolimits2.sim import Sim
from tests.test_nolimits2_adapter import FakeNL2Client


def connected_sim():
    client = FakeNL2Client()
    sim = Sim(client=client)
    sim.connect()
    sim.service()
    assert sim._ride_load_pending
    return sim, client


def test_select_during_startup_load_is_queued_and_then_sent():
    sim, client = connected_sim()
    startup_id = sim._ride_load_request_id
    sim.select_experience(1)
    assert sim._selected == 1
    assert sim._loaded_index is None
    assert len([c for c in client.commands if c[0] == 'load_park']) == 1
    client.complete_request(startup_id, ok=True)
    sim._service_ride_load_result()
    assert len([c for c in client.commands if c[0] == 'load_park']) == 2
    assert sim._loading_index == 1
    assert sim._loaded_index == 0
    assert sim.state is ExperienceState.RESETTING
    client.complete_request(sim._ride_load_request_id, ok=True)
    sim._service_ride_load_result()
    assert sim._loaded_index == 1
    assert not sim._ride_load_pending


def test_failed_load_can_retry_same_requested_park():
    sim, client = connected_sim()
    client.complete_request(sim._ride_load_request_id, ok=True)
    sim._service_ride_load_result()
    sim.select_experience(1)
    failed_id = sim._ride_load_request_id
    client.complete_request(failed_id, ok=False, detail='not available')
    sim._service_ride_load_result()
    assert sim._selected == 1
    assert sim._loaded_index is None
    sim.select_experience(1)
    assert sim._ride_load_request_id != failed_id
    assert sim._ride_load_pending


def test_second_selection_replaces_first_queued_selection():
    sim, client = connected_sim()
    sim.select_experience(1)
    sim.select_experience(2)
    client.complete_request(sim._ride_load_request_id, ok=True)
    sim._service_ride_load_result()
    assert sim._loading_index == 2
    assert len([c for c in client.commands if c[0] == 'load_park']) == 2


def test_same_confirmed_park_does_not_reload():
    sim, client = connected_sim()
    client.complete_request(sim._ride_load_request_id, ok=True)
    sim._service_ride_load_result()
    before = len([c for c in client.commands if c[0] == 'load_park'])
    sim.select_experience(0)
    assert len([c for c in client.commands if c[0] == 'load_park']) == before
