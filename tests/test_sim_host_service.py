#test_sim_host_service.py  ver 20
from __future__ import annotations

import socket

from sim_host_service.client import SimHostClient
from sim_host_service.protocol import (
    PROTOCOL_VERSION,
    TYPE_ACK,
    TYPE_FAN_POWER,
    TYPE_PING,
    TYPE_PONG,
    TYPE_SELECT_SIM,
    TYPE_STARTUP,
    decode_message,
    encode_message,
)
from sim_host_service.server import (
    SERVER_FILE_VERSION,
    SimConfig,
    SimHostService,
    WindowsSimManager,
    dashboard_status,
    load_config,
    local_ipv4_addresses,
    project_manifest_version,
)


class FakeFan:
    def __init__(self):
        self.powers = []

    def set_power(self, power):
        self.powers.append(power)
        return True

    def close(self):
        pass

    def service(self):
        pass

    def snapshot(self):
        return {
            "connected": True,
            "port": "COM7",
            "last_command": f"power,{self.powers[-1]}" if self.powers else "",
            "last_response": "ready",
            "last_error": "",
        }


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class FakeSocket:
    def __init__(self, *args, **kwargs):
        self.sent = []
        self.rx = []
        self.closed = False

    def setblocking(self, value):
        pass

    def bind(self, address):
        self.bound = address

    def sendto(self, data, address):
        self.sent.append((decode_message(data), address))

    def recvfrom(self, size):
        if not self.rx:
            raise BlockingIOError
        return self.rx.pop(0), ("127.0.0.1", 10040)

    def close(self):
        self.closed = True


class FakeManager:
    def __init__(self):
        self.ensure_calls = []
        self.select_calls = []
        self.command_calls = []
        self.current = ""
        self.running = set()

    def is_running(self, key):
        return key in self.running

    def active_sim(self):
        return self.current

    def ensure_sim(self, key):
        self.ensure_calls.append(key)
        self.current = key
        return True, "start initiated"

    def select_sim(self, key):
        self.select_calls.append(key)
        self.current = key
        return True, "switch initiated"

    def command(self, target, command):
        self.command_calls.append((target, command))
        return True, "sent"


def test_client_waits_for_pong_before_startup_request():
    clock = FakeClock()
    fake_socket = FakeSocket()
    client = SimHostClient(
        "127.0.0.1",
        10040,
        time_func=clock,
        socket_factory=lambda *args: fake_socket,
    )
    client.set_startup_sim("spacecoaster")
    client.start()

    client.service()
    assert [message["type"] for message, _ in fake_socket.sent] == [TYPE_PING]

    fake_socket.rx.append(encode_message({
        "type": TYPE_PONG,
        "id": 1,
        "protocol": PROTOCOL_VERSION,
        "server_version": "0.1",
        "active_sim": "",
    }))
    client.service()

    sent_types = [message["type"] for message, _ in fake_socket.sent]
    assert sent_types[-1] == TYPE_STARTUP
    assert fake_socket.sent[-1][0]["sim"] == "spacecoaster"
    assert client.connected is True


def test_client_sim_selection_becomes_reconnect_startup_target():
    clock = FakeClock()
    fake_socket = FakeSocket()
    client = SimHostClient(
        "127.0.0.1",
        10040,
        heartbeat_timeout_s=3.0,
        time_func=clock,
        socket_factory=lambda *args: fake_socket,
    )
    client.set_startup_sim("nolimits2")
    client.start()
    fake_socket.rx.append(encode_message({
        "type": TYPE_PONG,
        "id": 1,
        "protocol": PROTOCOL_VERSION,
        "server_version": "0.2",
        "active_sim": "nolimits2",
    }))
    client.service()

    assert client.select_sim("spacecoaster") is True
    sent_count = len(fake_socket.sent)
    client.service()
    assert len(fake_socket.sent) == sent_count

    clock.now = 4.0
    client.service()
    fake_socket.rx.append(encode_message({
        "type": TYPE_PONG,
        "id": 2,
        "protocol": PROTOCOL_VERSION,
        "server_version": "0.2",
        "active_sim": "spacecoaster",
    }))
    client.service()

    startup_messages = [
        message for message, _address in fake_socket.sent
        if message["type"] == TYPE_STARTUP
    ]
    assert startup_messages[-1]["sim"] == "spacecoaster"


