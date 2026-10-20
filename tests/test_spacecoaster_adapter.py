#test_spacecoaster_adapter.py  ver 17
from __future__ import annotations

import math

from core.types import ExperienceState
from sims.spacecoaster import config
from sims.spacecoaster.sim import NativeState, Sim


class FakeControl:
    def __init__(self):
        self.commands = []

    def _record(self, command):
        self.commands.append(command)
        return True

    def pause(self): return self._record("pause")
    def resume(self): return self._record("resume")
    def ready(self): return self._record("ready")
    def run(self): return self._record("run")
    def reset_vr(self): return self._record("reset_vr")
    def lobby(self): return self._record("lobby")


class FakeSleep:
    def __init__(self):
        self.calls = []

    def __call__(self, seconds):
        self.calls.append(seconds)


def make_sim():
    control = FakeControl()
    sleeper = FakeSleep()
    sim = Sim(control=control, sleep_func=sleeper)
    return sim, control, sleeper


def test_state_sequence_uses_semantic_udp_controls():
    sim, control, sleeper = make_sim()
    sim._mark_sim_seen()

    sim._process_native_state(NativeState.INITIALIZING)
    sim._process_native_state(NativeState.WAITING)
    assert control.commands == []
    assert sim._ready_due_at is not None
    assert sleeper.calls == []
    assert sim.state is ExperienceState.RESETTING

    sim._ready_due_at = 0.0
    sim.service()
    assert control.commands == ["ready"]

    sim._process_native_state(NativeState.READY)
    assert sim.state is ExperienceState.READY

    sim.run()
    assert control.commands[-1] == "run"
    assert sim.state is ExperienceState.READY

    sim._process_native_state(NativeState.RUNNING)
    assert sim.state is ExperienceState.RUNNING

    sim._process_native_state(NativeState.COMPLETED)
    assert control.commands[-1] == "lobby"
    assert sim.state is ExperienceState.RESETTING

    sim._process_native_state(NativeState.WAITING)
    assert control.commands[-1] == "lobby"
    sim._ready_due_at = 0.0
    sim.service()
    assert control.commands[-1] == "ready"


def test_any_waiting_event_requests_ready():
    sim, control, _sleeper = make_sim()
    sim.native_state = NativeState.RUNNING
    sim.state = ExperienceState.RUNNING

    sim._process_native_state(NativeState.WAITING)

    assert control.commands == []
    assert sim._ready_due_at is not None
    sim._ready_due_at = 0.0
    sim.service()
    assert control.commands == ["ready"]
    assert sim.native_state is NativeState.WAITING
    assert sim.state is ExperienceState.RESETTING


def test_pause_and_resume_use_explicit_commands():
    sim, control, _sleeper = make_sim()
    sim.state = ExperienceState.RUNNING
    sim.pause()
    assert control.commands == ["pause"]
    assert sim.state is ExperienceState.PAUSED

    sim.pause()
    assert control.commands == ["pause", "resume"]
    assert sim.state is ExperienceState.RUNNING


def test_xyzrpy_paces_recorded_motion_and_provides_live_speed():
    sim, _control, _sleeper = make_sim()
    sim._mark_sim_seen()
    sim.native_state = NativeState.RUNNING
    sim.state = ExperienceState.RUNNING
    sim.frame_number = 0

    expected = tuple(sim._telemetry[0])
    sim._handle_datagram("xyzrpy,3,4,12,999,999,999")
    sample = sim.read()

    assert sample.transform == expected
    assert math.isclose(sample.speed, 13.0)
    assert sample.telemetry_valid is True
    assert sim.frame_number == 1


def test_spacecoaster_live_speed_uses_all_three_velocity_components():
    sim, _control, _sleeper = make_sim()

    sim._update_fan_speed("xyzrpy,-12,3,-4,100,200,300,")

    assert math.isclose(sim._fan_speed, 13.0)


