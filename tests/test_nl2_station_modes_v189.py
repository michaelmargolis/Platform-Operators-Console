# test_nl2_station_modes_v189.py ver 3
"""Fake-only v189 multi-station/manual-mode/blocked-train regressions.

Never connects to NoLimits 2, external SHS, or physical platform hardware.
"""
from __future__ import annotations

import time

from core.types import ExperienceState
from sims.nolimits2.nl2_protocol import NL2Protocol
from sims.nolimits2.sim import Sim
from sims.nolimits2.station_modes import StationModeManager, MAX_STATIONS, PLAY_MODE_RETRY_S
from tests.test_nolimits2_adapter import telemetry
from tests.test_nl2_flyer_lock_v185 import FlyerClient, CAN_LOCK_FLYER
from tests.test_nl2_multistation_v171 import VIEW, OTHER, MANUAL, CAN_DISPATCH


class DiscoveryClient(FlyerClient):
    supports_station_discovery = True

    def __init__(self, stations=(0, 1), *, no_manual=()):
        super().__init__()
        self.stations = set(stations)
        self.no_manual = set(no_manual)
        self.flags = {station: ((VIEW | CAN_LOCK_FLYER) if station == 0 else OTHER)
                      for station in stations}
        if 0 in self.flags:
            self.flags[0] &= ~CAN_DISPATCH
        self.flags = {s: (bits & ~MANUAL) for s, bits in self.flags.items()}
        self.current_coaster_index = 0
        self.current_station_index = 1 if 1 in self.stations else 0
        for station, bits in self.flags.items():
            self.put(station, bits)

    def request_station_state(self, coaster=0, station=0):
        self.commands.append(('get_station', int(coaster), int(station)))
        request_id = self._next_request_id
        self._next_request_id += 1
        if int(coaster) != 0 or int(station) not in self.stations:
            self.complete_request(request_id, ok=False, detail='Invalid index')
        else:
            self.put(int(station), self.flags[int(station)])
        return request_id

    def set_manual_mode(self, enabled=True, coaster=0, station=0):
        super().set_manual_mode(enabled, coaster, station)
        if int(station) in self.stations and station not in self.no_manual:
            self.flags[station] = ((self.flags[station] | MANUAL) if enabled
                                   else (self.flags[station] & ~MANUAL))
            self.put(station, self.flags[station])

    def set_flyer_car(self, locked, coaster=0, station=0):
        rid = super().set_flyer_car(locked, coaster, station)
        self.complete_request(rid, ok=True)
        if locked and all(self.flags[s] & MANUAL for s in self.stations):
            self.flags[station] = (self.flags[station] | CAN_DISPATCH) & ~CAN_LOCK_FLYER
            self.put(station, self.flags[station])
        return rid

    def invalidate_current_station(self):
        super().invalidate_current_station()
        self.cache.clear()

    def reset_park(self, start_paused=False):
        super().reset_park(start_paused)
        # Model NL2 invalidating prior station control mode at reset.
        for station in self.stations:
            self.flags[station] &= ~MANUAL
        self.cache.clear()


def setup(park='flyingbeast', *, stations=(0, 1), no_manual=()):
    c = DiscoveryClient(stations, no_manual=no_manual)
    sim = Sim(client=c)
    sim._selected = sim._park_keys.index(park)
    sim.state = ExperienceState.READY
    sim._ready_station = (0, 0)
    c.latest_telemetry = telemetry(speed=0, frame=10)
    return sim, c


def prepare_modes(sim, client, cycles=24):
    for _ in range(cycles):
        sim._service_station_modes()
        if sim._all_station_modes_ready():
            break


def test_protocol_provides_request_id_for_correlated_station_probe():
    class Socket:
        def sendall(self, _packet):
            pass
    p = NL2Protocol('127.0.0.1')
    p._sock = Socket()
    p.is_connected = True
    rid = p.request_station_state(2, 3)
    assert isinstance(rid, int)
    assert p._station_requests[rid] == (2, 3)


