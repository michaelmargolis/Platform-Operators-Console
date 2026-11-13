"""Shared, presentation-only snapshots for Inspector and Advanced panels.

Only ReplayWindow owns extrema history. Neither panel independently accumulates
peaks, recomputes kinematics, or changes the analysed recording.
"""
from __future__ import annotations
from dataclasses import dataclass
from .tuning import muscle_flags


@dataclass(frozen=True)
class MuscleBarState:
    values: tuple
    lo: float
    hi: float
    requested: tuple | None
    request_oob: tuple
    final_clipped: tuple
    extrema: tuple | None
    plot_bounds: tuple | None

    def apply(self, widget):
        widget.update_values(self.values, self.lo, self.hi,
            requested=self.requested, request_oob=self.request_oob,
            final_clipped=self.final_clipped, extrema=self.extrema,
            plot_bounds=self.plot_bounds)


def muscle_bar_state(recording, frame, *, extrema=None, bounds=None):
    """Use the pre-refactor flags, exact requests and controller-owned history."""
    requested = frame.analysis.get('requested', ())
    flags, clamps = muscle_flags(frame, recording.min_length_mm, recording.max_length_mm)
    return MuscleBarState(tuple(frame.muscle_lengths_mm), float(recording.min_length_mm),
        float(recording.max_length_mm), tuple(requested) if len(requested) == 6 else None,
        tuple(flags), tuple(clamps), tuple(extrema) if extrema is not None else None,
        tuple(bounds) if bounds is not None else None)


def transform_readout(recording, frame, show_source=False):
    dynamic = getattr(recording, 'dynamic_profile', None) is not None
    if dynamic:
        context = 'BASE POST-WASHOUT' if show_source else 'PROFILE REQUEST'
    else:
        context = 'RAW CSV' if show_source else 'PROCESSED'
    return context, tuple(frame.source_axes if show_source else frame.processed_axes)


def candidate_label(recording):
    if recording is None:
        return 'Not loaded'
    dynamic = getattr(recording, 'dynamic_profile', None)
    if isinstance(dynamic, dict):
        return str(dynamic.get('label') or recording.variant_label or 'Dynamic profile')
    return str(recording.variant_label or 'Static baseline')


def comparison_pair(active, other, active_slot='A'):
    """Stable A/B identity: selecting B must not swap the column meanings."""
    return (other, active) if active_slot == 'B' else (active, other)


def event_description(event):
    """The same duration, peak and details that were available in the timeline."""
    if event is None:
        return 'Select an event to see its details.'
    if hasattr(event, 'start_s') and hasattr(event, 'end_s'):
        duration = float(getattr(event, 'duration_s', event.end_s - event.start_s))
        timing = (f'{event.start_s:.2f}-{event.end_s:.2f} s ({duration:.2f} s)\n'
                  f'Peak at {event.seconds:.2f} s')
    else:
        timing = f'{event.seconds:.2f} s'
    return f'{event.name}\n{timing}\n{getattr(event, "details", "")}'.rstrip()
