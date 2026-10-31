# test_muscle_scale_v179.py ver 1
"""Qt-free chart-scale checks for the 800 and 1000 mm platforms."""
import pytest

from tools.sim_player.muscle_extrema import RelativeMuscleRuler


def test_legal_min_max_and_percentages_align_across_platforms():
    chair = RelativeMuscleRuler(600, 800, 560, 850)
    mdx = RelativeMuscleRuler(750, 1000, 680, 1020)
    for fraction in (0.0, 0.1, 0.25, 0.5, 0.8, 1.0):
        a = chair.position(600 + 200 * fraction)
        b = mdx.position(750 + 250 * fraction)
        assert a == pytest.approx(b, abs=1e-12)
    assert chair.position(600) == pytest.approx(0.12)
    assert chair.position(800) == pytest.approx(0.88)


def test_valid_limits_stay_put_regardless_of_request_overflow():
    for plot_low, plot_high in ((590, 810), (350, 950), (580, 860)):
        ruler = RelativeMuscleRuler(600, 800, plot_low, plot_high)
        assert ruler.position(600) == pytest.approx(0.12)
        assert ruler.position(800) == pytest.approx(0.88)
        assert ruler.position(700) == pytest.approx(0.5)
        assert 0 <= ruler.position(500) < 0.12
        assert 0.88 < ruler.position(900) <= 1.0


def test_requested_extrema_show_on_correct_side_of_legal_limits():
    ruler = RelativeMuscleRuler(600, 800, 530, 930)
    assert 0 <= ruler.position(560) < ruler.position(600)
    assert ruler.position(800) < ruler.position(880) <= 1
    assert ruler.position(580) > ruler.position(560)
    assert ruler.position(850) < ruler.position(880)


def test_invalid_rulers_rejected():
    for values in ((800, 600, 560, 850), (600, 800, float('nan'), 900)):
        with pytest.raises(ValueError):
            RelativeMuscleRuler(*values)
    ruler = RelativeMuscleRuler(600, 800, 560, 850)
    with pytest.raises(ValueError):
        ruler.position(float('inf'))