def test_discovery_is_generic_and_manual_mode_verified_for_all_stations():
    sim, c = setup(park='grandnational')
    assert not sim.is_ready_to_start()  # second station is still automatic
    prepare_modes(sim, c)
    assert sim._station_modes.stations == ((0, 0), (0, 1))
    assert sim._all_station_modes_ready()
    assert sim.is_ready_to_start()
    assert ('manual', True, 0, 0) in c.commands
    assert ('manual', True, 0, 1) in c.commands
    assert not any(x[0] == 'dispatch' for x in c.commands)
    assert sim._reset_station_candidates() == (0, 1)


def test_single_station_does_not_issue_manual_to_unknown_station():
    sim, c = setup(park='hybris', stations=(0,))
    prepare_modes(sim, c)
    assert sim._station_modes.stations == ((0, 0),)
    assert sim._all_station_modes_ready()
    assert [x for x in c.commands if x[0] == 'manual'] == [('manual', True, 0, 0)]
    assert sim._reset_station_candidates() == ()


def test_manual_modes_required_before_flying_beast_lock_and_dispatch():
    sim, c = setup()
    assert not sim.is_ready_to_start()
    sim.run()
    assert sim._dispatch_pending
    sim._service_dispatch_preparation()
    assert not any(x[0] in ('flyer', 'dispatch') for x in c.commands)
    prepare_modes(sim, c)
    assert sim._all_station_modes_ready()
    # A fresh station report after mode preparation enables actual lock.
    c.request_station_state(0, 0)
    sim._service_dispatch_preparation()
    assert len([x for x in c.commands if x[0] == 'flyer']) == 1
    c.request_station_state(0, 0)
    sim._service_dispatch_preparation()
    assert [('dispatch', 0, 0)] == [x for x in c.commands if x[0] == 'dispatch']
    assert sim.state == ExperienceState.RUNNING


def test_failure_to_confirm_other_station_never_authorizes_dispatch():
    sim, c = setup(no_manual=(1,))
    prepare_modes(sim, c)
    assert not sim._all_station_modes_ready()
    assert not sim.is_ready_to_start()
    sim.run()
    c.request_station_state(0, 0)
    sim._service_dispatch_preparation()
    assert not any(x[0] in ('flyer', 'dispatch') for x in c.commands)


def test_park_reset_invalidates_and_reestablishes_all_manual_modes():
    sim, c = setup()
    prepare_modes(sim, c)
    assert sim.is_ready_to_start()
    sim._start_park_reset()
    assert ('reset', False) in c.commands
    assert not sim._all_station_modes_ready()
    assert sim.state == ExperienceState.RESETTING
    c.latest_telemetry = telemetry(speed=0, frame=25)
    prepare_modes(sim, c)
    assert sim._all_station_modes_ready()
    assert sim._reset_station_candidates() == (0, 1)
    assert c.commands.count(('manual', True, 0, 1)) >= 2
    # Nearest station points at the OTHER train, not the viewed train.
    c.current_coaster_index, c.current_station_index = 0, 1
    for frame in (26, 27):
        c.request_station_state(0, 0)
        c.request_station_state(0, 1)
        c.latest_telemetry = telemetry(speed=0, frame=frame)
        sim._update_state(c.latest_telemetry)
    assert sim.state == ExperienceState.READY
    assert sim._ready_station == (0, 0)


def test_stale_other_station_snapshot_blocks_dispatch():
    sim, c = setup()
    prepare_modes(sim, c)
    sim.run()
    # Station 1 loses freshness while primary station is still current.
    c.cache[(0, 1)] = (c.flags[1], time.monotonic() - 3)
    c.request_station_state(0, 0)
    sim._service_dispatch_preparation()
    assert not any(x[0] == 'flyer' for x in c.commands)
    assert not any(x[0] == 'dispatch' for x in c.commands)


