#test_physical_controls.py  ver 6
from controls.physical_controls import LocalControlItf


class FakeButtons:
    initial_values = {}

    def __init__(self, callback):
        self.callback = callback
        self.entries = []
        self.closed = False

    def append(self, pin, key, pull_mode="pullup", edge="falling"):
        self.entries.append((pin, key, pull_mode, edge))

    def value(self, pin):
        return self.initial_values.get(pin, 1)

    def service(self):
        return None

    def close(self):
        self.closed = True


class FakeButtonsModule:
    Buttons = FakeButtons


class FakeDecoder:
    def __init__(self, pin_a, pin_b, callback):
        self.pin_a = pin_a
        self.pin_b = pin_b
        self.callback = callback
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class FakeRotaryModule:
    decoder = FakeDecoder


def make_controls(initial_load=1, initial_activate=1):
    calls = []

    def record(name):
        return lambda *args: calls.append((name, args))

    action_names = (
        "activate",
        "activate_startup_invalid",
        "deactivate",
        "dispatch_press",
        "dispatch_release",
        "pause",
        "reset_vr",
        "payload",
        "encoder_rotate",
        "encoder_press",
    )
    controls = LocalControlItf({name: record(name) for name in action_names})
    FakeButtons.initial_values = {
        controls.pins["LOAD_PIN"]: initial_load,
        controls.pins["ACTIVATE_PIN"]: initial_activate,
    }
    controls._load_gpio_modules = lambda: (FakeButtonsModule, FakeRotaryModule)
    assert controls.start()
    return controls, calls


def entry_for(controls, pin_name):
    pin = controls.pins[pin_name]
    return next(entry for entry in controls.buttons.entries if entry[0] == pin)


def test_load_high_is_normal_and_applied_at_startup():
    controls, calls = make_controls(initial_load=1)
    assert calls == [("payload", (1,)), ("deactivate", ())]
    controls._button_callback("load_full")
    controls._button_callback("load_normal")
    assert calls[-2:] == [("payload", (2,)), ("payload", (1,))]


def test_load_low_is_full_and_applied_at_startup():
    _controls, calls = make_controls(initial_load=0)
    assert calls == [("payload", (2,)), ("deactivate", ())]


def test_activate_switch_on_at_startup_warns_without_activating():
    _controls, calls = make_controls(initial_activate=0)
    assert calls == [
        ("payload", (1,)),
        ("activate_startup_invalid", ()),
    ]


def test_common_panel_actions_use_semantic_action_map():
    controls, calls = make_controls()
    calls.clear()
    for event in (
        "activate",
        "deactivate",
        "dispatch_press",
        "dispatch_release",
        "pause",
        "reset_vr",
    ):
        controls._button_callback(event)
    assert [name for name, _args in calls] == [
        "activate",
        "deactivate",
        "dispatch_press",
        "dispatch_release",
        "pause",
        "reset_vr",
    ]


def test_dispatch_switch_reports_debounced_press_and_release_edges():
    controls, _calls = make_controls()
    assert entry_for(controls, "DISPATCH_PIN") == (
        18,
        ["dispatch_press", "dispatch_release"],
        "pullup",
        "both",
    )


def test_encoder_switch_is_pullup_falling_edge_on_bcm17():
    controls, _calls = make_controls()
    assert entry_for(controls, "ENCODER_SW_PIN") == (
        17,
        "encoder_press",
        "pullup",
        "falling",
    )


def test_encoder_callback_queues_until_service_and_preserves_order():
    controls, calls = make_controls()
    calls.clear()
    controls._encoder_debounce_s = 0
    controls._encoder_callback(1)
    controls._encoder_callback(-1)
    assert calls == []
    controls.service()
    assert calls == [("encoder_rotate", (1,)), ("encoder_rotate", (-1,))]


def test_encoder_debounce_rejects_bounce_but_accepts_next_detent():
    controls, calls = make_controls()
    calls.clear()
    times = iter((1.000, 1.005, 1.030))
    controls._clock = lambda: next(times)
    controls._encoder_callback(1)
    controls._encoder_callback(1)
    controls._encoder_callback(1)
    controls.service()
    assert calls == [("encoder_rotate", (1,)), ("encoder_rotate", (1,))]


def test_encoder_press_uses_common_semantic_action_path():
    controls, calls = make_controls()
    calls.clear()
    controls._button_callback("encoder_press")
    assert calls == [("encoder_press", ())]


def test_stop_releases_button_and_encoder_resources():
    controls, _calls = make_controls()
    buttons = controls.buttons
    decoder = controls.decoder
    controls.stop()
    assert buttons.closed
    assert decoder.cancelled
    assert not controls.is_connected
