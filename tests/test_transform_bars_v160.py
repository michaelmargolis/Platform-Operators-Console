# test_transform_bars_v160.py ver 1
"""Regression checks for display-only heave sign and Debug-tab bypass."""

from ui import main_window


def test_heave_is_inverted_only_for_display():
    original = (0.2, -0.3, 0.4, -0.5, 0.6, -0.7)
    result = main_window.transform_bar_values(original)
    assert result == (0.2, -0.3, -0.4, -0.5, 0.6, -0.7)
    assert original[2] == 0.4  # Input/telemetry remains unchanged.


def test_debug_tab_updates_off_by_default():
    assert main_window.UPDATE_TRANSFORM_BARS_ON_DEBUG_TAB is False


def test_sample_callback_switch_keeps_other_work():
    """Exercise the real callback with a minimal stand-in window."""
    from types import SimpleNamespace

    class TabWidget:
        def __init__(self, active):
            self.active = active

        def currentWidget(self):
            return self.active

    class Bars:
        def __init__(self):
            self.calls = []

        def set_values(self, values):
            self.calls.append(values)

    debug = object()
    other = object()
    obj = SimpleNamespace(
        tab_debug=debug, tab_output=object(), tab_transform=object(),
        tabWidget=TabWidget(debug), transform_bars=Bars(),
        _capture_csv_sample=lambda sample: None,
        _update_debug=lambda: None,
    )
    sample = SimpleNamespace(transform=(0, 0, 0.5, 0, 0, 0))
    main_window.MainWindow._on_sample(obj, sample)
    assert obj.transform_bars.calls == []
    obj.tabWidget.active = other
    main_window.MainWindow._on_sample(obj, sample)
    assert obj.transform_bars.calls == [(0, 0, -0.5, 0, 0, 0)]
