#test_ui_layout.py  ver 11
from pathlib import Path
from types import SimpleNamespace
import xml.etree.ElementTree as ET

from core.types import ExperienceState, PlatformState, SimStatus
from ui.main_window import MainWindow, readiness_indicator_key
from ui.ui_widgets import configure_touch_combo_popup
from PyQt5 import QtWidgets


ROOT = Path(__file__).resolve().parents[1]


def _widget(root, name):
    return root.find(f".//widget[@name='{name}']")


def _font(widget):
    return widget.find("./property[@name='font']/font")


def test_main_ui_operator_fonts_and_sim_data_caption():
    root = ET.parse(ROOT / "ui" / "poc_main.ui").getroot()
    expected_sizes = {
        "lbl_status_message": 22,
        "lbl_intensity_value": 20,
        "btn_run": 16,
        "btn_pause": 16,
        "btn_activate": 14,
        "labelPc": 14,
        "labelConnection": 14,
        "labelData": 14,
        "labelReady": 14,
    }

    for name, point_size in expected_sizes.items():
        font = _font(_widget(root, name))
        assert int(font.findtext("pointsize")) == point_size
        assert font.findtext("bold") == "true"

    sim_data = _widget(root, "labelData")
    assert sim_data.findtext("./property[@name='text']/string") == "Sim\nData"
    assert sim_data.findtext("./property[@name='alignment']/set") == "Qt::AlignCenter"

    ready = _widget(root, "labelReady")
    assert ready.findtext("./property[@name='text']/string") == "Ready"
    assert _widget(root, "lbl_ready_indicator") is not None


def test_compact_ui_has_ready_status_indicator():
    root = ET.parse(ROOT / "ui" / "poc_main_800x480.ui").getroot()
    assert _widget(root, "lbl_ready_indicator") is not None
    ready = _widget(root, "labelReady")
    assert ready.findtext("./property[@name='text']/string") == "Ready"


def test_compact_tabs_keep_operator_pages_but_hide_transform_viewer():
    source = (ROOT / "ui" / "main_window.py").read_text(encoding="utf-8")
    start = source.index("def _enforce_compact_tabs")
    end = source.index("def _setup_debug_controls", start)
    method = source[start:end]

    for tab_name in (
        "self.tab_ride_control",
        "self.tab_input",
        "self.tab_output",
        "self.tab_platform_selection",
        "self.tab_debug",
    ):
        assert tab_name in method
    assert "self.tab_transform" not in method
    assert "min-width: 0px" in method


def test_debug_text_size_matches_layout():
    for ui_name, expected_size in (
        ("poc_main.ui", 16),
        ("poc_main_800x480.ui", 12),
    ):
        root = ET.parse(ROOT / "ui" / ui_name).getroot()
        font = _font(_widget(root, "txt_debug_state"))
        assert int(font.findtext("pointsize")) == expected_size


def test_debug_controls_own_activation_mode_and_exit():
    source = (ROOT / "ui" / "main_window.py").read_text(encoding="utf-8")
    assert 'QGroupBox("Controls", self.tab_debug)' not in source
    assert 'QRadioButton("Switch", activation_group)' not in source
    assert 'QRadioButton("Touch UI", activation_group)' not in source
    assert 'QPushButton("Exit", controls)' not in source
    assert "tab_exit" not in source

    for ui_name in ("poc_main.ui", "poc_main_800x480.ui"):
        root = ET.parse(ROOT / "ui" / ui_name).getroot()
        controls = _widget(root, "grp_debug_controls")
        assert controls is not None
        assert int(controls.findtext(
            "./property[@name='minimumSize']/size/width"
        )) == 140

        activation_layout = root.find(
            ".//layout[@name='activationControlLayout']"
        )
        assert int(activation_layout.findtext(
            "./property[@name='spacing']/number"
        )) == 16

        for name, label in (
            ("rb_activation_switch", "Switch"),
            ("rb_activation_touch", "Touch UI"),
        ):
            radio = _widget(root, name)
            assert radio.findtext("./property[@name='text']/string") == label
            assert int(radio.findtext(
                "./property[@name='minimumSize']/size/height"
            )) == 70
            assert int(_font(radio).findtext("pointsize")) == 12

        exit_button = _widget(root, "btn_exit_desktop")
        assert exit_button.findtext("./property[@name='text']/string") == "Exit"
        assert int(_font(exit_button).findtext("pointsize")) == 12


def test_ready_indicator_priority_and_states():
    no_data = SimStatus(data_connected=False, ready_to_start=True)
    assert readiness_indicator_key(
        PlatformState.ACTIVATED, ExperienceState.RUNNING, no_data
    ) == "nogo"

    data_not_ready = SimStatus(data_connected=True, ready_to_start=False)
    assert readiness_indicator_key(
        PlatformState.ACTIVATED, ExperienceState.READY, data_not_ready
    ) == "warning"

    data_ready = SimStatus(data_connected=True, ready_to_start=True)
    assert readiness_indicator_key(
        PlatformState.DEACTIVATED, ExperienceState.READY, data_ready
    ) == "warning"
    assert readiness_indicator_key(
        PlatformState.ACTIVATED, ExperienceState.READY, data_ready
    ) == "ok"
    assert readiness_indicator_key(
        PlatformState.ACTIVATED, ExperienceState.RUNNING, data_ready
    ) == "running"
    assert readiness_indicator_key(
        PlatformState.ACTIVATED, ExperienceState.PAUSED, data_ready
    ) == "paused"


def test_application_close_is_permitted_only_when_fully_deactivated():
    window = SimpleNamespace(
        core=SimpleNamespace(platform=SimpleNamespace(state=PlatformState.DEACTIVATED, parked=True))
    )
    assert MainWindow._platform_safe_to_close(window)

    window.core.platform.state = PlatformState.ACTIVATED
    assert not MainWindow._platform_safe_to_close(window)

    window.core.platform.state = PlatformState.DEACTIVATING
    assert not MainWindow._platform_safe_to_close(window)


def test_front_and_side_translation_reaches_each_label_edge():
    label = type("Label", (), {
        "width": lambda self: 300,
        "height": lambda self: 260,
    })()
    pixmap = type("Pixmap", (), {
        "width": lambda self: 170,
        "height": lambda self: 220,
    })()

    assert MainWindow._edge_scaled_translation(label, pixmap, 1.0, 1.0) == (65, 20)
    assert MainWindow._edge_scaled_translation(label, pixmap, -1.0, -1.0) == (-65, -20)
    assert MainWindow._edge_scaled_translation(label, pixmap, 2.0, -2.0) == (65, -20)


def test_simulator_touch_popup_doubles_rows_without_resizing_closed_combo():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    combo = QtWidgets.QComboBox()
    combo.setMinimumSize(220, 34)
    combo.resize(220, 34)
    combo.addItems(["NoLimits2 Coaster", "SpaceCoaster"])
    closed_size = combo.size()
    closed_font_size = combo.font().pointSizeF()

    delegate = configure_touch_combo_popup(combo)

    assert combo.size() == closed_size
    assert delegate.row_height >= closed_size.height() * 2
    assert combo.view().font().pointSizeF() >= closed_font_size * 2
    assert combo.view().minimumWidth() >= closed_size.width()
    assert app is not None
