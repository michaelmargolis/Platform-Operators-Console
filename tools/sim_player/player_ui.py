# player_ui.py ver 2
"""Construction of sim_player's dockable Qt6 main window.

Only widgets, menus, layouts and signal wiring live here.  ReplayWindow in
app.py remains responsible for loading recordings, playback and UDP output.
The View menu's dock toggles provide a stable registration point for later
strip-chart and tension-feasibility windows.
"""
from __future__ import annotations
from PyQt6.QtCore import Qt, QSize, QTimer
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import (
    QComboBox, QDockWidget, QDoubleSpinBox, QHBoxLayout, QLabel, QPushButton,
    QSlider, QStackedWidget, QToolButton, QVBoxLayout, QWidget, QSizePolicy,
)
from .video_backend import QVideoWidget, Qt6VideoBackend
from .panels import ResponsiveBars, TimelineMarkers, RangeJumpSlider, CueComparisonPanel, clock_label
from .anomaly_panel import AnomalyPanel
from .tuning_panel import MotionTuningPanel
from .angular_cues import MODE_LABELS, MODES
from .platform_selection import platform_choices, platform_metadata
from .theme import BG, PANEL, RAIL, TEXT, MUTED, ACCENT



class CompactVideoStack(QStackedWidget):
    """Don't let a 3D video's native 640x480+ size lock the main window height."""

    def minimumSizeHint(self):
        return QSize(320, 120)

    def sizeHint(self):
        return QSize(800, 410)