def test_shs_display_versions_match_versioned_sources():
    # Manifest revision advances independently of Sim Host Service code.
    from pathlib import Path
    manifest = (Path(__file__).resolve().parents[1] / "file_versions.txt").read_text()
    expected = next(
        line.split("|", 1)[1].strip()
        for line in manifest.splitlines()
        if line.startswith("file_versions.txt|")
    )
    assert project_manifest_version() == expected
    assert SERVER_FILE_VERSION == 18


def test_dashboard_status_uses_public_facing_console_wording():
    text, style = dashboard_status({"listen_state": "Listening", "poc_connected": False})
    assert text == "WAITING FOR CONSOLE CONNECTION"
    assert style == "Shs.Waiting.TLabel"

    text, style = dashboard_status({"listen_state": "Listening", "poc_connected": True})
    assert text == "CONSOLE CONNECTED — READY"
    assert style == "Shs.Ready.TLabel"

    text, style = dashboard_status({
        "listen_state": "Failed", "listen_error": "address in use", "poc_connected": False,
    })
    assert text == "SERVICE ERROR"
    assert style == "Shs.Error.TLabel"


def test_dashboard_snapshot_includes_simulator_and_relay_details():
    manager = FakeManager()
    manager.current = "spacecoaster"
    manager.running.add("spacecoaster")
    server = SimHostService(manager)
    server._poc_address = "192.168.0.3"
    server._spacecoaster_unity_addr = ("127.0.0.1", 54321)
    server.last_sim_action = "Select spacecoaster: activated"

    status = server.status_snapshot()

    assert status["poc_address"] == "192.168.0.3"
    assert status["active_sim"] == "spacecoaster"
    assert status["sim_running"] is True
    assert status["last_sim_action"] == "Select spacecoaster: activated"
    assert status["spacecoaster_unity_endpoint"] == "127.0.0.1:54321"


def test_recoverable_udp_reset_does_not_prevent_next_datagram():
    class ResetThenDataSocket:
        def __init__(self):
            self.calls = 0

        def recvfrom(self, _size):
            self.calls += 1
            if self.calls == 1:
                raise ConnectionResetError(10054, "remote UDP port closed")
            return b"state,2\n", ("127.0.0.1", 54321)

    sock = ResetThenDataSocket()
    assert SimHostService._recv_udp_datagram(sock, 65535, "test") is None
    assert SimHostService._recv_udp_datagram(sock, 65535, "test") == (
        b"state,2\n",
        ("127.0.0.1", 54321),
    )


def test_server_startup_is_idempotent_for_repeated_transaction_id():
    manager = FakeManager()
    server = SimHostService(manager)
    request = encode_message({
        "type": TYPE_STARTUP,
        "id": 44,
        "protocol": PROTOCOL_VERSION,
        "sim": "nolimits2",
    })

    response1 = decode_message(server.handle_datagram(request, ("10.0.0.2", 50000)))
    response2 = decode_message(server.handle_datagram(request, ("10.0.0.2", 50000)))

    assert response1["type"] == TYPE_ACK
    assert response1["ok"] is True
    assert response2 == response1
    assert manager.ensure_calls == ["nolimits2"]


