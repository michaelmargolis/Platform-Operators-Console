#test_activation_control.py  ver 2
#test_activation_control.py  ver 1
from types import SimpleNamespace

from core.poc_core import PocCore
from core.types import ActivationControlMode, PlatformState


class RecordingSignal:
    def __init__(self):
        self.values = []

    def emit(self, *args):
        self.values.append(args)


def fail_if_called(*_args, **_kwargs):
    raise AssertionError("inactive activation source commanded platform motion")


def test_physical_on_during_deactivation_is_consumed_not_queued():
    core = SimpleNamespace(
        activation_control_mode=ActivationControlMode.SWITCH,
        _activation_switch_armed=True,
        _activation_request_result="",
        platform=SimpleNamespace(state=PlatformState.DEACTIVATED, parked=False),
        message=RecordingSignal(),
        _begin_activation=fail_if_called,
    )
    PocCore.request_switch_activate(core)
    assert not core._activation_switch_armed
    assert "DEACTIVATED" in core._activation_request_result
    assert "OFF cycle required" in core._activation_request_result


def test_physical_switch_is_inert_in_touch_mode():
    core = SimpleNamespace(
        activation_control_mode=ActivationControlMode.TOUCH,
        _activation_switch_armed=True,
        _activation_request_result="",
        platform=SimpleNamespace(state=PlatformState.DEACTIVATED),
        message=RecordingSignal(),
        activateSwitchWarningChanged=RecordingSignal(),
        _begin_activation=fail_if_called,
        _begin_deactivation=fail_if_called,
    )
    PocCore.request_switch_activate(core)
    PocCore.request_switch_deactivate(core)
    assert "ignored" in core._activation_request_result


def test_touch_is_inert_in_switch_mode():
    core = SimpleNamespace(
        activation_control_mode=ActivationControlMode.SWITCH,
        _activation_request_result="",
        platform=SimpleNamespace(state=PlatformState.DEACTIVATED),
        message=RecordingSignal(),
        _begin_activation=fail_if_called,
        _begin_deactivation=fail_if_called,
    )
    PocCore.request_touch_activation_toggle(core)
    assert "ignored" in core._activation_request_result
    assert core.message.values == [
        ("Activation is controlled by the physical switch",)
    ]
