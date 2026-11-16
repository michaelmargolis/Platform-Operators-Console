#test_dynamic_core_equivalence_v201.py ver 1
"""Static 20 Hz command parity against golden POC v200 results.

Golden SHA256s were produced by executing the immutable v200 PocCore and
TelemetryConfig on these exact deterministic samples before implementation.
No Git checkout or hardware is needed to repeat this regression test.
"""
from __future__ import annotations
from pathlib import Path
import hashlib
import json
import sys
import types

import pytest
from core.telemetry_config import TelemetryConfig
from core.types import SimSample, ExperienceState, PlatformMotionState, PlatformState

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = {
    0: "cc1d8f3410c0c0c467dbdc6901aca7a5d47931bd353558f6c9aa2f910060de99",
    40: "cb113745cb7a1ee0f4f627e8e207ef38da0a1319de644d6844482d4176eff512",
    65: "8cc6b1e8b50fafc7754633f6e9030165c0be5341791567ea41561bdfa18b4fc8",
    100: "aea416214072da4dc558be594eef49eea043c66f88ee817ead01d1d5c549f7b9",
}


class Signal:
    def __init__(self): self.calls=[]
    def emit(self,*x): self.calls.append(x)


class DummyPlatform:
    state=PlatformState.ACTIVATED
    parking_state=PlatformMotionState.UNPARKED
    activation_percent=100
    def __init__(self): self.moves=[]
    def service(self,dt): pass
    def move(self,value): self.moves.append(tuple(value))


class DummySim:
    supports_washout=True
    def __init__(self,samples): self.samples=iter(samples)
    def read(self): return next(self.samples)


class FakeCore:
    DATA_PERIOD_MS=50
    _dispatch_unpark_pending=False
    _estop_latched=False
    _estop_recovery_active=False
    _estop_running_pause_sent=False
    _ride_dispatched=True
    _ride_has_run=True
    host_service_enabled=False
    def __init__(self,samples,config,sim_key,intensity):
        self.remote=types.SimpleNamespace(service=lambda:None)
        self.sim=DummySim(samples)
        self.sim_key=sim_key
        self.platform=DummyPlatform()
        self.telemetry_config=config
        self.intensity_percent=intensity
        self.experience_state=ExperienceState.RUNNING
        self._motion_transform=(0.,)*6
        self.platformActivationChanged=Signal()
        self.simStatusChanged=Signal()
        self.sampleUpdated=Signal()
        self._dynamic_runtime=None
        self._dynamic_profile_required=False
    def _set_experience_state(self,state): self.experience_state=state
    def _handle_normal_completion(self,sample): pass
    def _advance_pending_dispatch(self): pass
    def _sync_nl2_angular_washout(self): pass
    def _update_fan(self,sample): pass
    def _send_digital_twin_snapshot(self,sample,applied): pass
    def get_sim_status(self): return None


@pytest.mark.parametrize('sim_key',('nolimits2','spacecoaster'))
@pytest.mark.parametrize('intensity',(0,40,65,100))
def test_static_20hz_path_matches_v200_golden(monkeypatch,sim_key,intensity):
    try:
        import PyQt5  # noqa
    except ImportError:
        pyqt=types.ModuleType('PyQt5'); core=types.ModuleType('PyQt5.QtCore')
        core.QObject=object; core.pyqtSignal=lambda *a,**kw:None
        pyqt.QtCore=core
        monkeypatch.setitem(sys.modules,'PyQt5',pyqt)
        monkeypatch.setitem(sys.modules,'PyQt5.QtCore',core)
    from core.poc_core import PocCore
    ini=ROOT/'sims/nolimits2/telemetry_config.ini'
    samples=[SimSample(transform=tuple((i%7-3)*(j+1)*.03 for j in range(6)),
                       state=ExperienceState.RUNNING,telemetry_valid=True,speed=10.) for i in range(50)]
    fake=FakeCore(samples,TelemetryConfig(ini,max_intensity_percent=100),sim_key,intensity)
    for _ in samples:
        PocCore._data_tick_body(fake)
    raw=json.dumps(fake.platform.moves,separators=(',',':'),allow_nan=False).encode()
    assert hashlib.sha256(raw).hexdigest()==GOLDEN[intensity]
    assert len(fake.platform.moves)==50
    assert len(fake.sampleUpdated.calls)==50
    assert len(fake.simStatusChanged.calls)==50