def test_config_uses_batch_filename_without_working_directory(tmp_path):
    config = tmp_path / "sim_host_service.ini"
    config.write_text(
        "[server]\n"
        "[sim:nolimits2]\n"
        "window_class = NL3D_MAIN_TEST\n"
        "window_title = NoLimits 2\n"
        "start_batch = nolimits2.bat\n"
        "close_timeout_s = 7\n"
        "[spacecoaster_relay]\n"
        "enabled = true\n"
        "listen_port = 10009\n"
        "target_port = 10009\n"
        "[fan]\n"
        "enabled = true\n"
        "port = COM7\n"
        "baudrate = 115200\n"
        "command_timeout_s = 1.0\n",
        encoding="utf-8",
    )
    server_cfg, sims = load_config(config)
    assert sims["nolimits2"].start_batch == "nolimits2.bat"
    assert sims["nolimits2"].window_class == "NL3D_MAIN_TEST"
    assert sims["nolimits2"].close_timeout_s == 7.0
    assert server_cfg["spacecoaster_relay_enabled"] is True
    assert server_cfg["spacecoaster_relay_listen_port"] == 10009
    assert server_cfg["spacecoaster_relay_target_port"] == 10009
    assert server_cfg["fan_port"] == "COM7"
    assert server_cfg["fan_baudrate"] == 115200
    assert not hasattr(sims["nolimits2"], "working_directory")


def test_manager_resolves_start_batch_under_shs_sims_dir(tmp_path, monkeypatch):
    sims_dir = tmp_path / "sims"
    sims_dir.mkdir()
    batch = sims_dir / "spacecoaster.bat"
    batch.write_text("@echo off\n", encoding="utf-8")
    manager = WindowsSimManager(
        {"spacecoaster": SimConfig("spacecoaster", start_batch="spacecoaster.bat")},
        sims_dir=sims_dir,
    )
    monkeypatch.setattr(manager, "is_running", lambda _key: False)
    calls = []
    monkeypatch.setattr(
        "sim_host_service.server.subprocess.Popen",
        lambda args: calls.append(args),
    )
    ok, detail = manager.ensure_sim("spacecoaster")
    assert ok is True
    assert detail == "start initiated: spacecoaster.bat"
    assert calls == [["cmd.exe", "/c", str(batch)]]


def test_manager_select_closes_other_running_sim_and_activates_running_target(monkeypatch):
    manager = WindowsSimManager({
        "nolimits2": SimConfig("nolimits2", window_class="NL3D_MAIN_TEST"),
        "spacecoaster": SimConfig("spacecoaster", window_class="UnityWndClass"),
    })
    running = {"nolimits2": True, "spacecoaster": True}
    monkeypatch.setattr(manager, "is_running", lambda key: running[key])
    closed = []
    activated = []
    monkeypatch.setattr(manager, "close_sim", lambda key: (closed.append(key) or True, "closed"))
    monkeypatch.setattr(manager, "activate_sim", lambda key: (activated.append(key) or True, "activated"))

    ok, detail = manager.select_sim("spacecoaster")

    assert ok is True
    assert closed == ["nolimits2"]
    assert activated == ["spacecoaster"]
    assert manager.active_sim() == "spacecoaster"
    assert "close nolimits2" in detail


def test_manager_select_closes_old_sim_before_launching_target(monkeypatch):
    manager = WindowsSimManager({
        "nolimits2": SimConfig("nolimits2", window_class="NL3D_MAIN_TEST"),
        "spacecoaster": SimConfig("spacecoaster", window_class="UnityWndClass"),
    })
    running = {"nolimits2": True, "spacecoaster": False}
    monkeypatch.setattr(manager, "is_running", lambda key: running[key])
    actions = []
    monkeypatch.setattr(manager, "close_sim", lambda key: (actions.append(("close", key)) or True, "closed"))
    monkeypatch.setattr(manager, "ensure_sim", lambda key: (actions.append(("launch", key)) or True, "start initiated"))

    ok, detail = manager.select_sim("spacecoaster")

    assert ok is True
    assert actions == [("close", "nolimits2"), ("launch", "spacecoaster")]
    assert manager.active_sim() == "spacecoaster"
    assert "start initiated" in detail


