# test_nl2_service_timestamp_v213.py ver 1
"""Regression for service-cycle timestamps used by NL2 station discovery.

Fake-only: never connects to NoLimits 2, SHS, or platform hardware.
"""
from __future__ import annotations

import time

from core.types import ExperienceState
from sims.nolimits2.sim import Sim
from tests.test_nolimits2_adapter import FakeNL2Client, telemetry


def test_service_refreshes_now_after_processing_nl2_replies(monkeypatch):
    client = FakeNL2Client()
    sim = Sim(client=client)

    client.is_connected = True
    client.last_response_at = time.monotonic()
    sim._connected_sync_started = True
    sim._data_connected = True
    sim._selected = 0
    sim._loaded_index = 0
    sim._ride_load_pending = False
    sim._park_load_failed = False
    sim.state = ExperienceState.READY

    observed = {}

    def process_reply():
        received_at = time.monotonic()
        client.latest_telemetry = telemetry(
            speed=0.0, state=3, frame=213, received_at=received_at
        )
        client.last_response_at = received_at
        observed["received_at"] = received_at

    def capture_station_service(now=None):
        observed["station_now"] = now

    monkeypatch.setattr(client, "service", process_reply)
    monkeypatch.setattr(sim, "_service_station_modes", capture_station_service)
    monkeypatch.setattr(sim, "_service_dispatch_preparation", lambda: None)
    monkeypatch.setattr(sim, "_service_flyer_dispatch_confirmation", lambda: None)
    monkeypatch.setattr(sim, "_service_ride_load_result", lambda: None)

    sim.service()

    assert observed["station_now"] >= observed["received_at"]
