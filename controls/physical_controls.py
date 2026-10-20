#physical_controls.py  ver 7
"""Raspberry Pi physical control-panel adapter for POC.

GPIO callbacks never call PocCore directly. Buttons are polled by ``service()``
from the Qt/main thread, while rotary GPIO callbacks queue detents that are
forwarded as semantic encoder events on the next ``service()`` call.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Mapping

log = logging.getLogger(__name__)
ENCODER_DEBOUNCE_S = 0.025

PCB_PINS = {
    "DISPATCH_PIN": 18,
    "PAUSE_PIN": 15,
    "VR_RESET_PIN": 27,
    "LOAD_PIN": 23,
    "ACTIVATE_PIN": 24,
    "ENCODER_A": 4,
    "ENCODER_B": 14,
    "ENCODER_SW_PIN": 17,
}


class LocalControlItf:
    """Expose Raspberry Pi panel events through the common POC action map."""

    def __init__(self, actions: Mapping[str, Callable], initial_intensity: int = 10):
        self.actions = dict(actions)
        self.pins = dict(PCB_PINS)
        self.intensity = max(0, min(10, int(initial_intensity)))
        self.buttons = None
        self.decoder = None
        self._encoder_events = []
        self._encoder_lock = threading.Lock()
        self._encoder_debounce_s = ENCODER_DEBOUNCE_S
        self._last_encoder_event_at = 0.0
        self._clock = time.monotonic

        # Match SerialRemote's small status surface so PocCore does not need a
        # second lifecycle/debug path for local GPIO controls.
        self.is_connected = False
        self.port = "GPIO"
        self.last_event = ""
        self.last_error = ""

    def _load_gpio_modules(self):
        # Deliberately deferred. PocCore imports this module only on a Raspberry
        # Pi, and even then RPi.GPIO is not imported until start() is called.
        from controls import buttons, rotary_encoder

        return buttons, rotary_encoder

    def start(self) -> bool:
        if self.is_connected:
            return True
        try:
            buttons_module, rotary_module = self._load_gpio_modules()
            self.buttons = buttons_module.Buttons(self._button_callback)
            # DISPATCH retains its normal press action while activated. While
            # deactivated, its held state temporarily selects simulator browse
            # mode, so both the press and release edges are required.
            self.buttons.append(
                self.pins["DISPATCH_PIN"],
                ["dispatch_press", "dispatch_release"],
                "pullup",
                "both",
            )
            self.buttons.append(self.pins["PAUSE_PIN"], "pause", "pullup", "falling")
            self.buttons.append(self.pins["VR_RESET_PIN"], "reset_vr", "pullup", "falling")
            # Pull-up selector: LOW requests activation, HIGH requests deactivation.
            self.buttons.append(
                self.pins["ACTIVATE_PIN"], ["activate", "deactivate"], "pullup", "both"
            )
            # Pull-up load selector specified for POC:
            #   open/high -> NORMAL (payload level 1)
            #   closed/low -> FULL   (payload level 2)
            self.buttons.append(
                self.pins["LOAD_PIN"], ["load_full", "load_normal"], "pullup", "both"
            )
            self.buttons.append(
                self.pins["ENCODER_SW_PIN"], "encoder_press", "pullup", "falling"
            )
            self.decoder = rotary_module.decoder(
                self.pins["ENCODER_A"], self.pins["ENCODER_B"], self._encoder_callback
            )
            self.is_connected = True
            self.last_error = ""

            # A two-position load switch has meaningful state at startup; apply
            # it immediately rather than waiting for the first edge transition.
            self._apply_load_level(self.buttons.value(self.pins["LOAD_PIN"]))

            # Never activate from the switch state found at application startup.
            # If it was left in ACTIVATE (LOW), require the operator to return it
            # to DEACTIVATE before a later deliberate activation edge is accepted.
            if self.buttons.value(self.pins["ACTIVATE_PIN"]) == 0:
                self._invoke(
                    "activate_startup_invalid",
                    event="activate switch ON at startup",
                )
            else:
                self._invoke(
                    "deactivate",
                    event="activate switch OFF at startup",
                )
            log.info("Raspberry Pi physical controls initialized")
            return True
        except Exception as exc:
            self.last_error = str(exc)
            self.is_connected = False
            log.exception("Unable to initialize Raspberry Pi physical controls")
            self.stop()
            return False

    def stop(self) -> None:
        decoder = self.decoder
        self.decoder = None
        if decoder is not None:
            try:
                decoder.cancel()
            except Exception:
                log.exception("Unable to stop Raspberry Pi rotary encoder")

        buttons = self.buttons
        self.buttons = None
        if buttons is not None:
            try:
                buttons.close()
            except Exception:
                log.exception("Unable to release Raspberry Pi button GPIOs")
        self.is_connected = False

    def service(self) -> None:
        if not self.is_connected or self.buttons is None:
            return

        # Encoder edge callbacks run in RPi.GPIO's event thread. Drain those
        # detents first so a subsequent encoder-switch press in this service
        # cycle acts on the option/intensity value the operator just selected.
        with self._encoder_lock:
            encoder_events = self._encoder_events
            self._encoder_events = []
        for direction in encoder_events:
            self._invoke("encoder_rotate", direction, event=f"encoder={direction:+d}")

        self.buttons.service()

    def _encoder_callback(self, direction) -> None:
        # Called by RPi.GPIO's event thread. Queue the detent; all Qt/core work
        # is performed later by service() on the main thread.
        try:
            direction = int(direction)
        except (TypeError, ValueError):
            return
        if direction == 0:
            return
        now = self._clock()
        with self._encoder_lock:
            if now - self._last_encoder_event_at < self._encoder_debounce_s:
                return
            self._last_encoder_event_at = now
            self._encoder_events.append(1 if direction > 0 else -1)

    def _button_callback(self, event: str) -> None:
        if event == "load_normal":
            self._invoke("payload", 1, event="load=NORMAL")
        elif event == "load_full":
            self._invoke("payload", 2, event="load=FULL")
        else:
            self._invoke(event, event=event)

    def _apply_load_level(self, pin_value: int) -> None:
        if int(pin_value):
            self._invoke("payload", 1, event="load=NORMAL")
        else:
            self._invoke("payload", 2, event="load=FULL")

    @property
    def activation_switch_debug(self) -> str:
        """Return the maintained Activate selector position and raw GPIO level."""
        if not self.is_connected or self.buttons is None:
            return "unavailable"
        try:
            level = self.buttons.value(self.pins["ACTIVATE_PIN"])
        except Exception as exc:
            self.last_error = str(exc)
            return f"error: {exc}"
        state = "ACTIVATED" if level == 0 else "DEACTIVATED"
        return f"{state} (GPIO24={level})"

    @property
    def activation_switch_level(self) -> int | None:
        """Return the current raw selector level, or None when unavailable."""
        if not self.is_connected or self.buttons is None:
            return None
        try:
            return self.buttons.value(self.pins["ACTIVATE_PIN"])
        except Exception as exc:
            self.last_error = str(exc)
            return None

    def _invoke(self, action_name: str, *args, event: str | None = None) -> None:
        callback = self.actions.get(action_name)
        if callback is None:
            log.warning("No POC action registered for Raspberry Pi control %s", action_name)
            return
        self.last_event = event or action_name
        try:
            callback(*args)
        except Exception as exc:
            self.last_error = str(exc)
            log.exception("Raspberry Pi control action %s failed", action_name)