def test_manager_select_does_not_launch_target_if_old_sim_will_not_close(monkeypatch):
    manager = WindowsSimManager({
        "nolimits2": SimConfig("nolimits2", window_class="NL3D_MAIN_TEST"),
        "spacecoaster": SimConfig("spacecoaster", window_class="UnityWndClass"),
    })
    running = {"nolimits2": True, "spacecoaster": False}
    monkeypatch.setattr(manager, "is_running", lambda key: running[key])
    launched = []
    monkeypatch.setattr(manager, "close_sim", lambda _key: (False, "close timeout"))
    monkeypatch.setattr(manager, "ensure_sim", lambda key: (launched.append(key) or True, "start initiated"))

    ok, detail = manager.select_sim("spacecoaster")

    assert ok is False
    assert launched == []
    assert "close timeout" in detail


class FakeRelaySocket:
    def __init__(self):
        self.sent = []

    def sendto(self, data, address):
        self.sent.append((data, address))


def test_spacecoaster_relay_uses_latest_poc_heartbeat_address():
    manager = FakeManager()
    server = SimHostService(manager, spacecoaster_relay_target_port=10009)
    relay = FakeRelaySocket()

    ping = encode_message({"type": TYPE_PING, "id": 1})
    server.handle_datagram(ping, ("192.168.1.50", 51000))

    payload = b"state,2\r\n"
    assert server.forward_spacecoaster_datagram(payload, ("127.0.0.1", 60000), relay) is True
    assert relay.sent == [(payload, ("192.168.1.50", 10009))]


def test_spacecoaster_relay_drops_until_poc_heartbeat_seen():
    manager = FakeManager()
    server = SimHostService(manager)
    relay = FakeRelaySocket()

    assert server.forward_spacecoaster_datagram(b"xyzrpy,1,2,3", ("127.0.0.1", 60000), relay) is False
    assert relay.sent == []


def test_spacecoaster_relay_packet_classification_is_diagnostic_only():
    assert SimHostService._spacecoaster_packet_kind(b"xyzrpy,1,2,3") == "xyzrpy"
    assert SimHostService._spacecoaster_packet_kind(b"posrpy,1,2,3") == "posrpy"
    assert SimHostService._spacecoaster_packet_kind(b"state,2\r\n") == "state"
    assert SimHostService._spacecoaster_packet_kind(b"command,foo") == "command"
    assert SimHostService._spacecoaster_packet_kind(b"config,foo") == "config"
    assert SimHostService._spacecoaster_packet_kind(b"something,new") == "unknown"


def test_spacecoaster_startup_replays_cached_ready_state_to_restarted_poc():
    manager = FakeManager()
    manager.running.add("spacecoaster")
    server = SimHostService(manager, spacecoaster_relay_target_port=10009)
    relay = FakeRelaySocket()
    server._relay_sock = relay

    # Cache READY while no POC is present, as happens while the Pi reboots.
    assert server.forward_spacecoaster_datagram(
        b"state,2\n", ("127.0.0.1", 54321), relay
    ) is False
    request = encode_message({
        "type": TYPE_STARTUP,
        "id": 45,
        "protocol": PROTOCOL_VERSION,
        "sim": "spacecoaster",
    })

    response = decode_message(server.handle_datagram(request, ("192.168.0.3", 51000)))

    assert response["ok"] is True
    assert manager.ensure_calls == ["spacecoaster"]
    assert relay.sent == [(b"state,2\n", ("192.168.0.3", 10009))]


def test_spacecoaster_new_launch_does_not_replay_cached_state():
    manager = FakeManager()  # FakeManager reports "start initiated".
    server = SimHostService(manager, spacecoaster_relay_target_port=10009)
    relay = FakeRelaySocket()
    server._relay_sock = relay
    server._spacecoaster_last_state = b"state,2\n"
    request = encode_message({
        "type": TYPE_STARTUP,
        "id": 46,
        "protocol": PROTOCOL_VERSION,
        "sim": "spacecoaster",
    })

    response = decode_message(server.handle_datagram(request, ("192.168.0.3", 51000)))

    assert response["ok"] is True
    assert relay.sent == []
    assert server._spacecoaster_last_state is None
    assert server._spacecoaster_unity_addr is None