class PlayerUiMixin:
    """Qt6 UI builder; mixed into ReplayWindow before QMainWindow."""

    def _create_central(self):
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(7)
        heading = QHBoxLayout()
        self.ride_label = QLabel("SIM PLAYER   /   OPEN A RIDE CAPTURE")
        self.ride_label.setObjectName("rideTitle")
        heading.addWidget(self.ride_label, 1)
        self.twin_status = QLabel("TWIN OFF")
        self.twin_status.setObjectName("twinStatus")
        self.twin_status.setToolTip("Visualizer output is disabled. Enable it in File → Settings.")
        heading.addWidget(self.twin_status)
        self.info_badge = QLabel("OFFLINE")
        self.info_badge.setObjectName("badge")
        heading.addWidget(self.info_badge)
        layout.addLayout(heading)

        self.video_stack = CompactVideoStack()
        self.video_message = QLabel("Open a NoLimits2 telemetry CSV to begin\n\nFile → Open capture")
        self.video_message.setObjectName("videoMessage")
        self.video_message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_message.setWordWrap(True)
        self.video_message.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.video_stack.addWidget(self.video_message)
        self.video_screen = QVideoWidget()
        # Qt6 reads the encoded video frame size and maintains the source
        # aspect ratio automatically. Never stretch or crop the research video.
        self.video_screen.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatio)
        self.video_screen.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.video_stack.addWidget(self.video_screen)
        self.video_backend = Qt6VideoBackend(self.video_screen, self)
        self.video_backend.error.connect(self._video_error)
        # Ignore native video dimensions for the minimum window geometry.
        # Source aspect ratio is still preserved inside the available area.
        self.video_stack.setMinimumSize(320, 120)
        # The video fills any space above the controls.  No splitter is needed:
        # the controls must remain fixed-height, including on window resize.
        self.video_stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self.video_stack, 1)
        self.control_container = QWidget()
        self.control_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        control_layout = QVBoxLayout(self.control_container)
        control_layout.setContentsMargins(0, 0, 0, 0)
        control_layout.setSpacing(7)
        layout.addWidget(self.control_container, 0)

        markers_row = QHBoxLayout()
        self.marker_title = QLabel("KINEMATIC ANOMALIES  •  click to seek; hover for details")
        markers_row.addWidget(self.marker_title)
        markers_row.addStretch(1)
        markers_row.addWidget(QLabel("Markers"))
        self.marker_mode_box = QComboBox()
        self.marker_mode_box.setObjectName("markerCategory")
        self.marker_mode_box.addItem("Ride events", "ride")
        self.marker_mode_box.addItem("Kinematic", "kinematic")
        # Default to kinematic markers while retaining the existing menu order.
        # Set before connecting the change signal so startup cannot trigger a
        # refresh before the timeline widget has been constructed.
        self.marker_mode_box.setCurrentIndex(1)
        self.marker_mode_box.setFixedWidth(137)
        self.marker_mode_box.setToolTip(
            "One timeline marker line: choose recorded ride events or computed kinematic anomalies. "
            "Tension events can be added when the force solver is available."
        )
        self.marker_mode_box.currentIndexChanged.connect(self._marker_mode_changed)
        markers_row.addWidget(self.marker_mode_box)
        markers_row.addSpacing(12)
        self.range_label = QLabel("A 00:00.00  –  B 00:00.00")
        self.range_label.setObjectName("rangeInfo")
        self.range_label.setToolTip("Red A and gold B on the timeline slider are draggable playback boundaries")
        markers_row.addWidget(self.range_label)
        self.position_label = QLabel("00:00.00 / 00:00.00")
        self.position_label.setObjectName("position")
        markers_row.addWidget(self.position_label)
        control_layout.addLayout(markers_row)
        self.markers = TimelineMarkers()
        self.markers.seek_requested.connect(self.seek_seconds)
        self.markers.inspected.connect(lambda s: self.statusBar().showMessage(s, 5000))
        self.markers.marker_selected.connect(self._marker_selected)
        control_layout.addWidget(self.markers)

        self.slider = RangeJumpSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 1)
        self.slider.setValue(0)
        self.slider.setEnabled(False)
        self.slider.valueChanged.connect(lambda ms: self.seek_seconds(ms / 1000))
        self.slider.sliderPressed.connect(self._scrub_started)
        self.slider.sliderReleased.connect(self._scrub_finished)
        self.slider.range_edited.connect(self._range_marker_edited)
        control_layout.addWidget(self.slider)
        controls = QHBoxLayout()
        controls.setSpacing(5)
        self._button(controls, "−5s", lambda: self.seek_seconds(self.current_s - 5), "Back five seconds")
        self._button(controls, "◀", lambda: self.step_frame(-1), "Back one recorded sample")
        self.play_button = self._button(controls, "▶  Play", self.toggle_play, "Play / pause")
        self.play_button.setObjectName("playButton")
        self._button(controls, "▶", lambda: self.step_frame(1), "Forward one recorded sample")
        self._button(controls, "+5s", lambda: self.seek_seconds(self.current_s + 5), "Forward five seconds")
        controls.addSpacing(10)
        controls.addWidget(QLabel("Speed"))
        self.speed_box = QComboBox()
        for factor in (0.25, 0.5, 1.0, 2.0):
            self.speed_box.addItem(f"{factor:g}×", factor)
        self.speed_box.setCurrentIndex(2)
        self.speed_box.currentIndexChanged.connect(self._speed_changed)
        controls.addWidget(self.speed_box)
        controls.addStretch(1)
        self.mark_button = self._button(
            controls, "Mark", self._mark_range,
            "Centre A/B on the current position and select Loop range"
        )
        self.mark_button.setEnabled(False)
        self.range_duration_box = QDoubleSpinBox()
        self.range_duration_box.setRange(0.1, 600.0)
        self.range_duration_box.setDecimals(1)
        self.range_duration_box.setSingleStep(0.5)
        self.range_duration_box.setSuffix(" s")
        self.range_duration_box.setValue(2.0)
        self.range_duration_box.setToolTip("Duration centred around the current position when Mark is pressed")
        controls.addWidget(self.range_duration_box)
        # The A/B controls are a compact, visually distinct group. The
        # downstream Panels menu is separated by a larger right-hand gap.
        self.range_mode_box = QComboBox()
        self.range_mode_box.setObjectName("rangeMode")
        self.range_mode_box.addItem("Full ride", "full")
        self.range_mode_box.addItem("Play range", "range")
        self.range_mode_box.addItem("Loop range", "loop")
        self.range_mode_box.setCurrentIndex(0)
        self.range_mode_box.setFixedWidth(110)
        self.range_mode_box.setEnabled(False)
        self.range_mode_box.setToolTip("Full ride ignores A/B; Play range stops at B; Loop range repeats A to B")
        self.range_mode_box.currentIndexChanged.connect(self._range_mode_changed)
        controls.addWidget(self.range_mode_box)
        controls.addWidget(QLabel("Delay"))
        self.loop_delay_box = QComboBox()
        self.loop_delay_box.setObjectName("loopDelay")
        for seconds in (0.0, 0.5, 1.0, 2.0):
            self.loop_delay_box.addItem(f"{seconds:g} s", seconds)
        self.loop_delay_box.setCurrentIndex(2)
        self.loop_delay_box.setFixedWidth(69)
        self.loop_delay_box.setToolTip("Hold the end frame and Twin pose before restarting a loop")
        self.loop_delay_box.currentIndexChanged.connect(self._loop_delay_changed)
        controls.addWidget(self.loop_delay_box)
        self.loop_feedback_label = QLabel("LOOP HOLD")
        self.loop_feedback_label.setObjectName("loopFeedback")
        self.loop_feedback_label.setVisible(False)
        controls.addWidget(self.loop_feedback_label)
        self.reset_range_button = self._button(
            controls, "Reset range", self._reset_range,
            "Return A and B to full ride and clear the selection"
        )
        self.reset_range_button.setEnabled(False)
        self.panel_button = QToolButton()
        self.panel_button.setText("Panels ▾")
        self.panel_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        controls.addSpacing(22)  # Separate playback range from the Panels menu.
        controls.addWidget(self.panel_button)
        control_layout.addLayout(controls)

        video_row = QHBoxLayout()
        video_row.setContentsMargins(8, 8, 8, 8)
        video_row.addWidget(QLabel("Video offset"))
        self.offset_box = QDoubleSpinBox()
        self.offset_box.setRange(-60, 60)
        self.offset_box.setDecimals(2)
        self.offset_box.setSingleStep(.05)
        self.offset_box.setSuffix(" s")
        self.offset_box.setToolTip("Video timestamp at telemetry elapsed=0. An estimate, not frame-calibrated.\n"
                                   "Adjust to align a visible event with the telemetry time cursor.")
        self.offset_box.valueChanged.connect(self._offset_changed)
        self.offset_box.setEnabled(False)
        video_row.addWidget(self.offset_box)
        self.save_offset_button = QPushButton("Save sync offset")
        self.save_offset_button.clicked.connect(self.save_sync_offset)
        self.save_offset_button.setEnabled(False)
        video_row.addWidget(self.save_offset_button)
        self.sync_label = QLabel("No recording loaded")
        self.sync_label.setObjectName("syncInfo")
        self.sync_label.setToolTip("OBS timing metadata is initially approximate; adjust offset if required.")
        video_row.addWidget(self.sync_label, 1)
        self.video_sync_widget = QWidget()
        self.video_sync_widget.setLayout(video_row)


        # Research options are deliberately session-only and do not mutate
        # NL2's production angular cue settings or telemetry_config.ini.
        experiment_row = QHBoxLayout()
        experiment_row.addWidget(QLabel("Platform"))
        self.platform_box = QComboBox()
        self.platform_box.setObjectName("replayPlatform")
        self.platform_box.setMinimumWidth(150)
        self.platform_box.setMaximumWidth(230)
        self.platform_box.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.platform_box.setMinimumContentsLength(12)
        for key, name in platform_choices():
            self.platform_box.addItem(name, key)
            self.platform_box.setItemData(self.platform_box.count()-1,
                key + " | " + platform_metadata(key)['platform_config_module'], Qt.ItemDataRole.ToolTipRole)
        self.platform_box.setCurrentIndex(self.platform_box.findData(self.platform_key))
        self.platform_box.currentIndexChanged.connect(self._platform_changed)
        experiment_row.addWidget(self.platform_box)
        experiment_row.addWidget(QLabel("Ride variant"))
        self.variant_box = QComboBox()
        self.variant_box.setObjectName("rideVariant")
        self.variant_box.addItem("Raw (no capture loaded)", None)
        self.variant_box.setEnabled(False)
        self.variant_box.setMinimumWidth(150)
        self.variant_box.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.variant_box.setMinimumContentsLength(12)
        self.variant_box.setToolTip(
            "Presets from the selected platform JSON (rejected variants are hidden). "
            "<park> raw preserves the v17 legacy analysis for comparison. "
            "Selecting a preset never edits production settings or the capture.")
        self.variant_box.currentIndexChanged.connect(self._variant_changed)
        experiment_row.addWidget(self.variant_box)
        experiment_row.addSpacing(10)
        experiment_row.addWidget(QLabel("Angular cue"))
        self.cue_box = QComboBox()
        for mode in MODES:
            self.cue_box.addItem(MODE_LABELS[mode], mode)
        self.cue_box.setCurrentIndex(0)
        self.cue_box.setEnabled(False)
        self.cue_box.setMinimumWidth(150)
        self.cue_box.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.cue_box.setMinimumContentsLength(12)
        self.cue_box.setToolTip(
            "Experimental, offline-only NL2 Roll/Pitch mapping. "
            "Recalculates the full recording through POC washout and kinematics. "
            "Not validated for physical platform operation."
        )
        self.cue_box.currentIndexChanged.connect(self._cue_changed)
        experiment_row.addWidget(self.cue_box)
        note = QLabel("Offline only")
        note.setObjectName("researchNote")
        experiment_row.addWidget(note, 1)
        control_layout.addLayout(experiment_row)

        self.setCentralWidget(central)

    def _fix_playback_controls_height(self):
        """Fix the completed controls layout to its natural minimum height."""
        layout = self.control_container.layout()
        layout.activate()
        height = max(layout.minimumSize().height(), layout.sizeHint().height())
        self.control_container.setFixedHeight(height)

    def _button(self, row, text, callback, tooltip):
        b = QPushButton(text)
        b.setToolTip(tooltip)
        b.clicked.connect(callback)
        row.addWidget(b)
        return b

    def _add_dock(self, key, title, widget):
        dock = QDockWidget(title, self)
        dock.setObjectName("sim_player_" + key)
        dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable | QDockWidget.DockWidgetFeature.DockWidgetMovable |
                         QDockWidget.DockWidgetFeature.DockWidgetFloatable)
        dock.setWidget(widget)
        self.panels[key] = dock
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        return dock

    def _create_docks(self):
        self.axis_bars = ResponsiveBars(True)
        self.muscle_bars = ResponsiveBars(False)
        axis = self._add_dock("axes", "Transform · 6DOF", self.axis_bars)
        lengths = self._add_dock("muscles", "Muscle lengths · mm", self.muscle_bars)
        self.cue_comparison = CueComparisonPanel()
        comparison = self._add_dock("comparison", "Angular cues · comparative analysis", self.cue_comparison)
        self.anomaly_panel = AnomalyPanel()
        anomalies = self._add_dock("anomalies", "Kinematic anomalies", self.anomaly_panel)
        self.tuning_panel = MotionTuningPanel()
        tuning = self._add_dock("tuning", "Motion tuning · gains & neutral", self.tuning_panel)
        video_sync = self._add_dock("video_sync", "Video synchronisation", self.video_sync_widget)
        # Keep this accessible through View, but not visible by default.
        video_sync.hide()
        self.tuning_panel.gain_changed.connect(self._gain_changed)
        self.tuning_panel.neutral_changed.connect(self._neutral_offset_changed)
        self.tuning_panel.reset_requested.connect(self._reset_gains)
        self.tuning_panel.baseline_requested.connect(self._set_baseline)
        self.tuning_panel.restore_requested.connect(self._restore_baseline)
        self.tuning_panel.save_requested.connect(self._save_experiment)
        self.tuning_panel.load_requested.connect(self._load_experiment)
        self.anomaly_panel.seek_requested.connect(self.seek_seconds)
        self.anomaly_panel.examine_requested.connect(self._examine_anomaly)
        self.anomaly_panel.thresholds_changed.connect(self._anomaly_thresholds_changed)
        self.splitDockWidget(axis, lengths, Qt.Orientation.Vertical)
        # Reuse the lower-right dock slot; comparison can be shown, hidden,
        # floated or resized without crowding the video player.
        self.tabifyDockWidget(lengths, comparison)
        self.tabifyDockWidget(lengths, anomalies)
        self.tabifyDockWidget(lengths, tuning)
        lengths.raise_()
        # Do not add a blank dock to absorb spare height.  It used to stop
        # the lower right panel short of the bottom of the window (v16).

    def _compact_right_docks(self):
        """Minimise the upper right dock rows, leaving the bottom row to fill.

        A restored layout may have independent vertical docks rather than the
        default lower tab group.  Compact *visible rows* instead of assuming
        only Transform and Muscle lengths exist.  Do this after the first show
        (or Restore default layout); never fight a user's later dock drags.
        """
        right = Qt.DockWidgetArea.RightDockWidgetArea
        docks = [dock for dock in self.panels.values()
                 if dock.isVisible() and not dock.isFloating()
                 and self.dockWidgetArea(dock) == right]
        docks.sort(key=lambda dock: (dock.geometry().top(), dock.geometry().left()))

        rows = []
        for dock in docks:
            # Tabbed docks share a single row.  Some Qt versions report every
            # tab as visible, others only the active tab; either is handled.
            if rows and abs(dock.geometry().top() - rows[-1].geometry().top()) <= 3:
                continue
            rows.append(dock)
        if len(rows) < 2:
            return

        minimums = []
        for dock in rows[:-1]:
            widget = dock.widget()
            child_min = max(widget.minimumHeight(), widget.minimumSizeHint().height()) if widget else 0
            minimums.append(max(dock.minimumHeight(), dock.minimumSizeHint().height(), child_min + 35))
        # The last dock receives all remaining height; no artificial empty
        # spacer, and no hard-coded main-window height or resize event loop.
        self.resizeDocks(rows, minimums + [10000], Qt.Orientation.Vertical)

    def _create_menus(self):
        file_menu = self.menuBar().addMenu("File")
        open_action = QAction("Open capture…", self)
        open_action.setShortcut(QKeySequence(QKeySequence.StandardKey.Open))
        open_action.triggered.connect(self.pick_capture)
        file_menu.addAction(open_action)
        self.save_variant_action = QAction("Save current settings as a ride variant…", self)
        self.save_variant_action.setToolTip(
            "Save a new candidate to the selected platform JSON; existing entries are never overwritten")
        self.save_variant_action.setEnabled(False)
        self.save_variant_action.triggered.connect(self.save_variant_entry)
        file_menu.addAction(self.save_variant_action)
        file_menu.addSeparator()
        settings_action = QAction("Settings…", self)
        settings_action.triggered.connect(self.show_settings)
        file_menu.addAction(settings_action)
        file_menu.addSeparator()
        file_menu.addAction("Exit", self.close)
        view = self.menuBar().addMenu("View")
        for dock in self.panels.values():
            view.addAction(dock.toggleViewAction())
        view.addSeparator()
        self.raw_axes_action = QAction("Show original NL2 CSV transform instead of processed", self)
        self.raw_axes_action.setCheckable(True)
        self.raw_axes_action.setChecked(False)
        self.raw_axes_action.toggled.connect(self._display_current)
        view.addAction(self.raw_axes_action)
        view.addSeparator()
        view.addAction("Show all panels", self.show_all_panels)
        view.addAction("Restore default layout", self.restore_default_layout)
        self.panel_button.setMenu(view)
        help_menu = self.menuBar().addMenu("Help")
        help_menu.addAction("About sim_player", self.about)

    def show_all_panels(self):
        for dock in self.panels.values():
            dock.show()

    def restore_default_layout(self):
        axis, muscle, comparison, anomalies, tuning = (
            self.panels[k] for k in ("axes", "muscles", "comparison", "anomalies", "tuning")
        )
        for dock in (axis, muscle, comparison, anomalies, tuning):
            dock.setFloating(False)
            dock.show()
            self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        self.splitDockWidget(axis, muscle, Qt.Orientation.Vertical)
        self.tabifyDockWidget(muscle, comparison)
        self.tabifyDockWidget(muscle, anomalies)
        self.tabifyDockWidget(muscle, tuning)
        muscle.raise_()
        self.resizeDocks([axis], [380], Qt.Orientation.Horizontal)
        if self.isVisible():
            # Wait for Qt to finish arranging the restored/tabbed docks.
            QTimer.singleShot(0, self._compact_right_docks)
        self.panels["video_sync"].hide()


