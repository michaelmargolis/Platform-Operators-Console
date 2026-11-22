#test_shs_local_sim_cycle_v241.py ver 3
from pathlib import Path
import importlib.util
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "tools" / "shs_local_sim_cycle.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("shs_local_sim_cycle", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_spacecoaster_state_parser_only_accepts_state_messages():
    module = _load_script()
    assert module._parse_spacecoaster_state("state,0") == 0
    assert module._parse_spacecoaster_state("state,2\n") == 2
    assert module._parse_spacecoaster_state("xyzrpy,1,2,3") is None
    assert module._parse_spacecoaster_state("state,bad") is None


def test_script_uses_raw_nl2_telemetry_and_never_full_adapter():
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "WindowsSimManager" in source
    assert "NL2Protocol" in source
    assert "create_sim" not in source
    assert "status.ready_to_start" not in source
    assert "telemetry.in_play_mode" in source
    assert ".load_park(" not in source
    assert 'sock.sendto(b"control,ready", target)' in source
    assert "SPACECOASTER_MAX_READY_ATTEMPTS = 10" in source
    assert "ready attempt" in source
    assert "state == 2" in source
    assert "_is_udp_connection_reset" in source
    assert "10054" in source
    assert "--dont_restart_oculus" in source
    assert "restart_oculus=not args.dont_restart_oculus" in source
    assert "Stop the normal Sim Host Service" in source


def test_nolimits_wait_advances_on_play_mode_telemetry_without_ride_commands():
    module = _load_script()

    class FakeProtocol:
        instance = None

        def __init__(self, address, connect_timeout):
            assert address == module.LOCALHOST
            assert connect_timeout == 0.5
            self.is_connected = False
            self.latest_telemetry = None
            self.last_error = ""
            self.telemetry_requests = 0
            self.disconnected = False
            FakeProtocol.instance = self

        def connect(self):
            self.is_connected = True
            return True

        def service(self):
            return None

        def request_telemetry(self):
            self.telemetry_requests += 1
            if self.telemetry_requests == 1:
                self.last_error = "Not in play mode"
            else:
                self.last_error = ""
                self.latest_telemetry = SimpleNamespace(
                    in_play_mode=True,
                    state=5,
                    frame=123,
                )

        def disconnect(self):
            self.is_connected = False
            self.disconnected = True

    elapsed = module.wait_for_nolimits_ready(1.0, protocol_factory=FakeProtocol)
    assert elapsed < 1.0
    assert FakeProtocol.instance is not None
    assert FakeProtocol.instance.telemetry_requests >= 2
    assert FakeProtocol.instance.disconnected is True


def test_nolimits_launcher_opens_paused_after_vr():
    batch = (ROOT / "sim_host_service" / "sims" / "nolimits2.bat").read_text(
        encoding="utf-8"
    )
    assert "--telemetry --vr --openpaused --openpark" in batch
