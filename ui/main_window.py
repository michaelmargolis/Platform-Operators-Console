#main_window.py  ver 70
from __future__ import annotations

import csv
from datetime import datetime
import logging
import os
from pathlib import Path
import platform
import time

from PyQt5 import QtCore, QtGui, QtWidgets, uic

from config.platforms import PLATFORMS
from core.sim_registry import OPERATOR_SIMS
from core.types import ActivationControlMode, ExperienceState, PlatformState, PlatformMotionState
from ui.sim_tabs import SimTabManager
from ui.ui_widgets import (
    ActivationButton,
    TransformBarsWidget,
    VerticalTextButton,
    configure_touch_combo_popup,
)


XLATE_SCALE = 20
SIM_VIEW_TRAVEL_MARGIN = 20
ROTATE_SCALE = 10
SIM_GUIDE_WIDTH = 3
SIM_GUIDE_BACKGROUND_COLOR = QtGui.QColor(190, 190, 190)
SIM_GUIDE_IMAGE_COLOR = QtGui.QColor(220, 0, 0)
# Set True only when selecting/copying live Debug text must freeze its refresh.
# False keeps diagnostics live even if a touch or mouse action selects text.
FREEZE_DEBUG_WHILE_TEXT_SELECTED = False
# Disable the optional percentage captions if GUI measurements justify it.
SHOW_TRANSFORM_BAR_VALUES = True
# Source-level option: keep drawing transform bars while the Debug tab is active.
# Default False avoids this GUI workload during processing/jitter measurements.
UPDATE_TRANSFORM_BARS_ON_DEBUG_TAB = False
log = logging.getLogger(__name__)


def transform_bar_values(values):
    """Map six raw sample DOFs to display values (invert heave only)."""
    if len(values) != 6:
        raise ValueError("Expected six transform values")
    return (values[0], values[1], -values[2], values[3], values[4], values[5])


def readiness_indicator_key(platform_state, experience_state, sim_status, parking_state=None):
    """Return the Ready-slot icon key for readiness or active ride state."""
    if parking_state in (PlatformMotionState.PARKING, PlatformMotionState.UNPARKING):
        return "warning"
    if sim_status.data_connected:
        if experience_state is ExperienceState.RUNNING:
            return "running"
        if experience_state is ExperienceState.PAUSED:
            return "paused"
    if not sim_status.data_connected and not sim_status.ready_to_start:
        return "nogo"
    if (
        platform_state is PlatformState.ACTIVATED
        and sim_status.ready_to_start
        and (parking_state is None or parking_state is PlatformMotionState.PARKED)
    ):
        return "ok"
    return "warning"