def test_no_reply_is_not_misinterpreted_as_end_of_station_list():
    class NeverReplies:
        def request_station_state(self, *args):
            return 123
        def station_snapshot(self, identity):
            return None
        def request_result(self, request_id):
            return None
    c = NeverReplies()
    manager = StationModeManager()
    manager.step(c, 0, now=100)
    manager.step(c, 0, now=101.6)
    manager.step(c, 0, now=103.3)
    assert manager.phase == 'failed'
    assert 'No confirmed response' in manager.error
    assert not manager.ready(c, now=103.4)


def test_scan_limit_fails_closed_not_assuming_all_stations_found():
    class EndlessStations:
        def __init__(self):
            self.last = (None, None)
        def request_station_state(self, coaster, station):
            self.last = ((coaster, station), time.monotonic())
            return station
        def station_snapshot(self, identity):
            return (0x0002, self.last[1]) if identity == self.last[0] else None
        def request_result(self, rid):
            return None
    c = EndlessStations()
    manager = StationModeManager()
    for _ in range(MAX_STATIONS * 3):
        manager.step(c, 0)
    assert manager.phase == 'failed'
    assert not manager.ready(c)


def test_auxiliary_dispatch_only_for_stopped_view_train_blocked_at_own_station():
    from tests.test_nl2_multitrain_v170 import running_setup, observe, OTHER, EMPTY
    sim, c = running_setup()
    observe(sim, c, OTHER, 12, 2)  # viewed train leaves; another arrives
    assert [x for x in c.commands if x[0] == 'dispatch'] == [('dispatch', 0, 0)]
    observe(sim, c, OTHER, 0, 3)  # stopped; not yet evidence of sustained block
    assert len([x for x in c.commands if x[0] == 'dispatch']) == 1
    sim._arrival_probe_since = time.monotonic() - 4
    c.current_coaster_index, c.current_station_index = 0, 0
    c.current_station_received_at = time.monotonic()  # recent nearest station
    observe(sim, c, OTHER, 0, 4)
    assert [x for x in c.commands if x[0] == 'dispatch'] == [
        ('dispatch', 0, 0), ('dispatch', 0, 0)]
    assert any(e['event'] == 'aux_blocker_identified' for e in sim.consume_state_events())
    observe(sim, c, OTHER, 0, 5)  # still occupied; no repeated command
    assert len([x for x in c.commands if x[0] == 'dispatch']) == 2
    observe(sim, c, EMPTY, 0, 6)  # must clear before another may be considered
    assert len([x for x in c.commands if x[0] == 'dispatch']) == 2


def test_nearest_other_station_never_releases_unwatched_train_from_home():
    from tests.test_nl2_multitrain_v170 import running_setup, observe, OTHER
    sim, c = running_setup()
    observe(sim, c, OTHER, 12, 2)
    observe(sim, c, OTHER, 0, 3)
    sim._arrival_probe_since = time.monotonic() - 4
    c.current_coaster_index, c.current_station_index = 0, 1
    c.current_station_received_at = time.monotonic()
    observe(sim, c, OTHER, 0, 4)
    assert [x for x in c.commands if x[0] == 'dispatch'] == [('dispatch', 0, 0)]


def test_stale_nearest_station_or_revoked_auth_blocks_auxiliary():
    from tests.test_nl2_multitrain_v170 import running_setup, observe, OTHER
    sim, c = running_setup()
    observe(sim, c, OTHER, 12, 2)
    observe(sim, c, OTHER, 0, 3)
    sim._arrival_probe_since = time.monotonic() - 4
    c.current_station_received_at = time.monotonic() - 5
    observe(sim, c, OTHER, 0, 4)
    assert len([x for x in c.commands if x[0] == 'dispatch']) == 1
    c.current_station_received_at = time.monotonic()
    sim.cancel_pending_dispatch()
    observe(sim, c, OTHER, 0, 5)
    assert len([x for x in c.commands if x[0] == 'dispatch']) == 1


def test_nl2_unrelated_error_must_not_end_station_discovery():
    class TransientError:
        def request_station_state(self, coaster, station):
            self.last = station
            self.sent = time.monotonic()
            return station + 100
        def station_snapshot(self, identity):
            if identity[1] == 0:
                return (MANUAL, self.sent)
            return None
        def request_result(self, rid):
            return ('error', 'API permission denied') if rid == 101 else None
    c = TransientError()
    manager = StationModeManager()
    for _ in range(4):
        manager.step(c, 0)
    assert manager.phase == 'failed'
    assert 'Cannot determine whether station' in manager.error


