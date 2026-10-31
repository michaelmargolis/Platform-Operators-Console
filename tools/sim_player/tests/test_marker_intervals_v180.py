# test_marker_intervals_v180.py ver 1
"""Pure headless kinematic duration-bar geometry and hit testing."""
from types import SimpleNamespace
import pytest
from tools.sim_player.marker_intervals import event_span, event_at_x, timeline_x


def ev(start, end, peak=None):
    return SimpleNamespace(start_s=start, end_s=end,
                           seconds=(start if peak is None else peak))


def test_bar_follows_full_duration_not_peak_timestamp():
    event = ev(10, 40, 32)
    left, right = event_span(event, 1036, 100)
    assert left == pytest.approx(118)
    assert right == pytest.approx(418)
    assert event_at_x([event], 120, 1036, 100) is event
    assert event_at_x([event], 416, 1036, 100) is event
    assert event_at_x([event], 520, 1036, 100) is None


def test_short_and_single_frame_events_remain_visible():
    for event in (ev(12, 12), ev(12, 12.00001)):
        left, right = event_span(event, 1036, 100)
        assert right - left == pytest.approx(5.0)
        assert event_at_x([event], (left+right)/2, 1036, 100) is event


def test_intervals_are_clamped_and_minimum_stubs_remain_within_rail():
    for event in (ev(0, 0), ev(100, 100), ev(-5, 105)):
        left, right = event_span(event, 1036, 100)
        assert 18 <= left < right <= 1018


def test_shorter_overlapping_bar_takes_precedence():
    long = ev(10, 50, 30)
    short = ev(30, 32, 31)
    x=timeline_x(31,1036,100)
    assert event_at_x((long, short),x,1036,100) is short
    assert event_at_x((short, long),x,1036,100) is short
    assert event_at_x((long, short),timeline_x(20,1036,100),1036,100) is long


def test_invalid_time_interval_is_rejected():
    with pytest.raises(ValueError,match='Invalid'):
        event_span(ev(4,3),1036,100)
