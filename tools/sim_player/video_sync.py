# video_sync.py ver 1
"""Qt-independent helpers for robust video/timeline synchronization.

Avoid repeated H.264 decoder seeks during normal play and after pause/resume.
Even with Qt6 / FFmpeg, unnecessary seek requests cost decoder work; normal
playback should run continuously, while explicit paused user seeks are allowed.
"""
from __future__ import annotations


def is_ride_telemetry_csv(filename: str) -> bool:
    """True for candidate high-rate capture CSVs, never state-event sidecars."""
    name = str(filename).casefold()
    return name.endswith(".csv") and not name.endswith("_state_events.csv")


class VideoSeekGuard:
    """Debounce position corrections without affecting explicit user seeks."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.last_seek_at = float("-inf")
        self.resume_guard_until = float("-inf")
        self.drift_started_at = None

    def user_seek(self, now: float):
        self.last_seek_at = float(now)
        self.drift_started_at = None

    def resumed(self, now: float):
        # Allow the video decoder to resume before evaluating its position.
        self.resume_guard_until = float(now) + 1.2
        self.drift_started_at = None

    def should_seek_on_resume(self, drift_ms: int, now: float) -> bool:
        # Do not issue a second seek immediately after an explicit timeline
        # jump: Qt may not have reported its new position yet.
        if now - self.last_seek_at < .75:
            return False
        # Never seek to correct normal pause/decoder timestamp jitter. Only a
        # gross mismatch (e.g. initial nonzero OBS offset) needs a seek.
        return abs(drift_ms) >= 1000

    def should_correct_drift(self, now: float, drift_ms: int) -> bool:
        if (abs(drift_ms) <= 275 or now < self.resume_guard_until or
                now - self.last_seek_at < 2.5):
            self.drift_started_at = None
            return False
        if self.drift_started_at is None:
            self.drift_started_at = float(now)
            return False
        if now - self.drift_started_at < .75:
            return False
        self.user_seek(now)
        return True
