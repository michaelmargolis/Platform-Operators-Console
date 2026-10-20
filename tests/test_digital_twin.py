# test_digital_twin.py ver 3
import json
import socket
from types import SimpleNamespace

from core.digital_twin import DigitalTwinSender, MAX_DATAGRAM_BYTES
from core.poc_core import PocCore
from core.types import ExperienceState, PlatformState, SimSample


class FakeSocket:
    def __init__(self, send_error=None, blocking_error=None):
        self.blocking = None
        self.closed = False
        self.sent = []
        self.send_error = send_error
        self.blocking_error = blocking_error

    def setblocking(self, blocking):
        if self.blocking_error is not None:
            raise self.blocking_error
        self.blocking = blocking

    def sendto(self, data, destination):
        if self.send_error is not None:
            raise self.send_error
        self.sent.append((data, destination))

    def close(self):
        self.closed = True


def snapshot_values():
    return {
        "platform": "chair_v2",
        "platform_state": "ACTIVATED",
        "parking_state": "UNPARKED",
        "applied_transform_real": [12, 3, -5, 0, 0, .1],
        "simulator_state": "RUNNING",
        "telemetry_valid": True,
        "transform_applied": True,
        "dtop_load_kg_per_muscle": 85.0 / 6.0,
        "transform": [0.1, -0.2, 0.3, 0.01, -0.02, 0.03],
        "muscle_length_mm": [700, 701, 702, 703, 704, 705],
        "contraction_pct": [50, 49.5, 49, 48.5, 48, 47.5],
        "command_pressure_mbar": [1000, 1100, 1200, 1300, 1400, 1500],
        "piston_disengaged": None,
    }


def make_sender(fake_socket, address="192.168.0.20", port=10020):
    return DigitalTwinSender(
        address,
        port,
        socket_factory=lambda *_args: fake_socket,
        time_func=lambda: 1234.567,
    )


def test_dash_address_disables_sender_without_opening_socket():
    opened = []
    sender = DigitalTwinSender("-", socket_factory=lambda *_args: opened.append(True))

    assert not sender.enabled
    assert not sender.send_snapshot(**snapshot_values())
    assert opened == []


def test_snapshot_is_complete_versioned_compact_json():
    sock = FakeSocket()
    sender = make_sender(sock)

    assert sender.send_snapshot(**snapshot_values())
    assert sender.send_snapshot(**snapshot_values())

    first_data, destination = sock.sent[0]
    second_data, _destination = sock.sent[1]
    first = json.loads(first_data)
    second = json.loads(second_data)
    assert destination == ("192.168.0.20", 10020)
    assert first["protocol"] == "POC_TWIN"
    assert first["version"] == 1
    assert first["sequence"] == 0
    assert second["sequence"] == 1
    assert first["timestamp_ms"] == 1234567
    assert first["platform"] == "chair_v2"
    assert first["dtop_load_kg_per_muscle"] == 85.0 / 6.0
    assert first["transform"] == snapshot_values()["transform"]
    assert first["parking_state"] == "UNKNOWN"
    assert first["applied_transform_real"] is None
    assert first["muscle_length_mm"] == [700.0, 701.0, 702.0, 703.0, 704.0, 705.0]
    assert first["command_pressure_mbar"] == [1000.0, 1100.0, 1200.0, 1300.0, 1400.0, 1500.0]
    assert len(first_data) < MAX_DATAGRAM_BYTES
    assert b" " not in first_data
    assert sock.blocking is False


def test_invalid_destination_and_send_failure_never_raise():
    invalid = DigitalTwinSender("not-an-ip", "bad-port")
    assert not invalid.enabled
    assert "invalid digital twin destination" in invalid.last_error

    sender = make_sender(FakeSocket(OSError("network unavailable")))
    assert not sender.send_snapshot(**snapshot_values())
    assert sender.last_error == "network unavailable"

    failed_socket = FakeSocket(blocking_error=OSError("socket setup failed"))
    setup_failure = make_sender(failed_socket)
    assert not setup_failure.enabled
    assert failed_socket.closed
    assert "socket setup failed" in setup_failure.last_error