def test_real_nl2_invalid_index_marks_station_discovery_complete():
    """NL2 replies exactly 'Invalid index' for first nonexistent station.

    v189 incorrectly treated this normal end-of-list response as a fatal
    manual setup failure, leaving POC indefinitely in RESETTING.
    """
    for stations in ((0,), (0, 1)):
        sim, client = setup(stations=stations)
        for _ in range(24):
            sim._service_station_modes()
            if sim._all_station_modes_ready():
                break
        assert sim._station_modes.phase == 'maintain', sim._station_modes.error
        assert sim._station_modes.stations == tuple((0, s) for s in stations)
        assert sim._all_station_modes_ready()
        assert not any(cmd[0] == 'manual' and cmd[3] not in stations
                       for cmd in client.commands)
        # Discovery runs to completion; the v189 bug stopped at index N.
        assert any(e['event'] == 'station_discovery_complete'
                   for e in sim.consume_state_events())


def test_invalid_index_for_first_station_does_not_complete_discovery():
    """Never treat an invalid coaster/first station as a valid empty scan."""
    client = DiscoveryClient(stations=())
    manager = StationModeManager()
    for _ in range(5):
        manager.step(client, 0)
    assert manager.phase == 'failed'
    assert 'No station 0' in manager.error
    assert not manager.ready(client)


def test_loaded_park_exits_resetting_for_actual_nl2_invalid_index():
    """Regression for Flying Beast + single-station park load (Nov 5 trace)."""
    for stations in ((0,), (0, 1)):
        sim, client = setup(stations=stations)
        sim.state = ExperienceState.RESETTING
        sim._initial_sync = True
        prepare_modes(sim, client)
        assert sim._all_station_modes_ready(), sim._station_modes.error
        client.current_station_index = 0
        client.request_station_state(0, 0)
        client.latest_telemetry = telemetry(speed=0, frame=40)
        sim._update_state(client.latest_telemetry)
        assert sim.state == ExperienceState.READY


def test_production_protocol_preserves_actual_invalid_index_response():
    """The real protocol preserves NL2's exact error for the matched probe."""
    class Socket:
        def sendall(self, packet):
            pass
    protocol = NL2Protocol('127.0.0.1')
    protocol._sock = Socket()
    protocol.is_connected = True
    request_id = protocol.request_station_state(0, 2)
    protocol._process_message(protocol.N_MSG_ERROR, request_id, b'Invalid index')
    assert protocol.request_result(request_id) == ('error', 'Invalid index')


def test_play_mode_error_defers_without_advancing_station_index():
    """A transient reply from NL2 during RESET_PARK must not poison a scan.

    Covers an error at station 0 AND at the final (nonexistent) index 2, as
    happens in the two-station Grand National park. No sleeps or hardware.
    """
    class TransitionClient:
        def __init__(self, transitional_station):
            self.now = 100.0
            self.next_request = 50
            self.sent = []
            self.results = {}
            self.cache = {}
            self.errors = {transitional_station: 1}

        def request_station_state(self, coaster, station):
            self.next_request += 1
            rid = self.next_request
            self.sent.append((coaster, station))
            if self.errors.get(station, 0):
                self.errors[station] -= 1
                self.results[rid] = ('error', 'Not in play mode')
            elif station < 2:
                self.cache[(coaster, station)] = (0, self.now)
            else:
                self.results[rid] = ('error', 'Invalid index')
            return rid

        def station_snapshot(self, identity):
            return self.cache.get(identity)

        def request_result(self, rid):
            return self.results.get(rid)

        def set_manual_mode(self, *args):
            raise AssertionError('No control command is allowed during discovery')

    for transitional_station in (0, 2):
        client = TransitionClient(transitional_station)
        manager = StationModeManager()
        events = []
        def record(name, **details):
            events.append((name, details))
        deferred = False
        for _ in range(50):
            client.now += 0.25
            manager.step(client, 0, now=client.now, callback=record)
            if manager._waiting_for_play_mode and not deferred:
                deferred = True
                assert manager.phase == 'discover'
                assert manager._probe_index == transitional_station
                assert not manager.ready(client, now=client.now)
                sent_before = len(client.sent)
                manager.step(client, 0, now=client.now + 0.01, callback=record)
                assert len(client.sent) == sent_before  # bounded retry pacing
            if manager.phase == 'maintain':
                break
        assert deferred
        assert manager.phase == 'maintain', manager.error
        assert manager.stations == ((0, 0), (0, 1))
        assert ('station_discovery_waiting_for_play' in [e[0] for e in events])
        assert ('station_discovery_resumed' in [e[0] for e in events]
                if transitional_station == 0 else True)
        assert client.sent.count((0, transitional_station)) == 2