def apply_style(app):
    app.setStyle("Fusion")
    app.setStyleSheet(f"""
        QMainWindow, QWidget {{ background: {BG}; color: {TEXT}; font: 9pt 'Segoe UI'; }}
        QDockWidget {{ background: {PANEL}; border: 1px solid #36495a; }}
        QDockWidget::title {{ background: #293e50; padding: 6px 8px; text-align: left; }}
        QMenuBar, QMenu, QStatusBar {{ background: #1d2e3d; color: {TEXT}; }}
        QMenu::item:selected {{ background: #365875; }}
        QPushButton, QToolButton, QComboBox, QDoubleSpinBox {{ background: #283b4e;
            color: {TEXT}; border: 1px solid #4a647a; border-radius: 4px; padding: 5px 8px; }}
        QPushButton:hover, QToolButton:hover {{ background: #37556a; }}
        QPushButton#playButton {{ background: #236b92; font-weight: bold; min-width: 86px; }}
        QLabel#rideTitle {{ font-size: 12pt; font-weight: bold; }}
        QLabel#position {{ font-weight: bold; }}
        QLabel#rangeInfo {{ color: {MUTED}; font-size: 8pt; }}
        QLabel#loopFeedback {{ color: #ffd080; background: #394638; padding: 4px 7px;
            border-radius: 3px; font-weight: bold; }}
        QLabel#badge {{ background: #3a4d5a; padding: 4px 7px; border-radius: 4px; }}
        QLabel#twinStatus {{ background: #304658; color: #e4edf6; padding: 5px 9px;
            border-radius: 4px; font-weight: bold; }}
        QLabel#researchNote {{ color: #c8aa72; font-size: 8pt; }}
        QLabel#syncInfo {{ color: {MUTED}; font-size: 8pt; }}
        QLabel#tuningFeedback {{ color: {TEXT}; }}
        QLabel#videoMessage {{ background: #101e2d; color: {MUTED}; font-size: 12pt; }}
        QSlider::groove:horizontal {{ height: 6px; background: #405367; border-radius: 3px; }}
        QSlider::handle:horizontal {{ background: {ACCENT}; width: 15px; margin: -5px 0; border-radius: 7px; }}
        QStatusBar {{ color: {MUTED}; }}
    """)