class _ActivateSwitchWarningDialog(QtWidgets.QDialog):
    """Startup safety prompt which closes only when the switch is turned off."""

    def reject(self):
        pass

    def closeEvent(self, event):
        event.ignore()


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, core, config_store, parent=None):
        super().__init__(parent)
        self._compact_ui = self._use_compact_ui()
        ui_name = "poc_main_800x480.ui" if self._compact_ui else "poc_main.ui"
        ui_path = Path(__file__).with_name(ui_name)
        log.info("Loading %s", ui_name)
        uic.loadUi(str(ui_path), self)
        self.core = core
        self.config_store = config_store
        self.sim_tabs = SimTabManager(self.tabWidget, self.tab_debug)
        self._updating_sim_combo = False
        self._updating_experience_combo = False
        self._updating_platform_list = False
        self._confirmed_experience_index = 0
        self._updating_telemetry_controls = False
        self._status_pixmaps = {}
        self._status_icon_keys = {}
        self._busy_spinner_movie = None
        self._sim_view_pixmaps = {}
        self._csv_stream = None
        self._csv_writer = None
        self._csv_path = None
        self._csv_started_at = None
        self._csv_last_source_frame = None
        self._ride_pixmap = QtGui.QPixmap()
        self._encoder_message_box = None
        self._activate_switch_warning_dialog = None
        self._encoder_dialog_choice = QtWidgets.QMessageBox.Yes
        self._encoder_dialog_kind = None
        self._simulator_browse_active = False
        self._simulator_browse_completed = False
        self._simulator_combo_normal_style = ""
        self._ignore_encoder_press_until = 0.0
        self._last_debug_update_at = 0.0
        self._last_sim_button_state = None
        self.transform_viewer = None
        self._replace_activation_button()
        self._replace_load_buttons()
        self._setup_transform_bars()
        self._setup_debug_controls()

        self._setup_style()
        if not self._compact_ui:
            self._setup_transform_viewer()
        self._setup_controls()
        self._connect_core()
        self._populate_simulators()
        configure_touch_combo_popup(self.combo_simulator)
        self._populate_platforms()
        self.lbl_ride_image.installEventFilter(self)
        if self._compact_ui:
            self._enforce_compact_tabs()

    @staticmethod
    def _use_compact_ui():
        """Select the temporary 800x480 operator layout from screen geometry."""
        override = os.environ.get("POC_COMPACT_UI", "").strip().lower()
        if override in ("1", "true", "yes", "on"):
            return True
        if override in ("0", "false", "no", "off"):
            return False
        screen = QtWidgets.QApplication.primaryScreen()
        if screen is None:
            return False
        size = screen.geometry().size()
        return size.width() <= 800 and size.height() <= 480

    def _enforce_compact_tabs(self):
        """Show useful compact tabs while omitting the Transform Viewer."""
        visible_tabs = (
            self.tab_ride_control,
            self.tab_input,
            self.tab_output,
            self.tab_platform_selection,
            self.tab_debug,
        )
        for index in range(self.tabWidget.count() - 1, -1, -1):
            if self.tabWidget.widget(index) not in visible_tabs:
                self.tabWidget.removeTab(index)
        if self.tabWidget.indexOf(self.tab_ride_control) < 0:
            self.tabWidget.addTab(self.tab_ride_control, "Ride Control")
        if self.tabWidget.indexOf(self.tab_debug) < 0:
            self.tabWidget.addTab(self.tab_debug, "Debug")
        self.tabWidget.setCurrentWidget(self.tab_ride_control)
        self.tabWidget.tabBar().show()
        self.tabWidget.tabBar().setStyleSheet(
            "QTabBar::tab { min-width: 0px; min-height: 32px; padding: 4px 8px; }"
        )

    def _setup_debug_controls(self):
        """Configure behaviour for the static Debug-tab controls from Designer."""
        self.activation_control_group = QtWidgets.QButtonGroup(self)
        self.activation_control_group.setExclusive(True)
        self.activation_control_group.addButton(self.rb_activation_switch)
        self.activation_control_group.addButton(self.rb_activation_touch)
        self.rb_activation_switch.setChecked(
            self.core.activation_control_mode is ActivationControlMode.SWITCH
        )
        self.rb_activation_touch.setChecked(
            self.core.activation_control_mode is ActivationControlMode.TOUCH
        )
        if os.name == "nt":
            # Windows desktop: compact Debug controls without changing Pi layout.
            font = self.txt_debug_state.font()
            font.setPointSize(12)
            self.txt_debug_state.setFont(font)
            for button in (self.rb_activation_switch, self.rb_activation_touch):
                font = button.font()
                font.setPointSize(8)
                button.setFont(font)
            self.grp_debug_controls.setMinimumWidth(160)
            self.grp_debug_controls.setMaximumWidth(160)
            margins = self.activationControlLayout.contentsMargins()
            self.activationControlLayout.setContentsMargins(
                2, margins.top(), 2, margins.bottom()
            )
        self.btn_exit_desktop.clicked.connect(self._confirm_exit_to_desktop)

    def _confirm_exit_to_desktop(self):
        if not self._platform_safe_to_close():
            self._show_deactivate_before_exit()
            return
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Question)
        box.setWindowTitle("Exit POC")
        box.setText("Exit POC and return to the desktop?")
        exit_button = box.addButton("Exit POC", QtWidgets.QMessageBox.AcceptRole)
        cancel_button = box.addButton(QtWidgets.QMessageBox.Cancel)
        box.setDefaultButton(cancel_button)
        box.setEscapeButton(cancel_button)
        box.exec_()
        if box.clickedButton() is exit_button:
            self.close()

    def _platform_safe_to_close(self):
        return self.core.platform.state is PlatformState.DEACTIVATED and self.core.platform.parked

    def _show_deactivate_before_exit(self):
        QtWidgets.QMessageBox.warning(
            self,
            "Platform Active",
            "Deactivate the platform before exiting POC.",
        )

    def _replace_activation_button(self):
        old = self.btn_activate
        button = ActivationButton(old.parent())
        button.setObjectName("btn_activate")
        button.setMinimumSize(old.minimumSize())
        button.setMaximumSize(old.maximumSize())
        button.setSizePolicy(old.sizePolicy())
        button.setStyleSheet(old.styleSheet())
        button.setFont(old.font())
        button.setText(old.text())
        self.controlLayout.replaceWidget(old, button)
        old.deleteLater()
        self.btn_activate = button


    def _setup_transform_bars(self):
        """Install one lightweight six-DOF display in either Designer layout."""
        placeholder = self.transformBarsPlaceholder
        self.transform_bars = TransformBarsWidget(
            self.grp_transform_levels, show_values=SHOW_TRANSFORM_BAR_VALUES
        )
        self.transform_bars.setObjectName("transformBars")
        self.transformBarsLayout.replaceWidget(placeholder, self.transform_bars)
        placeholder.deleteLater()

    def _replace_load_buttons(self):
        for attr in ("btn_normal_load", "btn_full_load"):
            old = getattr(self, attr)
            button = VerticalTextButton(old.parent())
            button.setObjectName(attr)
            button.setMinimumSize(old.minimumSize())
            button.setMaximumSize(old.maximumSize())
            button.setSizePolicy(old.sizePolicy())
            button.setStyleSheet(old.styleSheet())
            button.setFont(old.font())
            button.setText(old.text())
            button.setCheckable(old.isCheckable())
            button.setChecked(old.isChecked())
            if attr == "btn_normal_load":
                button.set_checked_text_color("white")
                button.setStyleSheet(button.styleSheet() + """
                    QPushButton#btn_normal_load {
                        background-color: transparent;
                        color: black;
                        border: 2px solid black;
                        border-radius: 5px;
                    }
                    QPushButton#btn_normal_load:checked {
                        background-color: #006400;
                        color: white;
                        border: 3px solid #003d00;
                        border-bottom-width: 4px;
                        border-right-width: 4px;
                    }
                """)
            else:
                button.set_checked_text_color("black")
                button.setStyleSheet(button.styleSheet() + """
                    QPushButton#btn_full_load {
                        background-color: transparent;
                        color: black;
                        border: 2px solid black;
                        border-radius: 5px;
                    }
                    QPushButton#btn_full_load:checked {
                        background-color: #f28c00;
                        color: black;
                        border: 3px solid #8a4f00;
                        border-bottom-width: 4px;
                        border-right-width: 4px;
                    }
                """)
            self.loadLayout.replaceWidget(old, button)
            old.deleteLater()
            setattr(self, attr, button)

    def _setup_style(self):
        self.setStyleSheet(self.styleSheet() + """
            QGroupBox { border: 2px solid lightblue; border-radius: 5px; margin-top: 10px; padding-top: 8px; }
            QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top center; padding: 0 5px; }
            QPushButton { min-height: 34px; }
            QTabBar::tab { min-width: 150px; min-height: 32px; padding: 4px 10px; }
        """)
        self._load_header_logo()
        self._load_status_pixmaps()
        self._set_status_icon(self.lbl_pc_indicator, "nogo")
        self._set_status_icon(self.lbl_sim_indicator, "nogo")
        self._set_status_icon(self.lbl_data_indicator, "nogo")
        self._set_status_icon(self.lbl_ready_indicator, "nogo")
        self._update_button_style(self.btn_run, "default", "green", "green", "green")
        self._update_button_style(self.btn_pause, "default", "orange", "orange", "orange")
        self.lbl_busy_spinner.show()

        self.load_group = QtWidgets.QButtonGroup(self)
        self.load_group.setExclusive(True)
        for button in (self.btn_normal_load, self.btn_full_load):
            self.load_group.addButton(button)


    def _setup_controls(self):
        self.btn_activate.clicked.connect(self._activate_clicked)
        self.btn_run.clicked.connect(self.core.request_run)
        self.btn_pause.clicked.connect(self.core.request_pause)
        self.btn_reset.clicked.connect(self.core.request_reset_vr)
        self.sld_intensity.valueChanged.connect(self.core.request_intensity_percent)
        self.btn_normal_load.clicked.connect(lambda: self.core.request_payload_level(1))
        self.btn_full_load.clicked.connect(lambda: self.core.request_payload_level(2))
        self.combo_simulator.currentIndexChanged.connect(self._simulator_selected)
        self.combo_experience.activated[int].connect(self._experience_selected)
        self.combo_experience.highlighted[int].connect(self._experience_highlighted)
        self.combo_experience.view().installEventFilter(self)
        self.list_platforms.currentItemChanged.connect(self._platform_selection_changed)
        self.tabWidget.currentChanged.connect(self._tab_changed)
        self.tabWidget.tabBar().tabBarClicked.connect(self._tab_bar_clicked)
        self.rb_contractions.toggled.connect(self._update_output_display)
        self.rb_pressures.toggled.connect(self._update_output_display)
        self.chk_csv_output.toggled.connect(self._csv_output_toggled)
        twin_cfg = self.config_store.get("digital_twin", {})
        address = str(twin_cfg.get("address", "-")).strip()
        port = twin_cfg.get("port", 10020)
        self.lbl_twin_destination.setText(f"{address}:{port}" if address != "-" else "Not configured")
        self.chk_twin_output.setEnabled(address != "-")
        self.chk_twin_output.setChecked(self.core.digital_twin.enabled)
        self.chk_twin_output.toggled.connect(self._twin_output_toggled)
        self.rb_activation_switch.toggled.connect(
            self._activation_control_selection_changed
        )
        self.rb_activation_touch.toggled.connect(
            self._activation_control_selection_changed
        )
        self._setup_telemetry_controls()
        self.btn_normal_load.setChecked(True)
        self.core.request_payload_level(1)
        self._update_output_display()

    def _connect_core(self):
        self.core.simChanged.connect(self._on_sim_changed)
        self.core.simStatusChanged.connect(self._on_sim_status)
        self.core.sampleUpdated.connect(self._on_sample)
        self.core.experienceStateChanged.connect(self._on_experience_state)
        self.core.platformStateChanged.connect(self._on_platform_state)
        self.core.platformActivationChanged.connect(self._on_platform_activation)
        self.core.experiencesChanged.connect(self._on_experiences_changed)
        self.core.message.connect(self._show_message)
        self.core.intensityChanged.connect(self._on_intensity_changed)
        self.core.payloadChanged.connect(self._on_payload_changed)
        self.core.encoderRotated.connect(self._on_encoder_rotated)
        self.core.encoderPressed.connect(self._on_encoder_pressed)
        self.core.simulatorBrowseChanged.connect(self._on_simulator_browse_changed)
        self.core.activateSwitchWarningChanged.connect(
            self._on_activate_switch_warning_changed
        )
        self.core.activationControlModeChanged.connect(
            self._on_activation_control_mode_changed
        )

    def _on_activate_switch_warning_changed(self, visible):
        if not visible:
            dialog = self._activate_switch_warning_dialog
            self._activate_switch_warning_dialog = None
            if dialog is not None:
                dialog.done(QtWidgets.QDialog.Accepted)
                dialog.deleteLater()
            return

        if self._activate_switch_warning_dialog is not None:
            return

        image_path = Path(__file__).resolve().parent.parent / "images" / "activate_warning.png"
        source = QtGui.QPixmap(str(image_path))
        screen = QtWidgets.QApplication.primaryScreen()
        available = screen.availableGeometry().size() if screen is not None else QtCore.QSize(800, 600)
        target = QtCore.QSize(
            max(1, int(available.width() * 0.75)),
            max(1, int(available.height() * 0.72)),
        )
        pixmap = source.scaled(
            target, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation
        ) if not source.isNull() else source

        dialog = _ActivateSwitchWarningDialog(self)
        dialog.setWindowTitle("Activate Switch Warning")
        dialog.setWindowModality(QtCore.Qt.ApplicationModal)
        dialog.setWindowFlag(QtCore.Qt.WindowCloseButtonHint, False)
        layout = QtWidgets.QVBoxLayout(dialog)
        message = QtWidgets.QLabel("Flip the Activate switch down to proceed.", dialog)
        message.setAlignment(QtCore.Qt.AlignCenter)
        message.setStyleSheet("font-size: 18px; color: red; font-weight: bold;")
        layout.addWidget(message)
        image = QtWidgets.QLabel(dialog)
        image.setAlignment(QtCore.Qt.AlignCenter)
        if pixmap.isNull():
            image.setText("ACTIVATE SWITCH DOWN")
        else:
            image.setPixmap(pixmap)
        layout.addWidget(image)
        fallback = QtWidgets.QPushButton("Use Touch UI Control", dialog)
        fallback.setMinimumHeight(48)
        fallback.clicked.connect(
            lambda: self.core.set_activation_control_mode(ActivationControlMode.TOUCH)
        )
        layout.addWidget(fallback)
        dialog.setFixedSize(dialog.sizeHint())
        self._activate_switch_warning_dialog = dialog
        dialog.show()

    def _populate_simulators(self):
        self._updating_sim_combo = True
        self.combo_simulator.clear()
        selected = self.config_store.get("selected_sim", "nolimits2")
        selected_index = 0
        for i, definition in enumerate(OPERATOR_SIMS):
            self.combo_simulator.addItem(definition.display_name, definition.key)
            if definition.key == selected:
                selected_index = i
        self.combo_simulator.setCurrentIndex(selected_index)
        self._updating_sim_combo = False
        self._simulator_combo_normal_style = self.combo_simulator.styleSheet()

    def _populate_platforms(self):
        selected = self.config_store.get("selected_platform", "platform_v2")
        self._updating_platform_list = True
        try:
            self.list_platforms.clear()
            selected_item = None
            for key, definition in PLATFORMS.items():
                item = QtWidgets.QListWidgetItem(definition["name"])
                item.setData(QtCore.Qt.UserRole, key)
                self.list_platforms.addItem(item)
                if key == selected:
                    selected_item = item
            if selected_item is not None:
                self.list_platforms.setCurrentItem(selected_item)
        finally:
            self._updating_platform_list = False

    def _activate_clicked(self):
        self.core.request_touch_activation_toggle()
        self._sync_activate_button()

    def _activation_control_selection_changed(self, checked):
        if not checked:
            return
        mode = (
            ActivationControlMode.TOUCH
            if self.rb_activation_touch.isChecked()
            else ActivationControlMode.SWITCH
        )
        self.core.set_activation_control_mode(mode)

    def _on_activation_control_mode_changed(self, mode):
        for button in (self.rb_activation_switch, self.rb_activation_touch):
            button.blockSignals(True)
        self.rb_activation_switch.setChecked(mode is ActivationControlMode.SWITCH)
        self.rb_activation_touch.setChecked(mode is ActivationControlMode.TOUCH)
        for button in (self.rb_activation_switch, self.rb_activation_touch):
            button.blockSignals(False)
        self._update_debug(force=True)

    def _simulator_selected(self, index):
        if self._updating_sim_combo or index < 0:
            return
        key = self.combo_simulator.itemData(index)
        if key == self.core.sim_key:
            return

        if self.core.platform.state is not PlatformState.DEACTIVATED:
            self._show_message("Deactivate the platform before changing simulator")
            self._restore_sim_combo()
            return

        self._ask_simulator_change(index)

    def _ask_simulator_change(self, index):
        """Open a modeless simulator confirmation that GPIO can control."""
        if self._encoder_message_box is not None:
            return
        old_name = self.core.sim.name if self.core.sim else "current simulator"
        new_name = self.combo_simulator.itemText(index)
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Question)
        box.setWindowTitle("Change Simulator")
        box.setText(f"Change simulator from {old_name} to {new_name}?")
        box.setStandardButtons(QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        box.setDefaultButton(QtWidgets.QMessageBox.Yes)
        box.setEscapeButton(QtWidgets.QMessageBox.No)

        self._encoder_message_box = box
        self._encoder_dialog_choice = QtWidgets.QMessageBox.Yes
        self._encoder_dialog_kind = "simulator"
        yes_button = box.button(QtWidgets.QMessageBox.Yes)
        if yes_button is not None:
            yes_button.setFocus(QtCore.Qt.OtherFocusReason)
        box.finished.connect(
            lambda answer, selected=index, dialog=box:
                self._simulator_change_answered(selected, answer, dialog)
        )
        box.open()

    def _simulator_change_answered(self, index, answer, box):
        """Apply or cancel a mouse/encoder simulator confirmation."""
        if self._encoder_message_box is box:
            self._encoder_message_box = None
            self._encoder_dialog_choice = QtWidgets.QMessageBox.Yes
            self._encoder_dialog_kind = None

        if answer != QtWidgets.QMessageBox.Yes:
            self._restore_sim_combo()
            self._finish_simulator_browse()
            return

        key = self.combo_simulator.itemData(index)
        new_name = self.combo_simulator.itemText(index)
        changed = False
        if not self.core.simulator_hot_switch_supported(key):
            self.core.persist_sim_for_restart(key)
            self._show_message(
                f"{new_name} saved as selected simulator; restart POC to use it"
            )
            # The running adapter has not changed yet; keep the combo showing
            # the simulator that is actually active until POC restarts.
            self._restore_sim_combo()
            changed = True
        else:
            changed = bool(self.core.load_sim(key))

        if not changed:
            self._restore_sim_combo()
        self._finish_simulator_browse(completed=changed)

    def _restore_sim_combo(self):
        self._updating_sim_combo = True
        for i in range(self.combo_simulator.count()):
            if self.combo_simulator.itemData(i) == self.core.sim_key:
                self.combo_simulator.setCurrentIndex(i)
                break
        self._updating_sim_combo = False

    def _set_simulator_combo_index(self, index):
        """Preview a simulator without firing the normal selection handler."""
        self._updating_sim_combo = True
        try:
            self.combo_simulator.setCurrentIndex(index)
        finally:
            self._updating_sim_combo = False

    def _on_simulator_browse_changed(self, held):
        """Enter on DISPATCH press; cancel unfinished browse on release."""
        held = bool(held)
        if held:
            if (
                self.core.platform.state is not PlatformState.DEACTIVATED
                or self._encoder_message_box is not None
            ):
                return
            self._simulator_browse_active = True
            self._simulator_browse_completed = False
            self.combo_simulator.setStyleSheet(
                self._simulator_combo_normal_style
                + "\nQComboBox#combo_simulator { border: 3px solid #d98200; }"
            )
            self.combo_simulator.setFocus(QtCore.Qt.OtherFocusReason)
            return

        if self._simulator_browse_active and not self._simulator_browse_completed:
            # DISPATCH is polled before the encoder switch. If both contacts
            # change in one service cycle, do not let the same encoder press
            # immediately open an experience dialog after this cancellation.
            self._ignore_encoder_press_until = time.monotonic() + 0.15
            box = self._encoder_message_box
            if box is not None and self._encoder_dialog_kind == "simulator":
                box.done(QtWidgets.QMessageBox.No)
            else:
                self._restore_sim_combo()
            self._finish_simulator_browse()

    def _finish_simulator_browse(self, completed=False):
        self._simulator_browse_completed = bool(completed)
        self._simulator_browse_active = False
        self.combo_simulator.setStyleSheet(self._simulator_combo_normal_style)

    def _experience_selected(self, index):
        if self._updating_experience_combo or index < 0:
            return
        if index == self._confirmed_experience_index:
            return

        name = self.combo_experience.itemText(index)
        noun = getattr(self.core.sim, "experience_noun", "ride")
        self._ask_experience_change(
            f"Change {noun.title()}",
            f"Change {noun} to: {name}?",
            index,
        )

    def _ask_experience_change(self, title: str, text: str, index: int):
        """Open a non-blocking modal confirmation so GPIO service keeps running."""
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Question)
        box.setWindowTitle(title)
        box.setText(text)
        box.setStandardButtons(QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        box.setDefaultButton(QtWidgets.QMessageBox.Yes)
        box.setEscapeButton(QtWidgets.QMessageBox.No)

        self._encoder_message_box = box
        self._encoder_dialog_choice = QtWidgets.QMessageBox.Yes
        self._encoder_dialog_kind = "experience"
        yes_button = box.button(QtWidgets.QMessageBox.Yes)
        if yes_button is not None:
            yes_button.setFocus(QtCore.Qt.OtherFocusReason)
        box.finished.connect(
            lambda answer, selected=index, dialog=box:
                self._experience_change_answered(selected, answer, dialog)
        )
        box.open()

    def _experience_change_answered(self, index: int, answer: int, box):
        """Complete or cancel the ride change after the modeless event returns."""
        if self._encoder_message_box is box:
            self._encoder_message_box = None
            self._encoder_dialog_choice = QtWidgets.QMessageBox.Yes
            self._encoder_dialog_kind = None
        if answer != QtWidgets.QMessageBox.Yes:
            self._restore_experience_combo()
            return
        if not self.core.select_experience(index):
            self._restore_experience_combo()

    def _on_encoder_rotated(self, direction):
        """Apply one physical encoder detent according to current POC context."""
        try:
            direction = int(direction)
        except (TypeError, ValueError):
            return
        if direction == 0:
            return
        step = 1 if direction > 0 else -1

        # A modal experience-confirmation box has first claim on the encoder.
        box = self._encoder_message_box
        if box is not None:
            self._encoder_dialog_choice = (
                QtWidgets.QMessageBox.Yes
                if self._encoder_dialog_choice == QtWidgets.QMessageBox.No
                else QtWidgets.QMessageBox.No
            )
            box.setDefaultButton(self._encoder_dialog_choice)
            button = box.button(self._encoder_dialog_choice)
            if button is not None:
                button.setFocus(QtCore.Qt.OtherFocusReason)
            return

        state = self.core.platform.state
        if state is PlatformState.ACTIVATED:
            # Physical encoder intensity has exactly 11 discrete values:
            # 0, 10, 20, ... 100 percent. If the slider currently contains an
            # intermediate mouse-entered value, move to the next 10% boundary.
            current = int(self.core.intensity_percent)
            if step > 0:
                target = min(100, ((current // 10) + 1) * 10)
            else:
                target = max(0, ((current - 1) // 10) * 10) if current > 0 else 0
            if target != current:
                self.core.request_intensity_percent(target)
            return

        if state is PlatformState.DEACTIVATED:
            if self._simulator_browse_active:
                count = self.combo_simulator.count()
                if count <= 0:
                    return
                current = max(0, self.combo_simulator.currentIndex())
                target = max(0, min(count - 1, current + step))
                if target != current:
                    self._set_simulator_combo_index(target)
                return

            count = self.combo_experience.count()
            if count <= 0:
                return
            current = max(0, self.combo_experience.currentIndex())
            target = max(0, min(count - 1, current + step))
            if target != current:
                self.combo_experience.setCurrentIndex(target)
                self._experience_highlighted(target)
            return

        # Encoder input is deliberately ignored during ACTIVATING/DEACTIVATING.

    def _on_encoder_pressed(self):
        """Select the encoder-highlighted dialog or experience option."""
        if time.monotonic() < self._ignore_encoder_press_until:
            return
        box = self._encoder_message_box
        if box is not None:
            button = box.button(self._encoder_dialog_choice)
            if button is not None:
                button.click()
            return

        if self.core.platform.state is PlatformState.DEACTIVATED:
            if self._simulator_browse_active:
                index = self.combo_simulator.currentIndex()
                if index >= 0 and self.combo_simulator.itemData(index) != self.core.sim_key:
                    self._ask_simulator_change(index)
                return

            index = self.combo_experience.currentIndex()
            if index >= 0:
                self._experience_selected(index)
        # Encoder push has no action while activated or in transition.

    def _restore_experience_combo(self):
        self._updating_experience_combo = True
        if 0 <= self._confirmed_experience_index < self.combo_experience.count():
            self.combo_experience.setCurrentIndex(self._confirmed_experience_index)
        self._updating_experience_combo = False
        self._load_ride_image_for_index(self._confirmed_experience_index)

    def _experience_highlighted(self, index):
        if index >= 0:
            self._load_ride_image_for_index(index)

    def eventFilter(self, watched, event):
        if watched is self.combo_experience.view() and event.type() == QtCore.QEvent.Hide:
            QtCore.QTimer.singleShot(0, self._restore_experience_combo)
        elif watched is self.lbl_ride_image and event.type() == QtCore.QEvent.Resize:
            QtCore.QTimer.singleShot(0, self._scale_ride_image)
        return super().eventFilter(watched, event)

    def _platform_selection_changed(self, current_item, previous_item):
        if self._updating_platform_list or current_item is None:
            return
        if self.core.platform.state is not PlatformState.DEACTIVATED:
            self._show_message("Deactivate the platform before changing physical platform")
            self._populate_platforms()
            return

        new_key = current_item.data(QtCore.Qt.UserRole)
        current = self.config_store.get("selected_platform", "platform_v2")
        if new_key == current:
            return
        definition = PLATFORMS.get(new_key)
        if definition is None:
            self._populate_platforms()
            return
        new_name = definition["name"]
        answer = QtWidgets.QMessageBox.question(
            self,
            "Change Physical Platform",
            f"Select {new_name}?\n\nThe change will take effect only after POC is restarted.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if answer == QtWidgets.QMessageBox.Yes:
            self.config_store.set("selected_platform", new_key)
            QtWidgets.QMessageBox.information(
                self, "Restart Required", "Physical platform selection saved. Restart POC before operating the platform."
            )
        else:
            self._populate_platforms()

    def _setup_telemetry_controls(self):
        self.gain_sliders = [getattr(self, f"sld_gain_{i}") for i in range(6)]
        self.gain_labels = [getattr(self, f"lbl_gain_{i}") for i in range(6)]
        self.washout_checks = [getattr(self, f"chk_washout_{i}") for i in range(6)]
        self.washout_spins = [getattr(self, f"spin_washout_{i}") for i in range(6)]

        # Built at runtime so both normal and compact .ui layouts use one
        # session-only NL2 control; unrelated simulator tabs remain unchanged.
        from sims.nolimits2 import config as nl2_config
        self.lbl_nl2_heave_mode = QtWidgets.QLabel("NL2 Heave:")
        self.combo_nl2_heave_mode = QtWidgets.QComboBox()
        self.combo_nl2_heave_mode.setObjectName("combo_nl2_heave_mode")
        for caption, mode in (
            ("Legacy Height", nl2_config.HEAVE_MODE_LEGACY_HEIGHT),
            ("Vertical G", nl2_config.HEAVE_MODE_VERTICAL_G),
            ("Normalized Height", nl2_config.HEAVE_MODE_NORMALIZED_HEIGHT),
        ):
            self.combo_nl2_heave_mode.addItem(caption, mode)
        self.combo_nl2_heave_mode.setToolTip(
            "Normalized Height uses calibrated min/max track heights and per-ride amplitude. "
            "Only calibrated rides offer this mode. Choose while deactivated."
        )
        self.telemetryButtonsLayout.insertWidget(0, self.lbl_nl2_heave_mode)
        self.telemetryButtonsLayout.insertWidget(1, self.combo_nl2_heave_mode)
        self.lbl_nl2_heave_mode.hide()
        self.combo_nl2_heave_mode.hide()
        self.combo_nl2_heave_mode.currentIndexChanged.connect(self._nl2_heave_mode_changed)

        for slider in self.gain_sliders + [self.sld_gain_master]:
            slider.valueChanged.connect(self._telemetry_controls_changed)
        for checkbox in self.washout_checks:
            checkbox.toggled.connect(self._telemetry_controls_changed)
        for spin in self.washout_spins + [self.spin_washout_gain]:
            spin.valueChanged.connect(self._telemetry_controls_changed)
        self.combo_washout_type.currentIndexChanged.connect(self._washout_type_changed)
        self.btn_telemetry_save.clicked.connect(self._save_telemetry_controls)
        self.btn_telemetry_cancel.clicked.connect(self._cancel_telemetry_controls)

    def _sync_nl2_heave_mode(self):
        """Reflect the adapter's actual mode and current ride calibration."""
        active = self.core.sim_key == "nolimits2"
        self.lbl_nl2_heave_mode.setVisible(active)
        self.combo_nl2_heave_mode.setVisible(active)
        if not active:
            return
        from sims.nolimits2 import config as nl2_config
        sim = self.core.sim
        calibrated = bool(getattr(sim, "has_normalized_height_profile", lambda: False)())
        normalized_index = self.combo_nl2_heave_mode.findData(
            nl2_config.HEAVE_MODE_NORMALIZED_HEIGHT
        )
        item = self.combo_nl2_heave_mode.model().item(normalized_index)
        if item is not None:
            item.setEnabled(calibrated)
        mode = getattr(sim, "heave_mode", nl2_config.HEAVE_MODE_LEGACY_HEIGHT)
        index = self.combo_nl2_heave_mode.findData(mode)
        self.combo_nl2_heave_mode.blockSignals(True)
        self.combo_nl2_heave_mode.setCurrentIndex(max(0, index))
        self.combo_nl2_heave_mode.blockSignals(False)
        self.combo_nl2_heave_mode.setEnabled(
            self.core.platform.state is PlatformState.DEACTIVATED
        )

    def _nl2_heave_mode_changed(self, index):
        if index < 0 or self.core.sim_key != "nolimits2":
            return
        mode = self.combo_nl2_heave_mode.itemData(index)
        if not self.core.set_nl2_heave_mode(mode):
            self._sync_nl2_heave_mode()

    def _load_telemetry_controls(self, values=None):
        values = values or self.core.get_telemetry_config()
        if not values:
            return
        self._updating_telemetry_controls = True
        try:
            self.sld_gain_master.setValue(int(values.get("master_gain", 100)))
            gains = list(values.get("gains", [100] * 6))
            for i, slider in enumerate(self.gain_sliders):
                slider.setValue(int(gains[i]))
                self.gain_labels[i].setText(str(int(gains[i])))
            self.lbl_gain_master.setText(str(int(values.get("master_gain", 100))))

            kind = str(values.get("washout_type", "disabled")).lower()
            type_index = {"disabled": 0, "classical": 1, "legacy": 2}.get(kind, 0)
            self.combo_washout_type.setCurrentIndex(type_index)
            self.spin_washout_gain.setValue(float(values.get("washout_gain", 1.0)))
            enabled = list(values.get("enabled", [False] * 6))
            params = list(values.get("tau" if kind == "classical" else "legacy_duration", [0.0] * 6))
            for i in range(6):
                self.washout_checks[i].setChecked(bool(enabled[i]))
                self.washout_spins[i].setValue(float(params[i]))
            self._sync_washout_controls()
        finally:
            self._updating_telemetry_controls = False

    def _telemetry_values_from_ui(self):
        kind = ("disabled", "classical", "legacy")[self.combo_washout_type.currentIndex()]
        current = self.core.get_telemetry_config() or {}
        tau = list(current.get("tau", [1.0] * 6))
        legacy_duration = list(current.get("legacy_duration", [12.0] * 6))
        active_values = [float(spin.value()) for spin in self.washout_spins]
        if kind == "classical":
            tau = active_values
        elif kind == "legacy":
            legacy_duration = active_values
        return {
            "master_gain": self.sld_gain_master.value(),
            "gains": [slider.value() for slider in self.gain_sliders],
            "washout_type": kind,
            "washout_gain": self.spin_washout_gain.value(),
            "enabled": [checkbox.isChecked() for checkbox in self.washout_checks],
            "tau": tau,
            "legacy_duration": legacy_duration,
        }

    def _telemetry_controls_changed(self, *args):
        if self._updating_telemetry_controls:
            return
        self.lbl_gain_master.setText(str(self.sld_gain_master.value()))
        for label, slider in zip(self.gain_labels, self.gain_sliders):
            label.setText(str(slider.value()))
        self._sync_washout_controls()
        self.core.apply_telemetry_config(self._telemetry_values_from_ui())

    def _washout_type_changed(self, *args):
        if self._updating_telemetry_controls:
            return
        # Slider/spin edits are already live in the core. Change only the type
        # here, then reload the parameter values belonging to the new type.
        values = self.core.get_telemetry_config()
        values["washout_type"] = ("disabled", "classical", "legacy")[self.combo_washout_type.currentIndex()]
        self.core.apply_telemetry_config(values)
        self._load_telemetry_controls(self.core.get_telemetry_config())

    def _sync_washout_controls(self):
        if not bool(getattr(self.core.sim, "supports_washout", True)):
            self.combo_washout_type.setEnabled(False)
            self.spin_washout_gain.setEnabled(False)
            self.labelWashoutParam.setText("Washout not used for SpaceCoaster CSV telemetry")
            for checkbox, spin in zip(self.washout_checks, self.washout_spins):
                checkbox.setEnabled(False)
                spin.setEnabled(False)
            return

        self.combo_washout_type.setEnabled(True)
        kind = ("disabled", "classical", "legacy")[self.combo_washout_type.currentIndex()]
        enabled = kind != "disabled"
        self.spin_washout_gain.setEnabled(kind == "classical")
        self.labelWashoutParam.setText(
            "Time constant (s)" if kind == "classical" else
            "Decay to 2% (s)" if kind == "legacy" else
            "Washout disabled"
        )
        for checkbox, spin in zip(self.washout_checks, self.washout_spins):
            checkbox.setEnabled(enabled)
            spin.setEnabled(enabled and checkbox.isChecked())

    def _save_telemetry_controls(self):
        self.core.save_telemetry_config(self._telemetry_values_from_ui())
        self._load_telemetry_controls()

    def _cancel_telemetry_controls(self):
        self._load_telemetry_controls(self.core.cancel_telemetry_config())

    def _on_intensity_changed(self, percent):
        percent = int(percent)
        self.sld_intensity.blockSignals(True)
        self.sld_intensity.setValue(percent)
        self.sld_intensity.blockSignals(False)
        self.lbl_intensity_value.setText(f"{percent}%")
        self._update_debug()

    def _on_payload_changed(self, level):
        level = int(level)
        self.btn_normal_load.blockSignals(True)
        self.btn_full_load.blockSignals(True)
        self.btn_normal_load.setChecked(level == 1)
        self.btn_full_load.setChecked(level == 2)
        self.btn_normal_load.blockSignals(False)
        self.btn_full_load.blockSignals(False)
        self._update_debug()

    def _on_sim_changed(self, key):
        self._last_sim_button_state = None
        if self._csv_stream is not None:
            self._close_csv_output(report=True)
            self.chk_csv_output.blockSignals(True)
            self.chk_csv_output.setChecked(False)
            self.chk_csv_output.blockSignals(False)
        self._restore_sim_combo()
        self.btn_run.setText("Start")
        self.btn_pause.setText(self.core.sim.pause_label)
        self.sim_tabs.load(getattr(self.core.sim, "ui_tabs", ()))
        if self._compact_ui:
            self._enforce_compact_tabs()
        self._load_sim_images()
        self._load_busy_spinner()
        self._load_ride_image()
        self._load_telemetry_controls()
        self._sync_nl2_heave_mode()
        self._show_message(f"{self.core.sim.name} loaded at {self.core.sim.address}")

    def _on_experiences_changed(self, experiences, selected_index):
        if self._csv_stream is not None:
            self._csv_last_source_frame = None
        self._updating_experience_combo = True
        self.combo_experience.clear()
        self.combo_experience.addItems(experiences)
        if experiences:
            selected_index = max(0, min(selected_index, len(experiences) - 1))
            self.combo_experience.setCurrentIndex(selected_index)
            self._confirmed_experience_index = selected_index
        else:
            self._confirmed_experience_index = -1
        self._updating_experience_combo = False
        self._load_ride_image()
        self._sync_nl2_heave_mode()

    def _on_sim_status(self, status):
        self._set_status_icon(self.lbl_pc_indicator, "ok" if status.pc_connected else "nogo")
        self._set_status_icon(
            self.lbl_sim_indicator,
            "ok" if status.sim_connected else ("warning" if status.pc_connected else "nogo"),
        )
        self._set_status_icon(
            self.lbl_data_indicator,
            "warning" if status.busy else (
                "ok" if status.data_connected else ("warning" if status.sim_connected else "nogo")
            ),
        )
        self._set_status_icon(
            self.lbl_ready_indicator,
            readiness_indicator_key(
                self.core.platform.state,
                self.core.experience_state,
                status,
                self.core.platform.parking_state,
            ),
        )
        all_connection_states_ok = bool(
            status.pc_connected and status.sim_connected
            and (status.data_connected or self.core.ready_for_dispatch)
            and not status.busy
        )
        self._set_busy(not all_connection_states_ok)
        if status.text:
            self.lbl_status_message.setText(status.text)
        self._sync_sim_control_buttons(status)
        self._update_debug()

    def _on_sample(self, sample):
        values = tuple(float(value) for value in sample.transform)
        current_tab = self.tabWidget.currentWidget()
        # Keep raw telemetry unchanged; only the heave bar uses inverted sign.
        # The Debug-tab switch isolates the painter overhead at the full 20 Hz.
        if UPDATE_TRANSFORM_BARS_ON_DEBUG_TAB or current_tab is not self.tab_debug:
            self.transform_bars.set_values(transform_bar_values(values))
        if current_tab is self.tab_output:
            self.lbl_transform_from_sim.setText(
                "x={: .2f}  y={: .2f}  z={: .2f}  roll={: .2f}  pitch={: .2f}  yaw={: .2f}".format(*values)
            )
            self.lbl_sim_speed.setText(
                "Speed=--" if sample.speed is None else f"Speed={max(0.0, float(sample.speed)):.1f}"
            )
            self._update_output_display()
            self._update_sim_images(values)
        elif current_tab is self.tab_transform:
            self._update_transform_viewer(sample)
        self._capture_csv_sample(sample)
        self._update_debug()

    def _load_ride_image(self):
        self._load_ride_image_for_index(self.combo_experience.currentIndex())

    def _load_ride_image_for_index(self, index):
        if self.core.sim_key is None or index < 0 or index >= self.combo_experience.count():
            return
        display_name = self.combo_experience.itemText(index)
        self.grp_ride_controls.setTitle(display_name)
        name = display_name.strip()
        image_dir = Path(__file__).resolve().parent.parent / "sims" / self.core.sim_key / "images"
        found = None
        for suffix in (".png", ".jpg", ".jpeg"):
            candidate = image_dir / (name + suffix)
            if candidate.exists():
                found = candidate
                break
        if found is None:
            self._ride_pixmap = QtGui.QPixmap()
            self.lbl_ride_image.setPixmap(QtGui.QPixmap())
            self.lbl_ride_image.setText(f"{name}\nimage not configured")
            return
        self._ride_pixmap = QtGui.QPixmap(str(found))
        self.lbl_ride_image.setText("")
        self._scale_ride_image()

    def _scale_ride_image(self):
        if self._ride_pixmap.isNull():
            return
        size = self.lbl_ride_image.size()
        if size.width() <= 0 or size.height() <= 0:
            return
        self.lbl_ride_image.setPixmap(
            self._ride_pixmap.scaled(
                size,
                QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation,
            )
        )

    def _load_busy_spinner(self):
        self._set_busy(False)
        self._busy_spinner_movie = None
        if self.core.sim_key is None:
            return
        gif = Path(__file__).resolve().parent.parent / "sims" / self.core.sim_key / "images" / "busy_spinner.gif"
        if not gif.exists():
            return
        movie = QtGui.QMovie(str(gif))
        if not movie.isValid():
            return
        movie.setCacheMode(QtGui.QMovie.CacheAll)
        spinner_size = 56 if self._compact_ui else 80
        movie.setScaledSize(QtCore.QSize(spinner_size, spinner_size))
        self._busy_spinner_movie = movie

    def _set_busy(self, busy):
        self.lbl_busy_spinner.setVisible(True)
        busy = bool(busy and self._busy_spinner_movie is not None)
        if busy:
            self.lbl_busy_spinner.setMovie(self._busy_spinner_movie)
            if self._busy_spinner_movie.state() != QtGui.QMovie.Running:
                self._busy_spinner_movie.start()
            self.lbl_busy_spinner.raise_()
        else:
            if self._busy_spinner_movie is not None:
                self._busy_spinner_movie.stop()
            self.lbl_busy_spinner.clear()

    def _load_sim_images(self):
        self._sim_view_pixmaps = {}
        if self.core.sim_key is None:
            return
        image_dir = Path(__file__).resolve().parent.parent / "sims" / self.core.sim_key / "images"
        labels = ((self.lbl_front_view, "front"), (self.lbl_side_view, "side"), (self.lbl_top_view, "top"))
        for label, stem in labels:
            found = None
            for suffix in (".png", ".jpg", ".jpeg"):
                candidate = image_dir / (stem + suffix)
                if candidate.exists():
                    found = candidate
                    break
            if found is None:
                label.setPixmap(QtGui.QPixmap())
                label.setText(f"{stem.title()} image not configured")
                continue
            pixmap = QtGui.QPixmap(str(found))
            label.setText("")
            target_size = label.size()
            if stem in ("front", "side"):
                target_size = QtCore.QSize(
                    max(1, target_size.width() - (2 * SIM_VIEW_TRAVEL_MARGIN)),
                    max(1, target_size.height() - (2 * SIM_VIEW_TRAVEL_MARGIN)),
                )
            scaled = pixmap.scaled(
                target_size, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation
            )
            self._sim_view_pixmaps[stem] = self._add_image_guides(scaled)
        self._update_sim_images((0.0,) * 6)

    @staticmethod
    def _opaque_bounds(pixmap):
        """Return the visible-pixel bounds, ignoring faint scaled alpha edges."""
        image = pixmap.toImage().convertToFormat(QtGui.QImage.Format_ARGB32)
        left, top = image.width(), image.height()
        right = bottom = -1
        for y in range(image.height()):
            for x in range(image.width()):
                if QtGui.qAlpha(image.pixel(x, y)) >= 32:
                    left = min(left, x)
                    right = max(right, x)
                    top = min(top, y)
                    bottom = max(bottom, y)
        if right < left or bottom < top:
            return QtCore.QRect()
        return QtCore.QRect(left, top, right - left + 1, bottom - top + 1)

    def _add_image_guides(self, pixmap):
        """Add moving reference segments up to the visible image boundary."""
        guided = QtGui.QPixmap(pixmap)
        bounds = self._opaque_bounds(pixmap)
        if bounds.isNull():
            return guided

        center_x = guided.width() // 2
        center_y = guided.height() // 2
        gap = 2
        painter = QtGui.QPainter(guided)
        pen = QtGui.QPen(SIM_GUIDE_IMAGE_COLOR, SIM_GUIDE_WIDTH)
        pen.setCapStyle(QtCore.Qt.FlatCap)
        painter.setPen(pen)
        painter.drawLine(0, center_y, max(0, bounds.left() - gap), center_y)
        painter.drawLine(
            min(guided.width() - 1, bounds.right() + gap),
            center_y,
            guided.width() - 1,
            center_y,
        )
        painter.drawLine(center_x, 0, center_x, max(0, bounds.top() - gap))
        painter.drawLine(
            center_x,
            min(guided.height() - 1, bounds.bottom() + gap),
            center_x,
            guided.height() - 1,
        )
        painter.end()
        return guided

    def _draw_sim_view(self, label, pixmap, dx, dy, angle_deg):
        if pixmap is None or pixmap.isNull():
            return
        transform = QtGui.QTransform()
        transform.rotate(float(angle_deg))
        rotated = pixmap.transformed(transform, QtCore.Qt.SmoothTransformation)

        canvas = QtGui.QPixmap(label.size())
        canvas.fill(QtCore.Qt.transparent)
        x = int((canvas.width() - rotated.width()) / 2 + dx)
        y = int((canvas.height() - rotated.height()) / 2 + dy)
        painter = QtGui.QPainter(canvas)
        background_pen = QtGui.QPen(SIM_GUIDE_BACKGROUND_COLOR, SIM_GUIDE_WIDTH)
        background_pen.setCapStyle(QtCore.Qt.FlatCap)
        painter.setPen(background_pen)
        center_x = canvas.width() // 2
        center_y = canvas.height() // 2
        painter.drawLine(0, center_y, canvas.width() - 1, center_y)
        painter.drawLine(center_x, 0, center_x, canvas.height() - 1)
        painter.drawPixmap(x, y, rotated)
        painter.end()
        label.setPixmap(canvas)

    @staticmethod
    def _edge_scaled_translation(label, pixmap, x_value, y_value):
        """Map normalized translation so +/-100% reaches the label boundary."""
        x_travel = max(0.0, (label.width() - pixmap.width()) / 2.0)
        y_travel = max(0.0, (label.height() - pixmap.height()) / 2.0)
        x_value = max(-1.0, min(1.0, float(x_value)))
        y_value = max(-1.0, min(1.0, float(y_value)))
        return int(round(x_value * x_travel)), int(round(y_value * y_travel))

    def _update_sim_images(self, transform):
        if self.tabWidget.currentWidget() is not self.tab_output:
            return
        if not transform or len(transform) != 6:
            return
        surge, sway, heave, roll, pitch, yaw = [float(v) for v in transform]
        front_pixmap = self._sim_view_pixmaps.get("front")
        side_pixmap = self._sim_view_pixmaps.get("side")
        front_dx, front_dy = self._edge_scaled_translation(
            self.lbl_front_view, front_pixmap, sway, -heave,
        ) if front_pixmap is not None else (0, 0)
        side_dx, side_dy = self._edge_scaled_translation(
            self.lbl_side_view, side_pixmap, surge, -heave,
        ) if side_pixmap is not None else (0, 0)
        self._draw_sim_view(
            self.lbl_front_view, front_pixmap,
            front_dx, front_dy, -roll * ROTATE_SCALE,
        )
        self._draw_sim_view(
            self.lbl_side_view, side_pixmap,
            side_dx, side_dy, -pitch * ROTATE_SCALE,
        )
        self._draw_sim_view(
            self.lbl_top_view, self._sim_view_pixmaps.get("top"),
            int(sway * XLATE_SCALE), int(surge * XLATE_SCALE), yaw * ROTATE_SCALE,
        )

    @staticmethod
    def _csv_columns():
        axes = ("x", "y", "z", "roll", "pitch", "yaw")
        columns = [
            "wall_time", "elapsed_s", "sim", "experience", "experience_state",
            "telemetry_valid", "frame", "speed",
            "pos_x", "pos_y", "pos_z",
            "quat_x", "quat_y", "quat_z", "quat_w",
            "g_force_x", "g_force_y", "g_force_z",
        ]
        columns.extend(f"sim_{axis}" for axis in axes)
        columns.extend(f"motion_{axis}" for axis in axes)
        columns.extend(f"muscle_{i}_mm" for i in range(6))
        columns.extend(f"contraction_{i}_pct" for i in range(6))
        columns.extend(f"pressure_{i}_mbar" for i in range(6))
        return columns

    def _twin_output_toggled(self, enabled):
        active = self.core.set_digital_twin_enabled(enabled)
        if active != enabled:
            self.chk_twin_output.blockSignals(True)
            self.chk_twin_output.setChecked(active)
            self.chk_twin_output.blockSignals(False)
            self.core.message.emit("Twin output could not be enabled; check destination")

    def _csv_output_toggled(self, enabled):
        if not enabled:
            self._close_csv_output(report=True)
            return

        try:
            capture_dir = Path(__file__).resolve().parent.parent / "captures"
            capture_dir.mkdir(parents=True, exist_ok=True)
            sim_name = "".join(
                char if char.isalnum() or char in ("-", "_") else "_"
                for char in str(self.core.sim_key or "sim")
            )
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self._csv_path = capture_dir / f"{sim_name}_telemetry_{timestamp}.csv"
            self._csv_stream = self._csv_path.open("w", newline="", encoding="utf-8")
            self._csv_writer = csv.DictWriter(
                self._csv_stream, fieldnames=self._csv_columns(), extrasaction="ignore"
            )
            self._csv_writer.writeheader()
            self._csv_stream.flush()
            self._csv_started_at = time.monotonic()
            self._csv_last_source_frame = None
            self._show_message(f"CSV capture started: {self._csv_path.name}")
        except OSError as exc:
            self._close_csv_output(report=False)
            self.chk_csv_output.blockSignals(True)
            self.chk_csv_output.setChecked(False)
            self.chk_csv_output.blockSignals(False)
            self._show_message(f"Unable to start CSV capture: {exc}")

    def _close_csv_output(self, report=False):
        path = self._csv_path
        stream = self._csv_stream
        self._csv_stream = None
        self._csv_writer = None
        self._csv_path = None
        self._csv_started_at = None
        self._csv_last_source_frame = None
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass
        if report and path is not None:
            self._show_message(f"CSV capture saved: {path}")

    def _capture_csv_sample(self, sample):
        if self._csv_writer is None or self._csv_stream is None:
            return

        source = getattr(getattr(self.core.sim, "client", None), "latest_telemetry", None)
        frame = getattr(source, "frame", None)
        if frame is not None:
            source_frame = (
                self.core.sim_key,
                int(frame),
                getattr(source, "state", None),
            )
            if source_frame == self._csv_last_source_frame:
                return
            self._csv_last_source_frame = source_frame

        output = self.core.get_output_snapshot()
        debug = self.core.get_debug_snapshot()
        row = {
            "wall_time": datetime.now().isoformat(timespec="milliseconds"),
            "elapsed_s": f"{time.monotonic() - self._csv_started_at:.3f}",
            "sim": self.core.sim_key or "",
            "experience": self.combo_experience.currentText(),
            "experience_state": sample.state.name,
            "telemetry_valid": int(bool(sample.telemetry_valid)),
            "frame": "" if frame is None else int(frame),
        }
        for field in (
            "speed", "pos_x", "pos_y", "pos_z",
            "quat_x", "quat_y", "quat_z", "quat_w",
            "g_force_x", "g_force_y", "g_force_z",
        ):
            value = getattr(source, field, "")
            if field == "speed" and value in ("", None) and sample.speed is not None:
                value = sample.speed
            row[field] = value
        for axis, value in zip(("x", "y", "z", "roll", "pitch", "yaw"), sample.transform):
            row[f"sim_{axis}"] = float(value)
        for axis, value in zip(
            ("x", "y", "z", "roll", "pitch", "yaw"), debug["motion_transform"]
        ):
            row[f"motion_{axis}"] = float(value)
        for i, value in enumerate(output["muscle_lengths"]):
            row[f"muscle_{i}_mm"] = value
        for i, value in enumerate(output["contractions"]):
            row[f"contraction_{i}_pct"] = value
        for i, value in enumerate(output["pressures"]):
            row[f"pressure_{i}_mbar"] = value

        try:
            self._csv_writer.writerow(row)
            self._csv_stream.flush()
        except OSError as exc:
            self._close_csv_output(report=False)
            self.chk_csv_output.blockSignals(True)
            self.chk_csv_output.setChecked(False)
            self.chk_csv_output.blockSignals(False)
            self._show_message(f"CSV capture stopped: {exc}")

    def _update_output_display(self):
        if self.tabWidget.currentWidget() is not self.tab_output:
            return
        snap = self.core.get_output_snapshot()
        self.lbl_output_mode.setText("FESTO UDP OUTPUT")
        self.lbl_festo_ip.setText(snap["festo_ip"] or "--")
        show_pressure = self.rb_pressures.isChecked()
        values = snap["pressures"] if show_pressure else snap["contractions"]
        lengths = snap["muscle_lengths"]
        bars = [getattr(self, f"muscle_{i}") for i in range(6)]
        texts = [getattr(self, f"txt_muscle_{i}") for i in range(6)]
        for i, (bar, text, value, length) in enumerate(zip(bars, texts, values, lengths)):
            if show_pressure:
                bar.setMaximum(6000)
                bar.setValue(int(max(0, min(6000, value))))
                bar.setFormat(f"{int(value)} mbar")
            else:
                bar.setMaximum(100)
                bar.setValue(int(max(0, min(100, value))))
                bar.setFormat(f"{value:.0f}%")
            text.setText(f"M{i}: {length:.1f} mm")

    def _on_experience_state(self, state):
        self.btn_reset.setEnabled(state in (ExperienceState.RUNNING, ExperienceState.PAUSED, ExperienceState.READY))
        self._sync_sim_control_buttons()
        self._set_status_icon(
            self.lbl_ready_indicator,
            readiness_indicator_key(
                self.core.platform.state,
                state,
                self.core.get_sim_status(),
                self.core.platform.parking_state,
            ),
        )
        self._update_debug()

    def _on_platform_state(self, state):
        self.combo_nl2_heave_mode.setEnabled(
            self.core.sim_key == "nolimits2" and state is PlatformState.DEACTIVATED
        )
        if state is not PlatformState.DEACTIVATED and self._simulator_browse_active:
            self._on_simulator_browse_changed(False)
        self._sync_activate_button()
        selection_enabled = state is PlatformState.DEACTIVATED and self.core.platform.parked
        self.list_platforms.setEnabled(selection_enabled)
        self.combo_simulator.setEnabled(selection_enabled)
        self.combo_experience.setEnabled(selection_enabled)
        mode_selection_enabled = self.core.platform.parked
        self.rb_activation_switch.setEnabled(mode_selection_enabled)
        self.rb_activation_touch.setEnabled(mode_selection_enabled)
        self._sync_sim_control_buttons()
        self._set_status_icon(
            self.lbl_ready_indicator,
            readiness_indicator_key(
                state,
                self.core.experience_state,
                self.core.get_sim_status(),
                self.core.platform.parking_state,
            ),
        )
        self._update_debug()

    def _on_platform_activation(self, percent):
        self.btn_activate.set_activation_percent(percent)

    def _sync_sim_control_buttons(self, status=None):
        if self.core.sim is None:
            self.btn_run.setEnabled(False)
            self.btn_pause.setEnabled(False)
            return
        if status is None:
            status = self.core.get_sim_status()
        links_ready = bool(status.pc_connected and status.sim_connected and status.data_connected)
        start_ready = self.core.ready_for_dispatch
        pause_enabled = bool(
            links_ready
            and self.core.platform.state is PlatformState.ACTIVATED
            and self.core.platform.parking_state is PlatformMotionState.UNPARKED
            and not self.core.estop_latched
            and self.core.experience_state in (ExperienceState.RUNNING, ExperienceState.PAUSED)
        )
        paused = self.core.experience_state is ExperienceState.PAUSED
        button_state = (start_ready, pause_enabled, paused)
        if button_state == self._last_sim_button_state:
            return
        self._last_sim_button_state = button_state
        self.btn_run.setEnabled(start_ready)
        self.btn_pause.setEnabled(pause_enabled)
        self._update_button_style(
            self.btn_run, "active" if start_ready else "default", "green", "white" if start_ready else "green", "darkgreen" if start_ready else "green"
        )
        self._update_button_style(
            self.btn_pause, "active" if paused else "default", "orange", "black" if paused else "orange", "darkorange" if paused else "orange"
        )

    def _sync_activate_button(self):
        state = self.core.platform.state
        self.btn_activate.set_activation_percent(self.core.platform.activation_percent)
        if self.core.platform.parking_state is PlatformMotionState.PARKING:
            self.btn_activate.setChecked(state is PlatformState.ACTIVATED)
            self.btn_activate.setText("PARKING")
        elif self.core.platform.parking_state is PlatformMotionState.UNPARKING:
            self.btn_activate.setChecked(True)
            self.btn_activate.setText("UNPARKING")
        elif state is PlatformState.DEACTIVATED:
            self.btn_activate.setChecked(False)
            self.btn_activate.setText("INACTIVE")
        elif state is PlatformState.ACTIVATING:
            self.btn_activate.setChecked(True)
            self.btn_activate.setText("ACTIVATING")
        elif state is PlatformState.ACTIVATED:
            self.btn_activate.setChecked(True)
            self.btn_activate.setText("ACTIVATED")
        elif state is PlatformState.DEACTIVATING:
            self.btn_activate.setChecked(False)
            self.btn_activate.setText("DEACTIVATING")
        else:
            self.btn_activate.setChecked(False)
            self.btn_activate.setText("FAULT")

    def _update_debug(self, force=False):
        # Building and replacing this document at the 20 Hz telemetry rate is
        # unnecessarily expensive on a Pi 3. It also clears a user's text
        # selection before Copy can act. Keep hidden debug work dormant and
        # limit visible refresh to 4 Hz. Optional selection-freezing is off by
        # default because an accidental touch selection can look like stale
        # diagnostics.
        if not force and self.tabWidget.currentWidget() is not self.tab_debug:
            return
        if (
            FREEZE_DEBUG_WHILE_TEXT_SELECTED
            and self.txt_debug_state.textCursor().hasSelection()
        ):
            return
        now = time.monotonic()
        if not force and now - self._last_debug_update_at < 0.25:
            return

        snapshot = self.core.get_debug_snapshot()
        try:
            temperature_millidegrees = int(
                Path("/sys/class/thermal/thermal_zone0/temp").read_text(encoding="ascii").strip()
            )
            pi_temperature = f"{temperature_millidegrees / 1000.0:.1f} °C"
        except (OSError, ValueError):
            pi_temperature = "unavailable"
        pong_age = snapshot["shs_last_pong_age"]
        pong_text = "--" if pong_age is None else f"{pong_age:.2f} s"
        station_status = snapshot.get("sim_station_status")
        station_text = "None" if station_status is None else f"{int(station_status)} (0x{int(station_status):08x})"
        festo_ip = snapshot.get("festo_ip") or "--"
        sim_ip = getattr(self.core.host_service, "address", "") or "--"
        lines = [
            f"Pi temperature  : {pi_temperature}",
            f"Frame timing   : processing={snapshot['processing_percent']}%  max(10s)={snapshot['processing_max_percent']}%  jitter={snapshot['jitter_percent']}%",
            f"Platform state : {snapshot['platform_state']}  Festo={festo_ip}  enabled={snapshot['platform_backend_enabled']}  clips={snapshot.get('length_clip_count', 0)}",
            f"Simulator state: POC={snapshot['experience_state']}  native={snapshot['sim_native_state']}  SHS={sim_ip}",
            f"E-STOP         : latched={snapshot['estop_latched']}  recovery={snapshot.get('estop_recovery_active')}",
            f"Ready/start    : {snapshot.get('ready_to_start')}  NL2 recovery={snapshot.get('sim_estop_recovery_pending')}  manual={snapshot.get('sim_manual_mode_requested')}",
            f"Station status : {station_text}",
            f"Activation     : mode={snapshot['activation_control_mode']}  interlock={snapshot['activation_interlock']}",
            f"Request result : {snapshot['activation_request_result']}",
        ]
        lines.extend((
            f"Sim connection : attempts={snapshot['sim_connect_attempts']}",
            f"Sim error      : {snapshot['sim_last_error']}",
            f"Physical input : activate={snapshot['activation_switch']}  last event={snapshot['remote_last_event']}",
            f"Remote error   : {snapshot['remote_last_error']}",
            f"SHS connection : connected={snapshot['shs_connected']}  pong age={pong_text}",
            f"SHS last ack   : {snapshot['shs_last_ack']}",
            f"SHS error      : {snapshot['shs_last_error']}",
            f"Last message   : {self.lbl_status_message.text()}",
        ))
        text = "\n".join(lines)
        if self.txt_debug_state.toPlainText() != text:
            self.txt_debug_state.setPlainText(text)
        self._last_debug_update_at = now

    def _tab_bar_clicked(self, index):
        if self.tabWidget.widget(index) is not self.tab_debug:
            return
        # Capture one complete, current snapshot. This avoids trying to select
        # text while the live Debug view is being refreshed.
        self._update_debug(force=True)
        QtWidgets.QApplication.clipboard().setText(self.txt_debug_state.toPlainText())

    def _load_header_logo(self):
        image_path = Path(__file__).resolve().parent.parent / "images" / "POC.png"
        pixmap = QtGui.QPixmap(str(image_path)) if image_path.exists() else QtGui.QPixmap()
        if pixmap.isNull():
            self.labelPoc.setPixmap(QtGui.QPixmap())
            self.labelPoc.setText("POC")
            return
        self.labelPoc.setText("")
        target_height = max(1, self.headerFrame.sizeHint().height() - 12)
        self.labelPoc.setPixmap(
            pixmap.scaledToHeight(target_height, QtCore.Qt.SmoothTransformation)
        )

    def _setup_transform_viewer(self):
        try:
            from ui.transform_viewer import TransformViewer
            self.transform_viewer = TransformViewer(self.frm_transform_viewer)
            self.lbl_transform_viewer_status.setText("Red = input from sim    Blue = motion after controls / washout")
        except Exception as exc:
            self.transform_viewer = None
            self.lbl_transform_viewer_status.setText(f"Transform viewer unavailable: {exc}")

    def _update_transform_viewer(self, sample):
        if self.tabWidget.currentWidget() is not self.tab_transform or self.transform_viewer is None:
            return
        motion = self.core.get_debug_snapshot()["motion_transform"]
        self.transform_viewer.update(sample.transform, motion)

    def _tab_changed(self, index):
        current_tab = self.tabWidget.widget(index)
        if current_tab is self.tab_output:
            self._update_output_display()
            self._update_sim_images(self.core.get_debug_snapshot()["transform"])
        elif current_tab is self.tab_debug:
            self._update_debug(force=True)

    def _load_status_pixmaps(self):
        image_dir = Path(__file__).resolve().parent.parent / "images"
        icon_size = 32 if self._compact_ui else 48
        target_size = QtCore.QSize(icon_size, icon_size)
        self._status_pixmaps = {}
        self._status_icon_keys = {}
        for key in ("nogo", "warning", "ok", "running", "paused"):
            source = QtGui.QPixmap(str(image_dir / f"{key}.png"))
            if not source.isNull():
                self._status_pixmaps[key] = source.scaled(
                    target_size,
                    QtCore.Qt.KeepAspectRatio,
                    QtCore.Qt.SmoothTransformation,
                )

        for label in (
            self.lbl_pc_indicator,
            self.lbl_sim_indicator,
            self.lbl_data_indicator,
            self.lbl_ready_indicator,
        ):
            label.setScaledContents(False)

    def _set_status_icon(self, label, key):
        label_name = label.objectName()
        if self._status_icon_keys.get(label_name) == key:
            return

        self._status_icon_keys[label_name] = key
        pixmap = self._status_pixmaps.get(key)
        if pixmap is None:
            label.setPixmap(QtGui.QPixmap())
            label.setText(key.upper())
            return
        label.setText("")
        label.setPixmap(pixmap)

    def _update_button_style(self, button, state, base_color, text_color, border_color):
        # SOP button styling: filled gradient for active, outlined for default.
        padding = 10 if platform.system() == "Linux" else 8
        if state == "active":
            style = f"""
                QPushButton {{
                    background-color: qlineargradient(spread:pad, x1:0, y1:0, x2:1, y2:1,
                                                      stop:0 {base_color}, stop:1 dark{base_color});
                    color: {text_color};
                    border: 2px solid {border_color};
                    border-radius: 5px;
                    padding: {padding}px;
                    font-weight: bold;
                    border-bottom: 3px solid black;
                    border-right: 3px solid {border_color};
                }}
                QPushButton:pressed {{
                    background-color: qlineargradient(spread:pad, x1:0, y1:1, x2:1, y2:0,
                                                      stop:0 dark{base_color}, stop:1 black);
                    border-bottom: 1px solid {border_color};
                    border-right: 1px solid black;
                }}
            """
        else:
            style = f"""
                QPushButton {{
                    background-color: none;
                    color: {text_color};
                    border: 2px solid {border_color};
                    border-radius: 5px;
                    padding: {padding}px;
                    font-weight: bold;
                    border-bottom: 3px solid black;
                    border-right: 3px solid {border_color};
                }}
                QPushButton:pressed {{
                    background-color: {base_color};
                    color: {text_color};
                    border-bottom: 1px solid {border_color};
                    border-right: 1px solid black;
                }}
            """
        button.setStyleSheet(style)

    def _show_message(self, text):
        self.lbl_status_message.setText(str(text))
        self._update_debug()

    # Intended target for a future physical VR-reset button.
    @QtCore.pyqtSlot()
    def physical_reset_vr_pressed(self):
        self.core.request_reset_vr()

    def closeEvent(self, event):
        if not self._platform_safe_to_close():
            event.ignore()
            self._show_deactivate_before_exit()
            return
        self._close_csv_output(report=False)
        self.core.shutdown()
        super().closeEvent(event)