def test_dynamic_stage_is_after_washout_and_before_platform_move(monkeypatch):
    try:
        import PyQt5  # noqa
    except ImportError:
        pyqt=types.ModuleType('PyQt5');core=types.ModuleType('PyQt5.QtCore')
        core.QObject=object;core.pyqtSignal=lambda *a,**kw:None;pyqt.QtCore=core
        monkeypatch.setitem(sys.modules,'PyQt5',pyqt)
        monkeypatch.setitem(sys.modules,'PyQt5.QtCore',core)
    from core.poc_core import PocCore
    class Runtime:
        def __init__(self): self.seen=[]
        def tick(self,raw): self.seen.append(raw)
        def multiply(self,value):
            self.seen.append(('post-washout',tuple(value)))
            return tuple(x * 1.25 for x in value)
    tm=object()
    sample=SimSample(transform=(.25,.1,.15,.4,.2,.3),state=ExperienceState.RUNNING,telemetry_valid=True,speed=12.)
    fak=FakeCore([sample],TelemetryConfig(ROOT/'sims/nolimits2/telemetry_config.ini'),'nolimits2',100)
    fak.sim.client=types.SimpleNamespace(latest_telemetry=tm)
    fak._dynamic_runtime=Runtime()
    fak._dynamic_profile_required=True
    PocCore._data_tick_body(fak)
    assert fak._dynamic_runtime.seen[0] is tm
    processed=fak._dynamic_runtime.seen[1][1]
    assert fak.platform.moves==[tuple(x*1.25 for x in processed)]
    assert fak._motion_transform==fak.platform.moves[0]


def test_unavailable_dynamic_profile_never_runs_static_and_preserves_ui(monkeypatch):
    try:
        import PyQt5  # noqa
    except ImportError:
        pyqt=types.ModuleType('PyQt5');core=types.ModuleType('PyQt5.QtCore')
        core.QObject=object;core.pyqtSignal=lambda *a,**kw:None;pyqt.QtCore=core
        monkeypatch.setitem(sys.modules,'PyQt5',pyqt)
        monkeypatch.setitem(sys.modules,'PyQt5.QtCore',core)
    from core.poc_core import PocCore
    sample=SimSample(transform=(.25,.1,.15,.4,.2,.3),state=ExperienceState.RUNNING,telemetry_valid=True,speed=12.)
    fak=FakeCore([sample],TelemetryConfig(ROOT/'sims/nolimits2/telemetry_config.ini'),'nolimits2',100)
    fak._dynamic_runtime=None
    fak._dynamic_profile_required=True
    send=[]
    fak._send_digital_twin_snapshot=lambda samp,applied:send.append(applied)
    PocCore._data_tick_body(fak)
    assert fak.platform.moves==[]
    assert send==[False]
    assert len(fak.sampleUpdated.calls)==1


def test_dynamic_invalid_transform_withholds_physical_command(monkeypatch):
    """A bad motion sample must not command a sudden move to neutral."""
    try:
        import PyQt5  # noqa
    except ImportError:
        pyqt=types.ModuleType('PyQt5');core=types.ModuleType('PyQt5.QtCore')
        core.QObject=object;core.pyqtSignal=lambda *a,**kw:None;pyqt.QtCore=core
        monkeypatch.setitem(sys.modules,'PyQt5',pyqt)
        monkeypatch.setitem(sys.modules,'PyQt5.QtCore',core)
    from core.poc_core import PocCore
    from core.dynamic_profiles import DynamicProfileError
    class Runtime:
        def tick(self, raw): pass
        def multiply(self, values):
            raise DynamicProfileError('Injected invalid multiplier')
    sample=SimSample(transform=(.1,)*6,state=ExperienceState.RUNNING,telemetry_valid=True,speed=5.)
    fake=FakeCore([sample],TelemetryConfig(ROOT/'sims/nolimits2/telemetry_config.ini'), 'nolimits2',100)
    fake.sim.client=types.SimpleNamespace(latest_telemetry=object())
    fake._dynamic_runtime=Runtime(); fake._dynamic_profile_required=True; fake._dynamic_issue=''
    applied=[]
    fake._send_digital_twin_snapshot=lambda sample,is_applied:applied.append(is_applied)
    PocCore._data_tick_body(fake)
    assert fake.platform.moves == []
    assert applied == [False]
    assert fake._dynamic_issue == 'Injected invalid multiplier'
