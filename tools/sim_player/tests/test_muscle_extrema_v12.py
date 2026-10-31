# test_muscle_extrema_v12.py ver 1
"""Qt-free regression tests for v12 requested-muscle peak markers."""
from types import SimpleNamespace

from tools.sim_player.muscle_extrema import MuscleExtrema, plot_bounds, requested_lengths


def frame(lengths, *, final=None):
    return SimpleNamespace(analysis={"requested": lengths},
                           muscle_lengths_mm=tuple(final or lengths))


def ride(frames):
    return SimpleNamespace(frames=tuple(frames), min_length_mm=750,
                           max_length_mm=1000)


def test_track_both_extrema_per_muscle_and_never_retract_after_backward_seek():
    frames = [frame((800, 810, 820, 830, 840, 850)),
              frame((780, 790, 800, 810, 820, 830)),
              frame((740, 1015, 830, 820, 830, 840)),
              frame((810, 800, 810, 815, 850, 820))]
    peaks = MuscleExtrema()
    peaks.advance(frames, 0)
    assert peaks.extrema() is None  # Cleared markers initially.
    peaks.advance(frames, 1)
    assert peaks.extrema()[0] == (780., 800.)
    peaks.advance(frames, 3)
    assert peaks.extrema()[0] == (740., 810.)
    assert peaks.extrema()[1] == (790., 1015.)
    peaks.advance(frames, 1)
    assert peaks.extrema()[0] == (740., 810.)
    assert peaks.extrema()[1] == (790., 1015.)
    assert peaks.last_index == 1
    peaks.advance(frames, 3)  # Replayed section cannot retract peaks.
    assert peaks.extrema()[0] == (740., 810.)
    peaks.clear()
    assert peaks.extrema() is None
    assert peaks.last_index is None


def test_forward_seek_includes_skipped_frames():
    frames = [frame((800,) * 6), frame((700,) * 6), frame((800,) * 6)]
    peaks = MuscleExtrema()
    peaks.advance(frames, 0)
    peaks.advance(frames, 2)
    assert peaks.extrema()[0] == (700., 800.)


def test_fixed_ruler_includes_both_outside_boundaries_without_rescaling():
    frames = [frame((800,) * 6), frame((734, 1055, 800, 800, 800, 800))]
    limits = plot_bounds(ride(frames))
    assert limits[0] < 734 and limits[1] > 1055
    history = MuscleExtrema()
    for i in range(2):
        history.advance(frames, i)
    assert limits == plot_bounds(ride(frames))  # Independent of history position.
    assert history.extrema()[0] == (734., 800.)
    assert history.extrema()[1] == (800., 1055.)


def test_requested_not_permitted_is_source_of_marker():
    source = frame((725, 1017, 805, 805, 805, 805), final=(750, 1000, 805, 805, 805, 805))
    assert requested_lengths(source)[:2] == (725., 1017.)
    peaks = MuscleExtrema()
    peaks.record(requested_lengths(source))
    peaks.record((900,) * 6)
    assert peaks.extrema()[0] == (725., 900.)
    assert peaks.extrema()[1] == (900., 1017.)


def test_invalid_data_falls_back_to_final_or_is_ignored():
    good = SimpleNamespace(analysis={}, muscle_lengths_mm=(800,) * 6)
    bad = SimpleNamespace(analysis={"requested": (float("nan"),) * 6},
                          muscle_lengths_mm=(800,) * 6)
    assert requested_lengths(good) == (800,) * 6
    assert requested_lengths(bad) is None
    peaks = MuscleExtrema()
    peaks.record((800,) * 6)
    assert peaks.frames_seen == 1
    peaks.record((float("nan"),) * 6)
    assert peaks.frames_seen == 1
