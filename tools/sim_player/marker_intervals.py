# marker_intervals.py ver 1
"""Pixel spans and hit testing for kinematic anomaly intervals (Qt independent).

A kinematic event spans first to last anomalous recorded sample. Single-frame
and subpixel events are painted with a minimal visible bar, but their true
start/end timestamps and duration are not altered.
"""
from __future__ import annotations

import math


def timeline_x(seconds, width, duration, margin=18):
    """Map a telemetry timestamp onto the standard timeline rail."""
    duration = max(0.01, float(duration))
    fraction = max(0.0, min(1.0, float(seconds) / duration))
    return margin + max(0.0, float(width) - 2 * margin) * fraction


def event_span(event, width, duration, *, minimum_pixels=5.0, margin=18):
    """Return the visible (left,right) range of an event in timeline pixels.

    The requested visual minimum only aids selection of very short intervals;
    it does not modify the underlying start/end time or grouping.
    """
    start = float(event.start_s)
    end = float(event.end_s)
    if not (math.isfinite(start) and math.isfinite(end)) or end < start:
        raise ValueError('Invalid kinematic event interval')
    start_px = timeline_x(start, width, duration, margin)
    end_px = timeline_x(end, width, duration, margin)
    if end_px - start_px < minimum_pixels:
        centre = (start_px + end_px) / 2
        left_limit = margin
        right_limit = max(margin, float(width) - margin)
        left = max(left_limit, centre - minimum_pixels / 2)
        right = min(right_limit, left + minimum_pixels)
        left = max(left_limit, right - minimum_pixels)
        return left, right
    return start_px, end_px


def event_at_x(events, x, width, duration, *, tolerance=3.0):
    """Find a bar containing x, preferring the shortest when bars overlap.

    No remote events are selected just because their peak happens to be close.
    A kinematic event can only be clicked or hovered over its visible interval.
    """
    matches = []
    for event in events:
        left, right = event_span(event, width, duration)
        if left - tolerance <= x <= right + tolerance:
            matches.append((right-left, abs(timeline_x(event.seconds, width, duration)-x), event))
    return min(matches, key=lambda item: (item[0], item[1]))[2] if matches else None
