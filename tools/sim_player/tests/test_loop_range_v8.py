# test_loop_range_v8.py ver 1
"""Pure A/B range boundary tests retained and extended for v9 modes."""
from tools.sim_player.loop_range import PlaybackRange


def test_initial_full_range_and_reset():
    r = PlaybackRange()
    r.reset(88.16)
    assert (r.start, r.end, r.mode) == (0.0, 88.16, "full")
    r.mark(42.0, 2.0)
    r.reset()
    assert (r.start, r.end, r.mode) == (0.0, 88.16, "full")
    r.reset(10.0)
    assert r.end == 10.0


def test_mark_centered_and_selects_loop():
    r = PlaybackRange()
    r.reset(88.16)
    r.mark(42.59, 2.0)
    assert abs(r.start - 41.59) < 1e-9
    assert abs(r.end - 43.59) < 1e-9
    assert r.mode == "loop"


def test_mark_clips_at_start_and_finish_no_shift():
    r = PlaybackRange()
    r.reset(10.0)
    r.mark(0.2, 2.0)
    assert (r.start, r.end) == (0.0, 1.2)
    r.mark(9.8, 2.0)
    assert (r.start, r.end) == (8.8, 10.0)
    r.mark(10.0, 20.0)
    assert (r.start, r.end) == (0.0, 10.0)


def test_marker_drag_clamps_and_never_crosses():
    r = PlaybackRange()
    r.reset(10)
    r.mark(5, 2)
    assert (r.start, r.end) == (4.0, 6.0)
    r.set_start(10)
    assert r.start == r.end - r.minimum_span
    r.set_end(-10)
    assert abs((r.end - r.start) - r.minimum_span) < 1e-9
    r.set_start(-10)
    r.set_end(100)
    assert (r.start, r.end) == (0.0, 10.0)


def test_both_range_modes_start_from_a_if_outside():
    r = PlaybackRange()
    r.reset(20)
    r.mark(10, 2)
    for mode in ("range", "loop"):
        r.set_mode(mode)
        assert r.play_from(5) == 9
        assert r.play_from(9) == 9
        assert r.play_from(10) == 10
        assert r.play_from(11) == 9
    r.set_mode("full")
    assert r.play_from(11) == 11


def test_boundary_check_for_each_mode():
    r = PlaybackRange()
    r.reset(20)
    r.mark(10, 2)
    for mode in ("range", "loop"):
        r.set_mode(mode)
        assert not r.boundary_reached(10)
        assert r.boundary_reached(11)
        assert r.boundary_reached(11.03)
    r.set_mode("full")
    assert not r.boundary_reached(11.03)
    assert not r.boundary_reached(20)


def test_full_ride_default_and_short_recording():
    r = PlaybackRange()
    r.reset(100)
    assert r.mode == "full"
    assert not r.boundary_reached(100)
    r.reset(0)
    r.mark(0, 2)
    assert r.mode == "full"
    r.reset(.02)
    r.mark(.01, 2)
    assert (r.start, r.end) == (0, .02)
    assert r.boundary_reached(.021)


def test_default_delay_validations_and_reset_preserves_delay():
    r = PlaybackRange()
    assert r.loop_delay_s == 1.0
    r.set_delay(0.5)
    r.reset(100)
    assert r.loop_delay_s == .5
    for delay in (0, 1, 2):
        r.set_delay(delay)
        assert r.loop_delay_s == delay


def test_reject_invalid_modes_and_delays():
    import pytest
    r = PlaybackRange()
    with pytest.raises(ValueError):
        r.set_mode("unknown")
    for invalid in (-1, 31):
        with pytest.raises(ValueError):
            r.set_delay(invalid)
