#buttons.py  ver 1
"""Debounced Raspberry Pi GPIO button/switch polling helper."""
from __future__ import annotations

import time
import warnings

import RPi.GPIO as GPIO


class Buttons:
    def __init__(self, callback, bouncetime=60):
        GPIO.setmode(GPIO.BCM)
        self.callback = callback
        self.bouncetime = float(bouncetime) / 1000.0
        self.pins = []
        self.keys = []
        self.edges = []
        self.last_pin_val = []
        self.is_changing = []
        self.last_pin_change = []

    def append(self, pin, key, input_mode, edge):
        self.pins.append(pin)
        self.keys.append(key)
        self.edges.append(edge)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            if input_mode == "pulldown":
                GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
            elif input_mode == "pullup":
                GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            else:
                raise ValueError(f"Unsupported input mode: {input_mode}")
        self.last_pin_val.append(int(GPIO.input(pin)))
        self.last_pin_change.append(time.time())
        self.is_changing.append(False)

    def value(self, pin):
        """Return the current un-debounced GPIO level as 0 (low) or 1 (high)."""
        return int(GPIO.input(pin))

    def raw_value(self, pin):
        """Compatibility helper returning ``'low'`` or ``'high'``."""
        return "high" if self.value(pin) else "low"

    def service(self):
        for idx, pin in enumerate(self.pins):
            pinval = int(GPIO.input(pin))
            changed = (
                (pinval == 0 and self.last_pin_val[idx] == 1 and self.edges[idx] in ("falling", "both"))
                or (pinval == 1 and self.last_pin_val[idx] == 0 and self.edges[idx] in ("rising", "both"))
            )
            if changed:
                self.is_changing[idx] = True
                self.last_pin_change[idx] = time.time()
            self.last_pin_val[idx] = pinval

            if self.is_changing[idx] and time.time() - self.last_pin_change[idx] >= self.bouncetime:
                self.is_changing[idx] = False
                if self.edges[idx] == "both":
                    keys = self.keys[idx]
                    self.callback(keys[pinval])  # key[0]=low, key[1]=high
                else:
                    self.callback(self.keys[idx])

    def close(self):
        if self.pins:
            GPIO.cleanup(self.pins)
