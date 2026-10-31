# test_nl2_multistation_v171.py ver 1
"""NL2 arrival at a different station without losing departure-station dispatch."""
import struct
import time

from core.types import ExperienceState
from sims.nolimits2.nl2_protocol import NL2Protocol
from sims.nolimits2.sim import Sim
from tests.test_nolimits2_adapter import FakeNL2Client, telemetry

MANUAL = 0x0002
CAN_DISPATCH = 0x0004
OTHER = MANUAL | 0x0800 | CAN_DISPATCH
VIEW = MANUAL | 0x0800 | 0x1000 | CAN_DISPATCH
EMPTY = MANUAL


class TwoStationClient(FakeNL2Client):
    """Test double with real-protocol-style per-station snapshots."""
    def __init__(self):
        super().__init__()
        self.is_connected = True
        self.last_response_at = time.monotonic()
        self.cache = {}

    def station_snapshot(self, identity):
        return self.cache.get(identity)

    def put(self, station, flags, age=0.0):
        when = time.monotonic() - age
        self.cache[(0, station)] = (flags, when)
        self.station_status = flags
        self.station_status_received_at = when
        self.last_response_at = time.monotonic()


def running():
    client = TwoStationClient()
    client.put(0, VIEW)
    sim = Sim(client=client)
    sim.state = ExperienceState.READY
    client.latest_telemetry = telemetry(speed=0, frame=1)
    sim.run()
    client.put(0, VIEW)
    sim._service_dispatch_preparation()
    assert sim._ride_station == (0, 0)
    assert sim.state is ExperienceState.RUNNING
    return sim, client


def tick(sim, client, speed, frame):
    tm = telemetry(speed=speed, frame=frame)
    client.latest_telemetry = tm
    sim._update_state(tm)


def stop_at_other_station(sim, client):
    # Another train occupies the original departure station; our view train
    # has departed, stopped, and is in a separate nearby station.
    client.put(0, OTHER)
    tick(sim, client, 12, 2)
    assert sim._view_train_departed
    client.current_station_index = 1
    tick(sim, client, 0, 3)  # sends nearest-station query (fake replies at once)
    assert sim._arrival_probe_since > 0
    client.put(1, VIEW)
    tick(sim, client, 0, 4)


def test_adjacent_arrival_completes_and_becomes_next_departure():
    sim, c = running()
    stop_at_other_station(sim, c)
    assert sim.state == ExperienceState.READY
    assert sim._ready_station == (0, 1)
    assert sim._ride_station is None
    assert sim.is_ready_to_start()
    returned = [x for x in sim.consume_state_events() if x['event'] == 'view_train_returned']
    assert len(returned) == 1
    assert returned[0]['station'] == (0, 1)
    assert returned[0]['departure_station'] == (0, 0)
    # Next dispatch is from the newly arrived station, not the original one.
    sim.run()
    c.put(1, VIEW)
    sim._service_dispatch_preparation()
    assert c.commands[-2][0] == 'dispatch' or ('dispatch', 0, 1) in c.commands
    assert sim._ride_station == (0, 1)
    assert [v for v in c.commands if v[0] == 'dispatch'][-1] == ('dispatch', 0, 1)


def test_another_train_in_departure_station_does_not_finish_ride():
    sim, c = running()
    c.put(0, OTHER)
    tick(sim, c, 12, 2)
    tick(sim, c, 0, 3)
    assert sim.state == ExperienceState.RUNNING
    assert sim._ready_station is None


def test_stale_or_non_view_arrival_status_does_not_finish_ride():
    sim, c = running()
    c.put(0, OTHER)
    tick(sim, c, 12, 2)
    c.current_station_index = 1
    c.put(1, VIEW, age=3.0)  # old, before nearest-station query
    tick(sim, c, 0, 3)
    tick(sim, c, 0, 4)
    assert sim.state == ExperienceState.RUNNING
    c.put(1, OTHER)
    tick(sim, c, 0, 5)
    assert sim.state == ExperienceState.RUNNING
    c.current_coaster_index = 5
    c.current_station_received_at = time.monotonic()
    c.put(1, VIEW)
    tick(sim, c, 0, 6)
    assert sim.state == ExperienceState.RUNNING