def test_grand_national_post_reset_play_error_recovers_to_alternate_station():
    """Reproduce 5 Nov trace: reset, Not in play mode, station 1 is VIEW.

    POC must keep RESETTING until both station modes and the viewed train at
    station 1 are independently confirmed, then select that station as READY.
    """
    class GrandNationalClient(DiscoveryClient):
        def __init__(self):
            super().__init__(stations=(0, 1))
            self.reject_next_station_zero = 0

        def reset_park(self, start_paused=False):
            super().reset_park(start_paused)
            self.reject_next_station_zero = 1

        def request_station_state(self, coaster=0, station=0):
            if self.reject_next_station_zero and int(station) == 0:
                self.reject_next_station_zero -= 1
                self.commands.append(('get_station', int(coaster), int(station)))
                rid = self._next_request_id
                self._next_request_id += 1
                self.complete_request(rid, ok=False, detail='Not in play mode')
                return rid
            return super().request_station_state(coaster, station)

    client = GrandNationalClient()
    sim = Sim(client=client)
    sim._selected = sim._park_keys.index('grandnational')
    sim._ready_station = (0, 0)
    sim.state = ExperienceState.READY
    client.latest_telemetry = telemetry(speed=0, frame=10)
    prepare_modes(sim, client)
    assert sim._all_station_modes_ready()

    sim._start_park_reset()
    assert sim.state is ExperienceState.RESETTING
    assert ('reset', False) in client.commands
    # Grand National has two NL2 station identities. The viewed train
    # returns to index 1 after crossing to the other track.
    client.flags[0] = (OTHER & ~MANUAL)
    client.flags[1] = (VIEW & ~MANUAL)
    client.current_coaster_index, client.current_station_index = 0, 1
    client.latest_telemetry = telemetry(speed=0, frame=30)

    sim._service_station_modes()
    sim._service_station_modes()
    assert sim._station_modes.phase == 'discover'
    assert sim._station_modes._waiting_for_play_mode
    assert not sim._all_station_modes_ready()
    assert sim.state is ExperienceState.RESETTING
    # Advance the manager's retry deadline deterministically; the client
    # timestamps and manual-mode checks still use real monotonic time.
    sim._station_modes._probe_retry_at = 0.0
    prepare_modes(sim, client)
    assert sim._station_modes.stations == ((0, 0), (0, 1))
    assert sim._all_station_modes_ready(), sim._station_modes.error
    for frame in range(31, 36):
        client.request_station_state(0, 0)
        client.request_station_state(0, 1)
        client.latest_telemetry = telemetry(speed=0, frame=frame)
        sim._update_state(client.latest_telemetry)
        if sim.state is ExperienceState.READY:
            break
    assert sim.state is ExperienceState.READY
    assert sim._ready_station == (0, 1)
    assert sim._reset_requested_at == 0.0
    events = sim.consume_state_events()
    assert any(e['event'] == 'station_discovery_waiting_for_play' for e in events)
    assert any(e['event'] == 'reset_view_station_confirmed' and
               e['station'] == (0, 1) for e in events)
    assert not any(x[0] == 'dispatch' for x in client.commands)
