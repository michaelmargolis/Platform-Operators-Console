# test_twin_extension_v150.py ver 1
"""Twin v1 optional extension, independent of Qt availability."""
import json

from core.digital_twin import DigitalTwinSender


class DummySocket:
    def __init__(self):
        self.packets = []
    def setblocking(self, enabled):
        pass
    def sendto(self, raw, dest):
        self.packets.append(json.loads(raw))
    def close(self):
        pass


def snapshot(**kwargs):
    fields = dict(platform="platform_v2", platform_state="ACTIVATED", parking_state="UNPARKED",
                  simulator_state="RUNNING", telemetry_valid=True, transform_applied=True,
                  dtop_load_kg_per_muscle=10., transform=[0.]*6,
                  applied_transform_real=[10., 0., -30., 0., .05, 0.],
                  muscle_length_mm=[1000.]*6, contraction_pct=[0.]*6,
                  command_pressure_mbar=[0.]*6, piston_disengaged=None)
    fields.update(kwargs)
    return fields


def test_packet_real_pose_and_park_state():
    sock = DummySocket()
    sender = DigitalTwinSender("127.0.0.1", socket_factory=lambda *_: sock)
    assert sender.send_snapshot(**snapshot())
    packet = sock.packets[0]
    assert packet["version"] == 1
    assert packet["parking_state"] == "UNPARKED"
    assert packet["applied_transform_real"] == [10., 0., -30., 0., .05, 0.]
    assert packet["transform"] == [0.] * 6
    assert sender.send_snapshot(**snapshot(parking_state="PARKED"))
    assert sender.send_snapshot(**snapshot(applied_transform_real=None))
    assert sock.packets[-1]["applied_transform_real"] is None


def test_nonfinite_real_pose_rejected():
    sock = DummySocket()
    sender = DigitalTwinSender("127.0.0.1", socket_factory=lambda *_: sock)
    assert not sender.send_snapshot(**snapshot(applied_transform_real=[0.] * 5 + [float("nan")]))
    assert not sock.packets