def test_moving_through_station_cannot_terminate():
    sim, c = running()
    c.put(0, EMPTY)
    tick(sim, c, 20, 2)
    c.current_station_index = 1
    c.put(1, VIEW)
    tick(sim, c, 14, 3)
    assert sim.state == ExperienceState.RUNNING


def test_auxiliary_dispatch_remains_pinned_while_nearest_changes():
    sim, c = running()
    c.current_station_index = 1
    c.put(0, OTHER)
    tick(sim, c, 11, 2)
    dispatches = [v for v in c.commands if v[0] == 'dispatch']
    assert dispatches == [('dispatch', 0, 0), ('dispatch', 0, 0)]
    assert sim.state == ExperienceState.RUNNING


def test_single_station_return_still_completes():
    sim, c = running()
    c.put(0, EMPTY)
    tick(sim, c, 10, 2)
    c.put(0, VIEW)
    tick(sim, c, 0, 3)
    assert sim.state == ExperienceState.READY
    assert sim._ready_station == (0, 0)


def test_real_protocol_preserves_independent_station_replies():
    class Socket:
        def __init__(self): self.packets = []
        def sendall(self, p): self.packets.append(p)
    c = NL2Protocol('127.0.0.1')
    c.is_connected = True
    c._sock = Socket()
    c.request_station_state(0, 0)
    id0 = struct.unpack('>I', c._sock.packets[-1][3:7])[0]
    c.request_current_coaster_and_station()
    nearest_id = struct.unpack('>I', c._sock.packets[-1][3:7])[0]
    c.request_station_state(0, 1)
    id1 = struct.unpack('>I', c._sock.packets[-1][3:7])[0]
    c._process_message(c.N_MSG_STATION_STATE, id1, struct.pack('>I', VIEW))
    c._process_message(c.N_MSG_INT_VALUE_PAIR, nearest_id, struct.pack('>ii', 0, 1))
    c._process_message(c.N_MSG_STATION_STATE, id0, struct.pack('>I', OTHER))
    assert c.station_snapshot((0, 0))[0] == OTHER
    assert c.station_snapshot((0, 1))[0] == VIEW
    assert c.current_station_index == 1
    c._process_message(c.N_MSG_INT_VALUE_PAIR, nearest_id + 99, struct.pack('>ii', 0, 7))
    assert c.current_station_index == 1
    c.invalidate_current_station()
    assert c.station_snapshot((0, 1)) is None


def test_grand_national_station_alternates_on_second_cycle():
    sim, c = running()
    stop_at_other_station(sim, c)
    sim.run()
    c.put(1, VIEW)
    sim._service_dispatch_preparation()
    assert sim._ride_station == (0, 1)
    c.put(1, OTHER)
    tick(sim, c, 15, 5)
    assert sim._view_train_departed
    c.current_station_index = 0
    tick(sim, c, 0, 6)
    c.put(0, VIEW)
    tick(sim, c, 0, 7)
    assert sim.state == ExperienceState.READY
    assert sim._ready_station == (0, 0)
    sim.run()
    c.put(0, VIEW)
    sim._service_dispatch_preparation()
    assert sim._ride_station == (0, 0)


def test_arrival_detected_even_if_departure_station_not_manual():
    sim, c = running()
    c.put(0, OTHER)
    tick(sim, c, 16, 2)
    # The departure station may switch modes during operation. That cannot
    # block the ridden train's verified arrival at a different station.
    c.put(0, 0x0800 | CAN_DISPATCH)
    c.current_station_index = 1
    tick(sim, c, 0, 3)
    c.put(1, VIEW)
    tick(sim, c, 0, 4)
    assert sim.state == ExperienceState.READY
