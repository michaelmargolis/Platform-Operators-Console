# test_nl2_estop_recovery_v232.py ver 2
"""Regression tests for NL2 E-STOP recovery park-load reuse.

Fake-only: these tests never connect to NoLimits 2, SHS or platform hardware.
"""
from __future__ import annotations

from collections import deque
import time
from unittest.mock import Mock, call

from core.types import ExperienceState
from sims.nolimits2.sim import LOAD_PARK_BUSY_RETRIES, Sim


class _Client:
    def __init__(self, result=None):
        self.is_connected = True
        self.version = (2, 6, 8, 1)
        self.last_error = ""
        self._result = result
        self.load_park = Mock(return_value=100)

    def response_is_fresh(self, _max_age, _now=None):
        return True

    def request_result(self, _request_id):
        return self._result


def _bare_sim(*, result=None):
    sim = Sim.__new__(Sim)
    sim.address = "127.0.0.1"
    sim.client = _Client(result)
    sim._rides = (("Hybris", "C:/parks/Hybris.nl2park"),
                  ("Hybris [B]", "C:/parks/Hybris.nl2park"),
                  ("Blackbird", "C:/parks/Blackbird.nl2park"))
    sim._experiences = ("Hybris", "Hybris [B]", "Blackbird")
    sim._selected = 0
    sim._loaded_index = 0
    sim._loading_index = None
    sim._ride_load_pending = False
    sim._ride_load_request_id = None
    sim._ride_load_retry_at = 0.0
    sim._ride_load_busy_retries = 0
    sim._queued_ride_index = None
    sim._park_load_failed = False
    sim._dispatch_pending = False
    sim._ride_train = None
    sim._ride_station = None
    sim._estop_recovery_pending = False
    sim._manual_mode_requested = False
    sim._station_modes = Mock()
    sim._state_events = deque(maxlen=32)
    sim.state = ExperienceState.READY
    return sim


def test_fresh_confirmed_loaded_park_reuses_park_for_estop_recovery():
    sim = _bare_sim()
    sim._manual_mode_requested = True
    sim.client.latest_telemetry = object()
    sim.client.station_status = 0x0002
    sim.client.station_status_received_at = 123.0
    sim.client.invalidate_current_station = Mock()
    sim._start_park_reset = Mock()
    sim._begin_ride_load = Mock()

    sim.recover_from_estop()

    assert sim._estop_recovery_pending is True
    assert sim._manual_mode_requested is False
    assert sim.client.latest_telemetry is None
    assert sim.client.station_status is None
    assert sim.client.station_status_received_at == 0.0
    sim._station_modes.reset.assert_called_once_with()
    sim.client.invalidate_current_station.assert_called_once_with()
    sim._start_park_reset.assert_called_once_with()
    sim._begin_ride_load.assert_not_called()
    assert sim.consume_state_events()[-1]["event"] == "estop_recovery_reuse_loaded_park"


def test_estop_recovery_still_reloads_after_ride_train_was_established():
    sim = _bare_sim()
    sim._ride_train = (0, 0)
    sim._start_park_reset = Mock()
    sim._begin_ride_load = Mock()

    sim.recover_from_estop()

    sim._begin_ride_load.assert_called_once_with(0, startup=False)
    sim._start_park_reset.assert_not_called()
    assert sim._estop_recovery_pending is True


def test_estop_busy_same_park_preserves_confirmed_load_and_starts_reset():
    sim = _bare_sim(result=("error", "Application is processing another operation"))
    sim._ride_load_pending = True
    sim._ride_load_request_id = 232
    sim._loading_index = 0
    sim._estop_recovery_pending = True
    sim.client.last_error = "Application is processing another operation"
    sim._apply_loaded_variant = Mock()
    sim._start_park_reset = Mock()

    assert sim._service_ride_load_result() is True

    assert sim._loaded_index == 0
    assert sim._park_load_failed is False
    assert sim._estop_recovery_pending is True
    assert sim.client.last_error == ""
    sim._apply_loaded_variant.assert_called_once_with(0)
    sim._start_park_reset.assert_called_once_with()
    assert sim.consume_state_events()[-1]["event"] == "estop_recovery_busy_fallback"


def test_estop_busy_same_physical_park_keeps_latest_variant_choice():
    sim = _bare_sim(result=("error", "Application is processing another operation"))
    sim._selected = 1  # another variant, same physical Hybris park
    sim._ride_load_pending = True
    sim._ride_load_request_id = 233
    sim._loading_index = 0
    sim._estop_recovery_pending = True
    sim._apply_loaded_variant = Mock()
    sim._start_park_reset = Mock()

    assert sim._service_ride_load_result() is True

    assert sim._loaded_index == 1
    assert sim._park_load_failed is False
    sim._apply_loaded_variant.assert_called_once_with(1)
    sim._start_park_reset.assert_called_once_with()


def _exhaust_busy_retries(sim, park_path):
    for attempt in range(LOAD_PARK_BUSY_RETRIES):
        assert sim._service_ride_load_result() is True
        assert sim._ride_load_pending
        assert sim._park_load_failed is False
        assert sim._ride_load_busy_retries == attempt + 1
        assert sim._ride_load_retry_at > time.monotonic()
        sim._ride_load_retry_at = time.monotonic() - 0.001
        assert sim._service_ride_load_result() is True

    assert sim._service_ride_load_result() is True
    assert sim.client.load_park.call_args_list == [
        call(park_path, start_paused=True)
    ] * LOAD_PARK_BUSY_RETRIES


def test_normal_operator_busy_load_fails_after_bounded_retries():
    sim = _bare_sim(result=("error", "Application is processing another operation"))
    sim._ride_load_pending = True
    sim._ride_load_request_id = 234
    sim._loading_index = 0
    sim._estop_recovery_pending = False
    sim._start_park_reset = Mock()

    _exhaust_busy_retries(sim, sim._rides[sim._loading_index][1])

    assert sim._loaded_index is None
    assert sim._park_load_failed is True
    assert sim.state is ExperienceState.READY
    sim._start_park_reset.assert_not_called()


def test_estop_busy_different_park_fails_after_bounded_retries():
    sim = _bare_sim(result=("error", "Application is processing another operation"))
    sim._selected = 2
    sim._ride_load_pending = True
    sim._ride_load_request_id = 235
    sim._loading_index = 2
    sim._estop_recovery_pending = True
    sim._start_park_reset = Mock()

    _exhaust_busy_retries(sim, sim._rides[sim._loading_index][1])

    assert sim._loaded_index is None
    assert sim._park_load_failed is True
    assert sim._estop_recovery_pending is False
    sim._start_park_reset.assert_not_called()
