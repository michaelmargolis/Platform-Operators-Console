# twin_output.py ver 1
"""Optional headless replay -> MDX Platform Twin output.

Uses POC's existing DigitalTwinSender and its versioned JSON/UDP protocol,
never duplicates or changes the packet schema. No Festo, SHS or Qt imports.
UDP is best effort: a disconnected Twin cannot interrupt offline replay.
"""
from __future__ import annotations

import time
from .settings import VisualizerSettings


class ReplayTwinOutput:
    """Send the current applied replay frame at no more than 20 Hz.

    An explicit seek or opening a recording transmits the selected frame
    immediately. During playback, repeated 30 Hz UI ticks for the same 20 Hz
    sample do not cause duplicate packets. Paused playback holds its pose.
    """

    MIN_SEND_INTERVAL_S = 0.05

    def __init__(self, preferences: VisualizerSettings, *, clock=time.monotonic, sender_factory=None):
        self._clock = clock
        if sender_factory is None:
            from core.digital_twin import DigitalTwinSender
            sender_factory = DigitalTwinSender
        self._sender_factory = sender_factory
        self._sender = None
        self._preferences = VisualizerSettings()
        self._last_index = None
        self._last_sent_at = -float("inf")
        self.configure(preferences)

    @property
    def enabled(self) -> bool:
        return self._sender is not None and self._sender.enabled

    @property
    def last_error(self) -> str:
        if self._sender is None:
            return ""
        return str(self._sender.last_error)

    def configure(self, preferences: VisualizerSettings) -> bool:
        preferences = preferences.validate()
        self.close()
        self._preferences = preferences
        if not preferences.enabled:
            return False
        self._sender = self._sender_factory(address=preferences.ip_address, port=preferences.port)
        return self.enabled

    def close(self) -> None:
        if self._sender is not None:
            self._sender.close()
        self._sender = None
        self._last_index = None
        self._last_sent_at = -float("inf")

    def send_frame(self, recording, index: int, *, state: str = "PAUSED", force: bool = False) -> bool:
        if not self.enabled or recording is None or not recording.frames:
            return False
        index = max(0, min(len(recording.frames) - 1, int(index)))
        now = self._clock()
        if not force:
            if index == self._last_index:
                return False
            if now - self._last_sent_at < self.MIN_SEND_INTERVAL_S:
                return False
        frame = recording.frames[index]
        maximum = float(recording.max_length_mm)
        minimum = float(recording.min_length_mm)
        length_range = max(1.0, maximum - minimum)
        contraction_pct = tuple(
            max(0.0, min(100.0, 100.0 * (maximum - float(length)) / length_range))
            for length in frame.muscle_lengths_mm
        )
        applied = frame.analysis.get("applied_real")
        if applied is None:
            # Never pretend that a *requested* pose is the workspace-limited
            # pose. The Twin can still render the lengths without Pose Diff.
            applied = None
        sent = self._sender.send_snapshot(
            platform=getattr(recording, "platform_key", "platform_v2"),
            platform_state="ACTIVE",
            parking_state="UNPARKED",
            simulator_state=str(state),
            telemetry_valid=True,
            transform_applied=True,
            dtop_load_kg_per_muscle=0.0,  # not measured in offline capture
            transform=frame.processed_axes,
            muscle_length_mm=frame.muscle_lengths_mm,
            contraction_pct=contraction_pct,
            command_pressure_mbar=(0.0,) * 6,
            piston_disengaged=None,
            applied_transform_real=applied,
        )
        if sent:
            self._last_index = index
            self._last_sent_at = now
        return bool(sent)
