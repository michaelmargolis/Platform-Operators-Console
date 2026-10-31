# loop_range.py ver 1
"""Session-only A/B playback interval and three playback modes.

All positions are telemetry seconds. This module has no Qt or POC dependencies.
Marker editing never seeks video; the UI/controller decides when to seek.
"""
from __future__ import annotations
from dataclasses import dataclass


MODES = ("full", "range", "loop")


@dataclass
class PlaybackRange:
    duration: float = 0.0
    start: float = 0.0
    end: float = 0.0
    mode: str = "full"
    loop_delay_s: float = 1.0

    def reset(self, duration: float | None = None) -> None:
        if duration is not None:
            self.duration = max(0.0, float(duration))
        self.start = 0.0
        self.end = self.duration
        self.mode = "full"
        # Preserve the user's in-session restart-delay selection.

    @property
    def minimum_span(self) -> float:
        # Prevent zero-duration loops; support very short recordings.
        return min(self.duration, 0.05)

    def set_mode(self, value: str) -> None:
        if value not in MODES:
            raise ValueError(f"Unknown playback mode: {value!r}")
        self.mode = value

    def set_delay(self, seconds: float) -> None:
        if not 0 <= float(seconds) <= 30:
            raise ValueError("Loop delay must be between 0 and 30 seconds")
        self.loop_delay_s = float(seconds)

    def set_start(self, seconds: float) -> None:
        if self.duration <= 0:
            return
        self.start = max(0.0, min(float(seconds), self.end - self.minimum_span))

    def set_end(self, seconds: float) -> None:
        if self.duration <= 0:
            return
        self.end = min(self.duration, max(float(seconds), self.start + self.minimum_span))

    def mark(self, position: float, seconds: float) -> None:
        """Centre A/B on position and clip to recording bounds; select loop."""
        if self.duration <= 0:
            return
        half = max(self.minimum_span, float(seconds)) / 2.0
        centre = min(self.duration, max(0.0, float(position)))
        self.start = max(0.0, centre - half)
        self.end = min(self.duration, centre + half)
        if self.end - self.start < self.minimum_span:
            if centre >= self.duration / 2.0:
                self.start = max(0.0, self.end - self.minimum_span)
            else:
                self.end = min(self.duration, self.start + self.minimum_span)
        self.mode = "loop"

    def select_event(self, start: float, end: float, context_s: float = 1.0) -> None:
        """Select an anomaly with context and enter loop mode, without seeking.

        The controller seeks to A and starts playback separately. An event at
        the edge of a capture clips the interval instead of shifting it.
        """
        if self.duration <= 0:
            return
        context = max(0.0, float(context_s))
        start = max(0.0, min(self.duration, float(start)))
        end = max(start, min(self.duration, float(end)))
        self.start = max(0.0, start - context)
        self.end = min(self.duration, end + context)
        if self.end - self.start < self.minimum_span:
            self.end = min(self.duration, self.start + self.minimum_span)
            self.start = max(0.0, self.end - self.minimum_span)
        self.mode = "loop"

    def play_from(self, current: float) -> float:
        """When using A/B, begin at A if the playhead is outside [A,B)."""
        if self.mode != "full" and not (self.start <= current < self.end):
            return self.start
        return current

    def boundary_reached(self, target: float) -> bool:
        """Report reaching B in either range-limited playback mode."""
        return self.mode != "full" and target >= self.end