def test_switching_away_from_spacecoaster_clears_process_specific_relay_state():
    manager = FakeManager()
    manager.current = "spacecoaster"
    manager.running.add("spacecoaster")
    server = SimHostService(manager, spacecoaster_relay_target_port=10009)
    server._spacecoaster_last_state = b"state,2\n"
    server._spacecoaster_unity_addr = ("127.0.0.1", 54321)
    request = encode_message({
        "type": TYPE_SELECT_SIM,
        "id": 48,
        "protocol": PROTOCOL_VERSION,
        "sim": "nolimits2",
    })

    response = decode_message(server.handle_datagram(request, ("192.168.0.3", 51000)))

    assert response["ok"] is True
    assert manager.select_calls == ["nolimits2"]
    assert server._spacecoaster_last_state is None
    assert server._spacecoaster_unity_addr is None


def test_existing_spacecoaster_without_cached_state_requests_lobby():
    manager = FakeManager()
    manager.running.add("spacecoaster")
    server = SimHostService(manager, spacecoaster_relay_target_port=10009)
    relay = FakeRelaySocket()
    server._relay_sock = relay
    server._spacecoaster_unity_addr = ("127.0.0.1", 54321)
    request = encode_message({
        "type": TYPE_STARTUP,
        "id": 47,
        "protocol": PROTOCOL_VERSION,
        "sim": "spacecoaster",
    })

    response = decode_message(server.handle_datagram(request, ("192.168.0.3", 51000)))

    assert response["ok"] is True
    assert manager.ensure_calls == ["spacecoaster"]
    assert relay.sent == [(b"control,lobby\n", ("127.0.0.1", 54321))]


def test_spacecoaster_transition_state_does_not_replace_cached_stable_state():
    server = SimHostService(FakeManager())
    relay = FakeRelaySocket()
    server.forward_spacecoaster_datagram(b"state,2\n", ("127.0.0.1", 54321), relay)
    server.forward_spacecoaster_datagram(b"state,99\n", ("127.0.0.1", 54321), relay)
    assert server._spacecoaster_last_state == b"state,2\n"


def test_server_stop_sets_stop_event():
    manager = FakeManager()
    server = SimHostService(manager)
    assert server._stop_event.is_set() is False
    server.stop()
    assert server._stop_event.is_set() is True


def test_large_unknown_spacecoaster_datagram_is_forwarded_unchanged_by_default():
    manager = FakeManager()
    server = SimHostService(manager, spacecoaster_relay_target_port=10009)
    relay = FakeRelaySocket()
    server._poc_address = "192.168.1.50"
    payload = b"unknown," + (b"x" * 1000)
    assert server.forward_spacecoaster_datagram(payload, ("127.0.0.1", 60000), relay) is True
    assert relay.sent == [(payload, ("192.168.1.50", 10009))]


def test_spacecoaster_udp_control_uses_learned_unity_endpoint():
    manager = FakeManager()
    server = SimHostService(manager)
    relay = FakeRelaySocket()
    server._relay_sock = relay

    # Learn the ephemeral Unity return endpoint from actual telemetry.
    server.forward_spacecoaster_datagram(
        b"xyzrpy,1,2,3", ("127.0.0.1", 54321), relay
    )
    relay.sent.clear()

    ok, detail = server.send_spacecoaster_control("pause")
    assert ok is True
    assert detail == "UDP control sent: pause"
    assert relay.sent == [(b"control,pause\n", ("127.0.0.1", 54321))]


def test_all_spacecoaster_v8_udp_commands_route_semantically():
    manager = FakeManager()
    server = SimHostService(manager)
    relay = FakeRelaySocket()
    server._relay_sock = relay
    server._spacecoaster_unity_addr = ("127.0.0.1", 54321)

    for request_id, command in enumerate(("pause", "resume", "ready", "run", "reset_vr", "lobby"), start=70):
        request = encode_message({
            "type": "command",
            "id": request_id,
            "protocol": PROTOCOL_VERSION,
            "target": "spacecoaster",
            "command": command,
        })
        response = decode_message(server.handle_datagram(request, ("10.0.0.2", 50000)))
        assert response["ok"] is True

    assert relay.sent == [
        (b"control,pause\n", ("127.0.0.1", 54321)),
        (b"control,resume\n", ("127.0.0.1", 54321)),
        (b"control,ready\n", ("127.0.0.1", 54321)),
        (b"control,run\n", ("127.0.0.1", 54321)),
        (b"control,reset_vr\n", ("127.0.0.1", 54321)),
        (b"control,lobby\n", ("127.0.0.1", 54321)),
    ]
    assert manager.command_calls == []