def test_spacecoaster_live_speed_prefers_unity_spline_speed_field():
    sim, _control, _sleeper = make_sim()

    sim._update_fan_speed("xyzrpy,0,0,0,0,0,0,17.5,")

    assert math.isclose(sim._fan_speed, 17.5)


def test_posrpy_paces_motion_and_derives_scaled_3d_speed(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("sims.spacecoaster.sim.time.monotonic", lambda: now[0])
    sim, _control, _sleeper = make_sim()
    sim._mark_sim_seen()
    sim.native_state = NativeState.RUNNING
    sim.state = ExperienceState.RUNNING
    sim.frame_number = 0

    sim._handle_datagram("posrpy,0,0,0,0,0,0,")
    first = sim.read()
    now[0] += 0.3
    sim._handle_datagram("posrpy,9,18,18,0,0,0,")
    second = sim.read()

    assert first.speed == 0.0
    assert math.isclose(second.speed, 10.0)
    assert sim.frame_number == 2


def test_repeated_posrpy_does_not_keep_last_speed_fresh(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("sims.spacecoaster.sim.time.monotonic", lambda: now[0])
    sim, _control, _sleeper = make_sim()
    sim._update_position_speed("posrpy,0,0,0,0,0,0,")
    now[0] += 0.1
    sim._update_position_speed("posrpy,0,0,9,0,0,0,")
    assert math.isclose(sim._fresh_fan_speed(), 10.0)

    now[0] += 0.4
    sim._update_position_speed("posrpy,0,0,9,0,0,0,")
    now[0] += 0.11

    assert sim._fresh_fan_speed() == 0.0


def test_posrpy_long_gap_resets_baseline_without_speed_spike(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("sims.spacecoaster.sim.time.monotonic", lambda: now[0])
    sim, _control, _sleeper = make_sim()
    sim._update_position_speed("posrpy,0,0,0,0,0,0,")
    now[0] += config.FAN_SPEED_TIMEOUT_S + 0.1
    sim._update_position_speed("posrpy,900,0,0,0,0,0,")
    assert sim._fresh_fan_speed() == 0.0
    assert not sim._position_speed_raw
    assert not sim._position_speed_average

    now[0] += 0.1
    sim._update_position_speed("posrpy,909,0,0,0,0,0,")

    assert math.isclose(sim._fresh_fan_speed(), 10.0)


def test_posrpy_filter_rejects_isolated_spike(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("sims.spacecoaster.sim.time.monotonic", lambda: now[0])
    sim, _control, _sleeper = make_sim()
    position = 0.0
    sim._update_position_speed("posrpy,0,0,0,0,0,0,")

    outputs = []
    for raw_speed in (10.0, 10.0, 10.0, 400.0, 10.0, 10.0, 10.0):
        now[0] += 0.1
        position += raw_speed * 0.1 / sim.fan_config["position_speed_scale"]
        sim._update_position_speed(f"posrpy,{position},0,0,0,0,0,")
        outputs.append(sim._fan_speed)

    assert all(math.isclose(value, 10.0) for value in outputs)


def test_position_speed_filter_uses_one_second_window():
    sim, _control, _sleeper = make_sim()

    assert math.isclose(sim._smooth_position_speed(10.0, 100.0), 10.0)
    assert math.isclose(sim._smooth_position_speed(20.0, 100.5), 12.5)
    assert math.isclose(sim._smooth_position_speed(20.0, 101.6), 20.0)


def test_position_speed_filter_is_cleared_on_pause(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("sims.spacecoaster.sim.time.monotonic", lambda: now[0])
    sim, _control, _sleeper = make_sim()
    sim.state = ExperienceState.RUNNING
    sim._update_position_speed("posrpy,0,0,0,0,0,0,")
    now[0] += 0.1
    sim._update_position_speed("posrpy,9,0,0,0,0,0,")

    sim.pause()

    assert sim._fan_speed == 0.0
    assert not sim._position_speed_raw
    assert not sim._position_speed_average


def test_bad_explicit_spline_speed_does_not_fall_back_to_rigidbody_velocity(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("sims.spacecoaster.sim.time.monotonic", lambda: now[0])
    sim, _control, _sleeper = make_sim()
    sim._update_fan_speed("xyzrpy,3,4,12,0,0,0,10")
    now[0] += 0.4

    sim._update_fan_speed("xyzrpy,3,4,12,0,0,0,bad")
    now[0] += 0.11

    assert sim._fresh_fan_speed() == 0.0


def test_spacecoaster_fan_speed_times_out_without_changing_motion_state(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("sims.spacecoaster.sim.time.monotonic", lambda: now[0])
    sim, _control, _sleeper = make_sim()
    sim._mark_sim_seen()
    sim.native_state = NativeState.RUNNING
    sim.state = ExperienceState.RUNNING
    sim.frame_number = 0
    sim._handle_datagram("xyzrpy,3,4,12,0,0,0")

    assert math.isclose(sim.read().speed, 13.0)
    now[0] += config.MOTION_DATA_TIMEOUT_S + 0.01
    sample = sim.read()

    assert sample.speed is None
    assert sample.state is ExperienceState.RUNNING
    assert sample.telemetry_valid is False


def test_bad_velocity_packet_does_not_refresh_fan_speed(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("sims.spacecoaster.sim.time.monotonic", lambda: now[0])
    sim, _control, _sleeper = make_sim()
    sim._update_fan_speed("xyzrpy,3,4,12,0,0,0")
    now[0] += 0.4

    sim._update_fan_speed("xyzrpy,bad,4,12,0,0,0")
    now[0] += 0.11

    assert sim._fresh_fan_speed() == 0.0


def test_state_message_connects_sim_but_does_not_claim_motion_data():
    sim, _control, _sleeper = make_sim()

    status = sim.get_status()
    assert status.pc_connected is True
    assert status.sim_connected is False
    assert status.data_connected is False

    sim._handle_datagram("state,2")
    sim.service()
    status = sim.get_status()

    assert status.pc_connected is True
    assert status.sim_connected is True
    assert status.data_connected is False
    assert sim.state is ExperienceState.READY


def test_motion_data_status_times_out_while_sim_connection_remains_latched(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("sims.spacecoaster.sim.time.monotonic", lambda: now[0])
    sim, _control, _sleeper = make_sim()
    sim._handle_datagram("xyzrpy,0,0,0,0,0,0")

    assert sim.get_status().sim_connected is True
    assert sim.get_status().data_connected is True

    now[0] += config.MOTION_DATA_TIMEOUT_S + 0.01

    assert sim.get_status().sim_connected is True
    assert sim.get_status().data_connected is False


def test_restart_with_telemetry_but_no_state_returns_to_lobby_once(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("sims.spacecoaster.sim.time.monotonic", lambda: now[0])
    sim, control, _sleeper = make_sim()

    sim._handle_datagram("xyzrpy,0,0,0,0,0,0")
    assert control.commands == []
    now[0] += 1.01
    sim.service()

    assert control.commands == ["lobby"]
    assert sim.state is ExperienceState.RESETTING

    sim._handle_datagram("xyzrpy,0,0,0,0,0,0")
    now[0] += 2.0
    sim.service()
    assert control.commands == ["lobby"]


def test_fresh_connect_does_not_schedule_lobby_fallback(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("sims.spacecoaster.sim.time.monotonic", lambda: now[0])

    class FakeThread:
        def __init__(self, **_kwargs):
            self.started = False

        def is_alive(self):
            return False

        def start(self):
            self.started = True

    monkeypatch.setattr("sims.spacecoaster.sim.threading.Thread", FakeThread)
    sim, control, _sleeper = make_sim()

    sim.connect()
    assert sim._startup_sync_due_at is None
    now[0] = 101.01
    sim.service()

    assert control.commands == []
    assert sim.state is ExperienceState.DISCONNECTED


def test_restart_state_message_cancels_lobby_fallback(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("sims.spacecoaster.sim.time.monotonic", lambda: now[0])
    sim, control, _sleeper = make_sim()

    sim._handle_datagram("xyzrpy,0,0,0,0,0,0")
    sim._handle_datagram("state,2")
    sim.service()
    now[0] += 2.0
    sim.service()

    assert control.commands == []
    assert sim.native_state is NativeState.READY
    assert sim.state is ExperienceState.READY


def test_reset_vr_uses_semantic_udp_control():
    sim, control, _sleeper = make_sim()

    sim.reset_vr()

    assert control.commands == ["reset_vr"]


class FakeHostService:
    def __init__(self):
        self.connected = True
        self.commands = []

    def command(self, target, command):
        self.commands.append((target, command))
        return True


def test_spacecoaster_control_can_be_attached_to_shared_shs_client():
    from sims.spacecoaster.control import SpaceCoasterControl

    host = FakeHostService()
    sim = Sim(control=SpaceCoasterControl())
    sim.set_host_service(host)
    sim.run()
    sim.reset_vr()

    assert host.commands == [
        ("spacecoaster", "run"),
        ("spacecoaster", "reset_vr"),
    ]


def test_unknown_spacecoaster_datagram_is_ignored_not_marked_connected():
    sim, _control, _sleeper = make_sim()
    assert sim._handle_datagram("unrelated," + "x" * 200) is False
    assert sim.get_status().sim_connected is False


def test_estop_recovery_sends_lobby_and_waits_for_ready():
    control = FakeControl()
    sim = Sim(control=control, sleep_func=lambda _seconds: None)
    sim._sim_connected = True
    sim.native_state = NativeState.RUNNING
    sim.state = ExperienceState.RUNNING

    assert not sim.get_status().ready_to_start
    sim.recover_from_estop()
    assert control.commands[-1] == "lobby"
    assert sim.state is ExperienceState.RESETTING

    sim._process_native_state(NativeState.WAITING)
    assert control.commands[-1] == "lobby"
    sim._ready_due_at = 0.0
    sim.service()
    assert control.commands[-1] == "ready"
    sim._process_native_state(NativeState.READY)
    assert sim.get_status().ready_to_start


def test_state_99_is_diagnostic_not_bad_or_native_state():
    sim, control, _sleeper = make_sim()
    sim.native_state = NativeState.RUNNING
    sim.state = ExperienceState.RESETTING

    sim._process_command_msg("state,99")

    assert sim.native_state is NativeState.RUNNING
    assert sim.state is ExperienceState.RESETTING
    assert control.commands == []


def test_delayed_ready_is_cancelled_if_waiting_state_changes():
    sim, control, _sleeper = make_sim()
    sim._process_native_state(NativeState.WAITING)
    sim._process_native_state(NativeState.READY)
    sim._ready_due_at = 0.0

    sim.service()

    assert control.commands == []


def test_paused_spacecoaster_does_not_advance_recorded_telemetry():
    sim, _control, _sleeper = make_sim()
    sim._mark_sim_seen()
    sim.native_state = NativeState.RUNNING
    sim.state = ExperienceState.PAUSED
    sim.frame_number = 0
    sim._handle_datagram("xyzrpy,1,2,3,4,5,6")

    sim.service()

    assert sim.frame_number == 0


def test_estop_recovery_rewinds_csv_and_discards_stale_pacing():
    sim, control, _sleeper = make_sim()
    sim._mark_sim_seen()
    sim.native_state = NativeState.RUNNING
    sim.state = ExperienceState.PAUSED
    sim.frame_number = min(10, len(sim._telemetry) - 1)
    sim._handle_datagram("xyzrpy,1,2,3,4,5,6")

    sim.recover_from_estop()

    assert sim.frame_number == sim.start_frame
    assert sim._xyzrpy_q.empty()
    assert control.commands[-1] == "lobby"


def test_spacecoaster_declares_washout_unsupported():
    sim, _control, _sleeper = make_sim()
    assert sim.supports_washout is False
