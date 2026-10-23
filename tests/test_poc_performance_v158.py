# test_poc_performance_v158.py  ver 1
"""Dependency-free tests of POC's 20 Hz callback timing instrumentation.

The real POC imports PyQt5 and hardware interfaces. Extract the actual callback
wrapper from source so these tests exercise its implementation without creating
hardware objects or requiring a display server.
"""
from __future__ import annotations

import ast
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]


class Clock:
    def __init__(self):
        self.now = 0.0

    def perf_counter(self):
        return self.now


def timing_callback(clock):
    """Compile the actual callback method, not a copied implementation."""
    source = (ROOT / "core" / "poc_core.py").read_text(encoding="utf-8")
    module = ast.parse(source)
    poc_core = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == "PocCore")
    method = next(n for n in poc_core.body if isinstance(n, ast.FunctionDef) and n.name == "_data_tick")
    namespace = {"time": clock}
    exec(compile(ast.Module(body=[method], type_ignores=[]), "core/poc_core.py", "exec"), namespace)
    return namespace["_data_tick"]


def fake_core(clock, timings=(0.010, 0.030, 0.005), window=200):
    durations = iter(timings)
    core = SimpleNamespace(
        DATA_PERIOD_MS=50,
        _last_data_tick_started_at=None,
        jitter_percent=0,
        processing_percent=0,
        processing_max_percent=0,
        _processing_history=deque(maxlen=window),
    )

    def work():
        clock.now += next(durations)

    core._data_tick_body = work
    return core


def test_processing_jitter_and_rolling_maximum():
    clock = Clock()
    core = fake_core(clock, window=2)
    callback = timing_callback(clock)

    callback(core)  # 10 ms callback, first interval cannot have jitter
    assert (core.processing_percent, core.jitter_percent, core.processing_max_percent) == (20, 0, 20)

    clock.now = 0.050
    callback(core)  # 30 ms callback with 50 ms start-to-start interval
    assert (core.processing_percent, core.jitter_percent, core.processing_max_percent) == (60, 0, 60)

    clock.now = 0.115
    callback(core)  # 5 ms; start was 65 ms after last -> 30% jitter
    assert (core.processing_percent, core.jitter_percent, core.processing_max_percent) == (10, 30, 60)

    clock.now = 0.165
    core._data_tick_body = lambda: setattr(clock, "now", clock.now + 0.002)
    callback(core)
    assert (core.processing_percent, core.jitter_percent, core.processing_max_percent) == (4, 0, 10)
    assert len(core._processing_history) == 2


def test_processing_recorded_on_early_return_and_exception():
    clock = Clock()
    callback = timing_callback(clock)
    core = fake_core(clock, timings=())

    def early_return():
        clock.now += 0.015
        return

    core._data_tick_body = early_return
    callback(core)
    assert core.processing_percent == 30

    def broken():
        clock.now += 0.025
        raise RuntimeError("test failure")

    core._data_tick_body = broken
    clock.now = 0.050
    with pytest.raises(RuntimeError, match="test failure"):
        callback(core)
    assert core.processing_percent == 50
    assert core.processing_max_percent == 50
    assert core.jitter_percent == 0


def test_snapshot_and_debug_ui_include_both_metrics():
    core_source = (ROOT / "core" / "poc_core.py").read_text(encoding="utf-8")
    ui_source = (ROOT / "ui" / "main_window.py").read_text(encoding="utf-8")
    for field in ("processing_percent", "jitter_percent", "processing_max_percent"):
        assert f'"{field}": self.{field}' in core_source
        assert f"snapshot['{field}']" in ui_source
    assert "max(10s)" in ui_source
