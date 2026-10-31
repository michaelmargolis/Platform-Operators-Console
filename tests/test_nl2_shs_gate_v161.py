#test_nl2_shs_gate_v161.py  ver 1
"""Regression coverage for NoLimits2 TCP startup gating (no Qt required)."""
import ast
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def _method(name):
    module = ast.parse((ROOT / "core/poc_core.py").read_text(encoding="utf-8"))
    core = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == "PocCore")
    fn = next(n for n in core.body if isinstance(n, ast.FunctionDef) and n.name == name)
    ns = {}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "core/poc_core.py", "exec"), ns)
    return ns[name]


def test_service_gate_with_shs_offline_then_online():
    calls = []
    sim = SimpleNamespace(client=SimpleNamespace(is_connected=False), service=lambda: calls.append("service"))
    poc = SimpleNamespace(sim_key="nolimits2", host_service_enabled=True,
                          host_service=SimpleNamespace(connected=False), sim=sim)
    tick = _method("_service_tick")
    tick(poc)
    assert calls == []
    poc.host_service.connected = True
    tick(poc)
    assert calls == ["service"]


def test_connected_nl2_is_serviced_through_shs_loss():
    calls = []
    sim = SimpleNamespace(client=SimpleNamespace(is_connected=True), service=lambda: calls.append(1))
    poc = SimpleNamespace(sim_key="nolimits2", host_service_enabled=True,
                          host_service=SimpleNamespace(connected=False), sim=sim)
    _method("_service_tick")(poc)
    assert calls == [1]


def test_shs_disabled_and_other_sim_unaffected():
    tick = _method("_service_tick")
    for name, enabled in (("nolimits2", False), ("spacecoaster", True)):
        calls = []
        sim = SimpleNamespace(service=lambda: calls.append(1))
        poc = SimpleNamespace(sim_key=name, host_service_enabled=enabled,
                              host_service=SimpleNamespace(connected=False), sim=sim)
        tick(poc)
        assert calls == [1]


def test_initial_nl2_connection_is_deferred_when_shs_offline():
    source = (ROOT / "core/poc_core.py").read_text(encoding="utf-8")
    assert 'if not (key == "nolimits2" and self.host_service_enabled' in source
    assert 'and not self.host_service.connected):\n            self.sim.connect()' in source
