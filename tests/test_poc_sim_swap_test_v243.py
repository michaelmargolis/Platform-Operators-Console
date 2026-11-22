#test_poc_sim_swap_test_v243.py ver 1
from pathlib import Path
import importlib.util
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "tools" / "poc_sim_swap_test.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("poc_sim_swap_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_spacecoaster_state_parser_only_accepts_state_messages():
    module = _load_script()
    assert module._parse_spacecoaster_state("state,0") == 0
    assert module._parse_spacecoaster_state("state,2\n") == 2
    assert module._parse_spacecoaster_state("posrpy,0,0,0") is None
    assert module._parse_spacecoaster_state("state,bad") is None


def test_script_is_lean_poc_side_shs_swap_exerciser():
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "SimHostClient" in source
    assert "NL2Protocol" in source
    assert 'client.select_sim(sim_key)' in source
    assert 'client.command("spacecoaster", "ready")' in source
    assert "PlatformController" not in source
    assert "from output" not in source.casefold()
    assert "import output" not in source.casefold()
    assert "festo_itf" not in source.casefold()
    assert "muscle_output" not in source
    assert "WindowsSimManager" not in source
    assert "Restart-Service" not in source
    assert "Normal POC application must remain stopped" in source


def test_nolimits_wait_advances_on_remote_play_mode_telemetry():
    module = _load_script()

    class FakeProtocol:
        instance = None

        def __init__(self, address, connect_timeout):
            assert address == "192.168.0.4"
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
                    frame=321,
                )

        def disconnect(self):
            self.is_connected = False
            self.disconnected = True

    elapsed = module.wait_for_nolimits_play_mode(
        "192.168.0.4", 1.0, protocol_factory=FakeProtocol
    )
    assert elapsed < 1.0
    assert FakeProtocol.instance is not None
    assert FakeProtocol.instance.telemetry_requests >= 2
    assert FakeProtocol.instance.disconnected is True