def test_malformed_or_nonfinite_snapshot_is_rejected_without_sending():
    sock = FakeSocket()
    sender = make_sender(sock)
    values = snapshot_values()
    values["transform"] = [0, 0, 0]
    assert not sender.send_snapshot(**values)
    assert "exactly 6" in sender.last_error

    values = snapshot_values()
    values["dtop_load_kg_per_muscle"] = float("nan")
    assert not sender.send_snapshot(**values)
    assert "non-finite" in sender.last_error
    assert sock.sent == []


def test_core_maps_current_output_and_state_to_sender_fields():
    calls = []
    fake_sender = SimpleNamespace(
        enabled=True,
        send_snapshot=lambda **values: calls.append(values),
    )
    core = SimpleNamespace(
        digital_twin=fake_sender,
        platform_key="chair_v2",
        platform_name="chair_v2",
        platform=SimpleNamespace(state=PlatformState.ACTIVATED,
                                 parking_state=SimpleNamespace(name="UNPARKED"),
                                 backend=SimpleNamespace(last_applied_real_transform=[12, 3, -5, 0, 0, .1])),
        experience_state=ExperienceState.RUNNING,
        _motion_transform=(0.1, 0.2, 0.3, 0.01, 0.02, 0.03),
        get_output_snapshot=lambda: {
            "dtop_load_kg_per_muscle": 14.25,
            "muscle_lengths": (700, 701, 702, 703, 704, 705),
            "contractions": (50, 49, 48, 47, 46, 45),
            "pressures": (1000, 1100, 1200, 1300, 1400, 1500),
            "piston_disengaged": None,
        },
    )

    PocCore._send_digital_twin_snapshot(core, SimSample(telemetry_valid=True), True)

    assert calls == [{
        "platform": "chair_v2",
        "platform_state": "ACTIVATED",
        "parking_state": "UNPARKED",
        "applied_transform_real": [12, 3, -5, 0, 0, .1],
        "simulator_state": "RUNNING",
        "telemetry_valid": True,
        "transform_applied": True,
        "dtop_load_kg_per_muscle": 14.25,
        "transform": core._motion_transform,
        "muscle_length_mm": (700, 701, 702, 703, 704, 705),
        "contraction_pct": (50, 49, 48, 47, 46, 45),
        "command_pressure_mbar": (1000, 1100, 1200, 1300, 1400, 1500),
        "piston_disengaged": None,
    }]


def test_core_uses_configured_platform_protocol_name():
    calls = []
    core = SimpleNamespace(
        digital_twin=SimpleNamespace(
            enabled=True,
            send_snapshot=lambda **values: calls.append(values),
        ),
        platform_key="platform_v2",
        platform_name="wheelchair_platform",
        platform=SimpleNamespace(state=PlatformState.DEACTIVATED),
        experience_state=ExperienceState.READY,
        _motion_transform=(0.0,) * 6,
        get_output_snapshot=lambda: {
            "dtop_load_kg_per_muscle": 0.0,
            "muscle_lengths": (1000.0,) * 6,
            "contractions": (0.0,) * 6,
            "pressures": (0.0,) * 6,
            "piston_disengaged": None,
        },
    )

    PocCore._send_digital_twin_snapshot(core, SimSample(), False)

    assert calls[0]["platform"] == "wheelchair_platform"


def test_real_udp_loopback_receives_one_decodable_snapshot():
    receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    receiver.bind(("127.0.0.1", 0))
    receiver.settimeout(1.0)
    sender = DigitalTwinSender("127.0.0.1", receiver.getsockname()[1])
    try:
        assert sender.send_snapshot(**snapshot_values())
        data, source = receiver.recvfrom(MAX_DATAGRAM_BYTES)
        packet = json.loads(data)
        assert source[0] == "127.0.0.1"
        assert packet["protocol"] == "POC_TWIN"
        assert packet["platform"] == "chair_v2"
    finally:
        sender.close()
        receiver.close()
