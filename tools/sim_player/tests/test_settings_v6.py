# test_settings_v6.py ver 1
"""Settings validation/persistence and UI separation regression tests (no Qt)."""
from __future__ import annotations
from pathlib import Path
import ast
import pytest

from tools.sim_player.settings import (
    VisualizerSettings, load_visualizer_settings, save_visualizer_settings,
)


class FakeSettings:
    def __init__(self):
        self.values = {}

    def value(self, key, default=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value


def test_defaults_safe_and_no_video_aspect_option():
    store = FakeSettings()
    assert load_visualizer_settings(store) == VisualizerSettings()
    root = Path(__file__).resolve().parents[1]
    options = (root / "options_dialog.py").read_text()
    assert '"IP address:"' in options
    assert '"UDP port:"' in options
    assert "aspect_box" not in options
    ui = (root / "player_ui.py").read_text()
    assert "Qt.AspectRatioMode.KeepAspectRatio" in ui


def test_saved_settings_reload_and_do_not_touch_poc_config():
    store = FakeSettings()
    setting = VisualizerSettings(True, "192.168.0.3", 10021)
    save_visualizer_settings(store, setting)
    assert load_visualizer_settings(store) == setting
    assert set(store.values) == {
        "visualizer/enabled", "visualizer/ip_address", "visualizer/port"
    }
    # QSettings may return "false" as a string; don't interpret it as true.
    store.values["visualizer/enabled"] = "false"
    assert not load_visualizer_settings(store).enabled


@pytest.mark.parametrize("address,port", [
    ("no-such-host", 10020), ("::1", 10020), ("127.0.0.1", 0),
    ("127.0.0.1", 65536), ("127.0.0.1", "not-a-number"),
])
def test_bad_destinations_rejected(address, port):
    with pytest.raises(ValueError):
        VisualizerSettings(True, address, port).validate()


def test_invalid_persistent_settings_fall_back_to_disabled():
    store = FakeSettings()
    store.values.update({
        "visualizer/enabled": "true", "visualizer/ip_address": "invalid",
        "visualizer/port": 20020,
    })
    assert load_visualizer_settings(store) == VisualizerSettings()


def test_separation_of_presentation_and_motion_calculation():
    root = Path(__file__).resolve().parents[1]
    for name in ("player_ui.py", "panels.py", "options_dialog.py", "theme.py"):
        contents = (root / name).read_text()
        assert "analyze_nl2_csv" not in contents
        assert "DigitalTwinSender" not in contents
        ast.parse(contents)
    app = (root / "app.py").read_text()
    assert "def paintEvent(" not in app
    assert "class ResponsiveBars" not in app
    assert "class ReplayWindow(PlayerUiMixin, QMainWindow)" in app
    assert "Settings…" in (root / "player_ui.py").read_text()
