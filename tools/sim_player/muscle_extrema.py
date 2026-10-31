# muscle_extrema.py ver 2
"""Qt-independent muscle-extreme history for sim_player research replay.

Track *requested* lengths, not the workspace-limited final commands: only
requested lengths expose out-of-range demands. Both minima and maxima are
monotone for the current run; a user-initiated replay reset clears history.
The plot scale is established from the entire *analysed* capture so an
out-of-range marker never needs to be clamped to the working-range rail.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math

N_MUSCLES = 6


def requested_lengths(frame):
    """Return finite six-muscle requests; use final lengths for old captures."""
    values = frame.analysis.get("requested", ())
    if len(values) != N_MUSCLES:
        values = frame.muscle_lengths_mm
    if len(values) != N_MUSCLES:
        return None
    try:
        numbers = tuple(float(x) for x in values)
    except (ValueError, TypeError):
        return None
    return numbers if all(math.isfinite(x) for x in numbers) else None


def plot_bounds(recording):
    """Stable per-experiment ruler containing limits and all requested data.

    Headroom makes a real over-limit marker visible outside the legal rail.
    The ruler is NOT resized as extrema arrive: extrema never appear to
    recede merely because the display scale changed during playback.
    """
    lo, hi = float(recording.min_length_mm), float(recording.max_length_mm)
    if not (math.isfinite(lo) and math.isfinite(hi) and hi > lo):
        raise ValueError("Invalid muscle working range")
    seen_lo, seen_hi = lo, hi
    for frame in recording.frames:
        values = requested_lengths(frame)
        if values:
            seen_lo = min(seen_lo, *values)
            seen_hi = max(seen_hi, *values)
    padding = max(8.0, (hi - lo) * 0.05)
    return seen_lo - padding, seen_hi + padding


@dataclass(frozen=True)
class RelativeMuscleRuler:
    """Stable length plot with common valid-limit positions on every platform.

    Valid limits occupy the central 76% of the panel, independent of whether
    a platform uses 600..800 mm or 750..1000 mm muscles. The 12% gutters
    compress any outside-range requests into visible warning areas; their
    extents are set once per recording by plot_bounds(), not during playback.
    The numeric warning/tooltip retains the actual millimetre excess.
    """

    lo: float
    hi: float
    data_lo: float
    data_hi: float
    margin: float = 0.12

    def __post_init__(self):
        if not (all(math.isfinite(float(v)) for v in
                    (self.lo, self.hi, self.data_lo, self.data_hi, self.margin))
                and self.hi > self.lo and 0.0 < self.margin < 0.5):
            raise ValueError('Invalid muscle-length ruler')

    def position(self, length_mm):
        """Horizontal fraction in [0,1]; working limits never move."""
        mm = float(length_mm)
        if not math.isfinite(mm):
            raise ValueError('Invalid plotted muscle length')
        if mm < self.lo:
            outside = max(self.lo - self.data_lo, (self.hi - self.lo) * 0.05)
            return self.margin * (1.0 - min(1.0, (self.lo - mm) / outside))
        if mm > self.hi:
            outside = max(self.data_hi - self.hi, (self.hi - self.lo) * 0.05)
            return 1.0 - self.margin + self.margin * min(1.0, (mm - self.hi) / outside)
        return self.margin + (1.0 - 2.0 * self.margin) * (
            (mm - self.lo) / (self.hi - self.lo))


@dataclass
class MuscleExtrema:
    """Monotonic per-muscle requested minima/maxima for one playback run."""

    minimum: list[float | None] = field(default_factory=lambda: [None] * N_MUSCLES)
    maximum: list[float | None] = field(default_factory=lambda: [None] * N_MUSCLES)
    last_index: int | None = None
    frames_seen: int = 0

    def clear(self):
        self.minimum[:] = [None] * N_MUSCLES
        self.maximum[:] = [None] * N_MUSCLES
        self.last_index = None
        self.frames_seen = 0

    @property
    def visible(self):
        """Blank markers immediately after reset; show after movement."""
        return self.frames_seen > 1

    def record(self, values):
        if values is None or len(values) != N_MUSCLES:
            return
        try:
            numbers = tuple(float(v) for v in values)
        except (ValueError, TypeError):
            return
        if not all(math.isfinite(v) for v in numbers):
            return
        for i, v in enumerate(numbers):
            self.minimum[i] = v if self.minimum[i] is None else min(self.minimum[i], v)
            self.maximum[i] = v if self.maximum[i] is None else max(self.maximum[i], v)
        self.frames_seen += 1

    def advance(self, frames, index):
        """Include skipped forward samples, but never retract on a backward seek.

        An automatic A/B loop returning to its start retains extrema. A new
        recording, changed cue/gains, or explicit replay restart calls clear().
        """
        if not frames:
            return
        index = max(0, min(len(frames) - 1, int(index)))
        if self.last_index is None:
            start = index
        elif index > self.last_index:
            start = self.last_index + 1
        elif index < self.last_index:
            start = index
        else:
            return
        for i in range(start, index + 1):
            self.record(requested_lengths(frames[i]))
        self.last_index = index

    def extrema(self):
        return tuple(zip(self.minimum, self.maximum)) if self.visible else None
