# test_twin_output_v6.py ver 1
"""Twin UDP playback contract and best-effort rate limiting, without Qt."""
from __future__ import annotations
from dataclasses import dataclass
import json
import socket
from types import SimpleNamespace

from tools.sim_player.settings import VisualizerSettings
from tools.sim_player.twin_output import ReplayTwinOutput


class FakeSender:
    def __init__(self, address, port):
        self.address, self.port = address, port
        self.enabled = True
        self.last_error = ""
        self.sent = []
        self.closed = False

    def send_snapshot(self, **packet):
        self.sent.append(packet)
        return True

    def close(self):
        self.closed = True
        self.enabled = False


def make_recording():
    frames = (
        SimpleNamespace(
            processed_axes=(.1, -.1, .4, .2, -.2, 0),
            input_axes=(.2, -.2, .5, .4, -.4, 0),
            muscle_lengths_mm=(750., 800., 850., 900., 950., 1000.),
            analysis={"applied_real": (10., 0., 30., 0., 3., 0.)},
        ),
        SimpleNamespace(
            processed_axes=(.1, .2, -.3, .0, -.1, .4),
            muscle_lengths_mm=(780., 820., 860., 900., 940., 980.),
            analysis={"applied_real": (11., 0., 30., 0., 3., 0.)},
        ),
    )
    return SimpleNamespace(frames=frames, platform_key="platform_v2",
                           min_length_mm=750., max_length_mm=1000.)


def test_disabled_by_default_never_creates_socket():
    created = []
    output = ReplayTwinOutput(VisualizerSettings(),
                              sender_factory=lambda **kw: created.append(kw))
    assert not output.enabled
    assert not output.send_frame(make_recording(), 0)
    assert not created


def test_packet_reuses_production_fields_with_zero_pressures():
    clock = [0.0]
    output = ReplayTwinOutput(
        VisualizerSettings(True, "127.0.0.1", 10020),
        clock=lambda: clock[0], sender_factory=FakeSender,
    )
    rec = make_recording()
    assert output.send_frame(rec, 0, state="PAUSED", force=True)
    packet = output._sender.sent[-1]
    assert packet["platform"] == "platform_v2"
    assert packet["simulator_state"] == "PAUSED"
    assert packet["transform"] == rec.frames[0].processed_axes
    assert packet["applied_transform_real"] == rec.frames[0].analysis["applied_real"]
    assert packet["muscle_length_mm"] == rec.frames[0].muscle_lengths_mm
    assert packet["contraction_pct"] == (100., 80., 60., 40., 20., 0.)
    assert packet["command_pressure_mbar"] == (0.,) * 6
    assert packet["dtop_load_kg_per_muscle"] == 0.0
    assert not output.send_frame(rec, 0)
    clock[0] = .025
    assert not output.send_frame(rec, 1)
    clock[0] = .052
    assert output.send_frame(rec, 1, state="RUNNING")
    assert len(output._sender.sent) == 2
    assert output.send_frame(rec, 1, force=True)  # explicit final seek, even if unchanged
    output.configure(VisualizerSettings())
    assert not output.enabled


def test_actual_production_json_protocol_on_local_udp_socket():
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.bind(("127.0.0.1", 0))
    receiver.settimeout(1.0)
    try:
        port = receiver.getsockname()[1]
        output = ReplayTwinOutput(VisualizerSettings(True, "127.0.0.1", port))
        try:
            assert output.send_frame(make_recording(), 0, force=True)
            data = json.loads(receiver.recvfrom(4096)[0])
            assert data["protocol"] == "POC_TWIN"
            assert data["version"] == 1
            assert data["platform"] == "platform_v2"
            assert data["command_pressure_mbar"] == [0.0] * 6
            assert data["contraction_pct"] == [100., 80., 60., 40., 20., 0.]
            assert data["applied_transform_real"] == [10., 0., 30., 0., 3., 0.]
        finally:
            output.close()
    finally:
        receiver.close()
