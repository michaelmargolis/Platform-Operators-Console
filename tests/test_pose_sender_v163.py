# test_pose_sender_v163.py ver 1
"""Pure tests for Pose Sender, without Qt, sockets or actual actuator output."""
from pathlib import Path

import pytest

from output.fstlib import easyip
from output.fstlib.pose_sender_logic import (
    PoseMotion, PressureMonitor, slew_pressures, MemoryFesto,
)


ROOT = Path(__file__).resolve().parents[1]


def make_reply(request_data, values, error=0):
    request = easyip.Packet(request_data)
    answer = easyip.Factory.response(request, error)
    answer.senddata_type = easyip.Operands.FLAG_WORD
    answer.senddata_size = answer.encode_payload(list(values), easyip.Packet.DIRECTION_SEND)
    return answer.pack()


def test_read_is_passive_and_requires_matching_fresh_response():
    monitor = PressureMonitor("192.168.0.10")
    payload = monitor.make_request(100.0)
    req = easyip.Packet(payload)
    assert req.reqdata_type == easyip.Operands.FLAG_WORD
    assert req.reqdata_offset_server == 10
    assert req.reqdata_size == 6
    assert monitor.make_request(100.1) is None
    reply = make_reply(payload, [12, 50, 80, 0, 150, 20])
    assert not monitor.decode_response(reply, "192.168.0.9", 100.1)
    assert not monitor.fresh(100.1)
    assert monitor.decode_response(reply, "192.168.0.10", 100.2)
    assert monitor.actual == (12, 50, 80, 0, 150, 20)
    assert monitor.fresh(102.0)
    assert not monitor.fresh(102.21)


def test_bad_response_never_confirms_pressure():
    monitor = PressureMonitor()
    request = monitor.make_request(2.0)
    assert not monitor.decode_response(make_reply(request, [1] * 6, error=1), monitor.target_ip, 2.1)
    assert not monitor.decode_response(make_reply(request, [6001] * 6), monitor.target_ip, 2.1)
    assert not monitor.decode_response(b"garbled", monitor.target_ip, 2.1)
    assert monitor.actual is None
    assert monitor.make_request(3.01) is not None  # timed-out read can be retried


def test_pressure_slew_and_piston_flag():
    assert slew_pressures([0] * 6, [6000] * 6) == (300,) * 6
    assert slew_pressures([500] * 6, [0] * 6 + [1]) == (200,) * 6 + (1,)
    assert slew_pressures([100] * 6, [200] * 6) == (200,) * 6
    with pytest.raises(ValueError):
        slew_pressures([0] * 6, [100, 200])


def test_capture_has_no_network_side_effects(monkeypatch):
    from output import festo_itf

    def forbidden(*args, **kwargs):
        raise AssertionError("Unexpected live Festo connection")
    monkeypatch.setattr(festo_itf, "Festo", forbidden)
    model = PoseMotion("platform_v2", ROOT)
    assert model.capture.generation == 0
    assert model.mode == "parked"
    assert model.backend.workspace_limit_mode == "geometry"
    assert model.backend.k.MUSCLE_EXTENSION_RESERVE == model.backend.cfg.MUSCLE_EXTENSION_RESERVE
    real, applied = model.preview()
    assert real == applied == (0.0,) * 6
    assert model.capture.generation == 0


def test_mdx_unpark_move_park_reuses_backend_and_floor_limiter():
    motion = PoseMotion("platform_v2", ROOT)
    motion.begin_unpark()
    with pytest.raises(RuntimeError):
        motion.begin_unpark()
    for i in range(240):
        pressure = motion.tick()
        if motion.mode == "active":
            break
    assert i < 240
    assert len(pressure) == 6
    assert any(v > 0 for v in pressure)
    motion.set_normalized([0.3, 0.0, -1.0, 0.5, -0.5, 0.0])
    requested, applied = motion.preview()
    assert requested[0] == pytest.approx(0.3 * motion.backend.cfg.LIMITS_1DOF_TRANFORM[0])
    assert applied != requested  # geometry limits an intentionally extreme pose
    assert motion.backend._floor_clearance_for_real_transform(applied) >= 15.4
    motion.tick()
    assert motion.backend.muscle_lengths != motion.backend.cfg.DEACTIVATED_MUSCLE_LENGTHS
    motion.begin_park()
    for i in range(240):
        pressure = motion.tick()
        if motion.mode == "parked":
            break
    assert i < 240
    assert pressure == (0,) * 6
    assert all(abs(a - b) < 1e-6 for a, b in zip(
        motion.backend.muscle_lengths, motion.backend.cfg.DEACTIVATED_MUSCLE_LENGTHS
    ))


def test_piston_platform_uses_poc_prop_sequences():
    motion = PoseMotion("chair_v3", ROOT)
    assert motion.has_piston
    motion.begin_unpark()
    seen = []
    for _ in range(240):
        p = motion.tick()
        seen.append(p[-1])
        if motion.mode == "active":
            break
    assert 1 in seen
    motion.begin_park()
    for _ in range(240):
        p = motion.tick()
        if motion.mode == "parked":
            break
    assert motion.mode == "parked"
    assert p[-1] == 0
    assert p[:6] == (0,) * 6
