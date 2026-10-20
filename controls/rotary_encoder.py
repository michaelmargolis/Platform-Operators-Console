#rotary_encoder.py  ver 1
"""Mechanical rotary encoder helper for Raspberry Pi GPIO."""
from __future__ import annotations

import warnings

import RPi.GPIO as GPIO


class decoder:
    """Decode mechanical rotary encoder pulses into +1/-1 callbacks."""

    def __init__(self, gpioA, gpioB, callback):
        GPIO.setmode(GPIO.BCM)
        self.gpioA = gpioA
        self.gpioB = gpioB
        self.callback = callback
        self.lastChannel = None

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            GPIO.setup(gpioA, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            GPIO.setup(gpioB, GPIO.IN, pull_up_down=GPIO.PUD_UP)

        self.levA = int(GPIO.input(gpioA))
        self.levB = int(GPIO.input(gpioB))
        GPIO.add_event_detect(gpioA, GPIO.BOTH, callback=self._pulse)
        GPIO.add_event_detect(gpioB, GPIO.BOTH, callback=self._pulse)

    def _pulse(self, channel):
        if channel == self.gpioA:
            level = self.levA = int(GPIO.input(channel))
        else:
            level = self.levB = int(GPIO.input(channel))

        if channel != self.lastChannel:
            self.lastChannel = channel

        if channel == self.gpioA and level == 1:
            if self.levB == 1:
                self.callback(1)
        elif channel == self.gpioB and level == 1:
            if self.levA == 1:
                self.callback(-1)

    def cancel(self):
        GPIO.remove_event_detect(self.gpioA)
        GPIO.remove_event_detect(self.gpioB)
        GPIO.cleanup((self.gpioA, self.gpioB))