def test_spacecoaster_udp_control_requires_learned_endpoint():
    server = SimHostService(FakeManager())
    server._relay_sock = FakeRelaySocket()
    ok, detail = server.send_spacecoaster_control("pause")
    assert ok is False
    assert "has not been learned" in detail


def test_client_request_ids_do_not_restart_at_one(monkeypatch):
    from sim_host_service import client as client_module

    monkeypatch.setattr(client_module.time, "time_ns", lambda: 0x12345678)
    client = client_module.SimHostClient("127.0.0.1", 10040)
    assert client._next_id == 0x12345678
    assert client._next_id != 1


def test_client_sends_replaceable_fan_state_without_pending_request():
    fake_socket = FakeSocket()
    client = SimHostClient(
        "127.0.0.1",
        10040,
        socket_factory=lambda *args: fake_socket,
    )
    client.start()
    client._connected = True

    assert client.send_fan_power(12.5, 53) is True
    message = fake_socket.sent[-1][0]
    assert message["type"] == TYPE_FAN_POWER
    assert message["speed"] == 12.5
    assert message["power"] == 53
    assert client._pending == {}


def test_server_forwards_fan_power_and_forces_zero_after_timeout():
    clock = FakeClock()
    fan = FakeFan()
    server = SimHostService(FakeManager(), fan=fan, time_func=clock, fan_command_timeout_s=1.0)
    message = encode_message({
        "type": TYPE_FAN_POWER,
        "id": 80,
        "protocol": PROTOCOL_VERSION,
        "speed": 11.75,
        "power": 50,
    })

    assert server.handle_datagram(message, ("10.0.0.2", 50000)) is None
    assert fan.powers == [50]
    assert server.status_snapshot()["fan_power"] == 50
    clock.now = 1.1
    server._service_fan_timeout()
    assert fan.powers == [50, 0]
    assert server.status_snapshot()["fan_power"] == 0
    assert "timeout" in server.status_snapshot()["last_error"]


def test_status_snapshot_exposes_connection_diagnostics(monkeypatch):
    clock = FakeClock()
    server = SimHostService(FakeManager(), fan=FakeFan(), time_func=clock)
    monkeypatch.setattr(
        "sim_host_service.server.local_ipv4_addresses",
        lambda: ["192.168.0.4", "192.168.1.50"],
    )

    initial = server.status_snapshot()
    assert initial["listen_state"] == "Starting"
    assert initial["listen_address"] == "0.0.0.0"
    assert initial["listen_port"] == 10040
    assert initial["last_datagram_age"] is None
    assert initial["local_ipv4"] == ["192.168.0.4", "192.168.1.50"]

    server.handle_datagram(b"not-json", ("192.168.0.3", 54321))
    received = server.status_snapshot()
    assert received["last_datagram_source"] == "192.168.0.3:54321"
    assert received["last_datagram_age"] == 0.0


def test_local_ipv4_addresses_excludes_loopback_and_duplicates(monkeypatch):
    monkeypatch.setattr("sim_host_service.server.socket.gethostname", lambda: "sim-pc")
    monkeypatch.setattr(
        "sim_host_service.server.socket.getaddrinfo",
        lambda *_args: [
            (socket.AF_INET, 0, 0, "", ("127.0.0.1", 0)),
            (socket.AF_INET, 0, 0, "", ("192.168.0.4", 0)),
            (socket.AF_INET, 0, 0, "", ("192.168.0.4", 0)),
        ],
    )
    assert local_ipv4_addresses() == ["192.168.0.4"]
