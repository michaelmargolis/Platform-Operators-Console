# player_ui.py ver 6
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
    QSlider, QStackedWidget, QStackedLayout, QToolButton, QVBoxLayout, QWidget, QSizePolicy, QMenu, QToolBar, QStyle, QFrame,
)
from .video_backend import QVideoWidget, Qt6VideoBackend, VideoFrameMirror
from .candidate_workflow import CandidateReviewMixin
from .canonical_workflow import CanonicalVideoMixin
from .dynamic_workflow import DynamicProfileWorkflowMixin
from .panels import ResponsiveBars, TimelineMarkers, RangeJumpSlider, CueComparisonPanel, clock_label
from .anomaly_panel import AnomalyPanel
from .tuning_panel import MotionTuningPanel
from .angular_cues import MODE_LABELS, MODES
from .platform_selection import platform_choices, platform_metadata
from .theme import BG, PANEL, RAIL, TEXT, MUTED, ACCENT
from .task_workspace import PlotCanvas, plot_choices, TaskInspector



class CompactVideoStack(QStackedWidget):
    """Don't let a 3D video's native 640x480+ size lock the main window height."""

    def minimumSizeHint(self):
        return QSize(320, 120)

    def sizeHint(self):
        return QSize(800, 410)


class PlayerUiMixin(CanonicalVideoMixin, DynamicProfileWorkflowMixin, CandidateReviewMixin):
    """Qt6 UI builder; mixed into ReplayWindow before QMainWindow."""

    def _create_central(self):
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(7)
        # Retain controller-updated metadata widgets without a duplicate heading
        # row. The active ride/candidate is shown by the workspace selectors;
        # Twin status belongs in its menu, not in a permanent toolbar button.
        self.ride_label = QLabel("SIM PLAYER / OPEN A RIDE CAPTURE", self)
        self.ride_label.setObjectName("rideTitle")
        self.ride_label.hide()
        self.twin_status = QLabel("TWIN OFF", self)
        self.twin_status.setObjectName("twinStatus")
        self.twin_status.hide()
        self.info_badge = QLabel("OFFLINE", self)
        self.info_badge.setObjectName("badge")
        self.info_badge.hide()

        # Legacy detailed source/reanalysis controls are retained, but hidden
        # behind Advanced rather than permanently occupying the replay area.
        self.advanced_widget = QWidget()
        self.advanced_layout = QVBoxLayout(self.advanced_widget)
        self.advanced_layout.setContentsMargins(2, 2, 2, 2)
        self.advanced_layout.setSpacing(4)
        source_row = QHBoxLayout()
        source_row.setSpacing(7)
        self.open_capture_button = QPushButton("Open capture…")
        self.open_capture_button.clicked.connect(self.pick_capture)
        source_row.addWidget(self.open_capture_button)
        self.open_candidates_button = QPushButton("Review candidates…")
        self.open_candidates_button.clicked.connect(self.pick_review_candidates)
        source_row.addWidget(self.open_candidates_button)
        self.open_dynamic_button = QPushButton("Review dynamic profiles...")
        self.open_dynamic_button.clicked.connect(self.pick_dynamic_profiles)
        source_row.addWidget(self.open_dynamic_button)
        source_row.addSpacing(8)
        self.candidate_box = QComboBox()
        self.candidate_box.setObjectName("candidateABChoice")
        self.candidate_box.setFixedWidth(225)
        self.candidate_box.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.candidate_box.setMinimumContentsLength(12)
        self.candidate_box.setToolTip("Switch independent A/B copies, preserving video time and edits")
        self.candidate_box.currentIndexChanged.connect(self._candidate_slot_changed)
        source_row.addWidget(self.candidate_box)
        self.candidate_status_label = QLabel("")
        self.candidate_status_label.setObjectName("candidateReviewState")
        self.candidate_status_label.setMaximumWidth(120)
        source_row.addWidget(self.candidate_status_label, 1)
        self.reset_candidate_button = QPushButton("Reset")
        self.reset_candidate_button.clicked.connect(self.reset_review_candidate)
        source_row.addWidget(self.reset_candidate_button)
        self.save_review_button = QPushButton("Save variant(s)…")
        self.save_review_button.clicked.connect(self.save_review_variants)
        source_row.addWidget(self.save_review_button)
        self.save_review_session_button = QPushButton("Save review…")
        self.save_review_session_button.clicked.connect(self.save_review_session)
        source_row.addWidget(self.save_review_session_button)
        self.advanced_layout.addLayout(source_row)
        self._create_profile_strip(self.advanced_layout)

        self._create_workspace_bar(layout)
        layout.addWidget(self.advanced_widget)
        self.advanced_widget.hide()
        self._create_view_mode_row(layout)
        self.media_status_label = QLabel()
        self.media_status_label.setObjectName('canonicalMediaStatus')
        self.media_status_label.setWordWrap(True)
        self.media_status_label.hide()
        layout.addWidget(self.media_status_label)

        self.video_stack = CompactVideoStack()
        self.video_message = QLabel("Open a NoLimits2 telemetry CSV to begin\n\nOpen > Open capture")
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
        # Keep the proven native Qt video widget as the video-only page.  A
        # separate ordinary QWidget mirrors its decoded frames for Overlay so
        # Windows native-video stacking cannot hide the plot.
        self.video_stack.addWidget(self.video_screen)

        self.overlay_plot = PlotCanvas(overlay=True)
        self.overlay_plot.seek_requested.connect(self.seek_seconds)
        self.plot_canvas = PlotCanvas(overlay=False)
        self.plot_canvas.seek_requested.connect(self.seek_seconds)

        self.video_overlay_widget = QWidget()
        self.video_overlay_widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.video_overlay_layout = QStackedLayout(self.video_overlay_widget)
        self.video_overlay_layout.setContentsMargins(0, 0, 0, 0)
        self.video_overlay_layout.setStackingMode(QStackedLayout.StackingMode.StackAll)
        self.overlay_video_frame = VideoFrameMirror()
        self.video_overlay_layout.addWidget(self.overlay_video_frame)
        self.video_overlay_layout.addWidget(self.overlay_plot)
        self.video_stack.addWidget(self.video_overlay_widget)
        self.video_stack.addWidget(self.plot_canvas)

        self._create_profile_canvas()
        self.video_backend = Qt6VideoBackend(self.video_screen, self)
        self.video_screen.videoSink().videoFrameChanged.connect(self.overlay_video_frame.set_frame)
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
                name + " | " + platform_metadata(key)['platform_config_module'], Qt.ItemDataRole.ToolTipRole)
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
        self.advanced_layout.addLayout(experiment_row)

        self.setCentralWidget(central)


    def _create_workspace_bar(self, layout):
        # One full-width header: selectors left, Review/Tune/Validate right.
        # Open/Advanced/Twin are menu entries only, matching the agreed layout.
        self.workspace_toolbar = QToolBar('Workspace', self)
        self.workspace_toolbar.setObjectName('sim_player_workspace_toolbar')
        self.workspace_toolbar.setMovable(False)
        self.workspace_toolbar.setFloatable(False)
        self.workspace_toolbar.setAllowedAreas(Qt.ToolBarArea.TopToolBarArea)
        bar = QWidget()
        bar.setObjectName('workspaceSelectorRow')
        bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        row = QHBoxLayout(bar)
        row.setContentsMargins(10, 6, 10, 6)
        row.setSpacing(7)
        row.addWidget(QLabel('Platform'))
        self.workspace_platform_box = QComboBox()
        self.workspace_platform_box.setMinimumWidth(115)
        for key, name in platform_choices():
            self.workspace_platform_box.addItem(name, key)
        self.workspace_platform_box.setCurrentIndex(self.workspace_platform_box.findData(self.platform_key))
        self.workspace_platform_box.currentIndexChanged.connect(self._workspace_platform_changed)
        row.addWidget(self.workspace_platform_box, 2)
        row.addWidget(QLabel('Ride'))
        self.workspace_ride_box = QComboBox()
        self.workspace_ride_box.setMinimumWidth(130)
        self.workspace_ride_box.addItem('Open a ride…', 'open_capture')
        self.workspace_ride_box.addItem('Review dynamic profiles…', 'open_dynamic')
        self.workspace_ride_box.currentIndexChanged.connect(self._workspace_ride_changed)
        row.addWidget(self.workspace_ride_box, 3)
        row.addWidget(QLabel('Candidate'))
        self.workspace_candidate_box = QComboBox()
        self.workspace_candidate_box.setMinimumWidth(150)
        self.workspace_candidate_box.addItem('Current analysis', ('none', None))
        self.workspace_candidate_box.currentIndexChanged.connect(self._workspace_candidate_changed)
        row.addWidget(self.workspace_candidate_box, 3)
        row.addWidget(QLabel('Task'))
        self.workflow_box = QComboBox()
        self.workflow_box.setMinimumWidth(130)
        self.workflow_box.addItem('Inspect motion', 'inspect_motion')
        self.workflow_box.addItem('Events and limits', 'inspect_events')
        self.workflow_box.addItem('Candidate A/B', 'compare')
        self.workflow_box.currentIndexChanged.connect(self._workflow_changed)
        row.addWidget(self.workflow_box, 2)
        row.addSpacing(12)
        self.workflow_mode_group = QFrame()
        self.workflow_mode_group.setObjectName('workflowModeGroup')
        self.workflow_mode_group.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.workflow_mode_group.customContextMenuRequested.connect(self._show_workflow_group_context_menu)
        workflow_row = QHBoxLayout(self.workflow_mode_group)
        workflow_row.setContentsMargins(7, 4, 7, 4)
        workflow_row.setSpacing(5)
        workflow_label = QLabel('Workflow')
        workflow_label.setObjectName('workflowGroupLabel')
        workflow_row.addWidget(workflow_label)
        workflow_tooltips = {
            'review': 'Review: inspect motion, video, events and candidate differences. Right-click for a short workflow guide.',
            'tune': 'Tune: experiment offline with gains, neutral/cue and washout. Right-click for a short workflow guide.',
            'validate': 'Validate: check calculated kinematic limits, synchronisation and provenance. Right-click for a short workflow guide.',
        }
        self.mode_buttons = {}
        for mode, title in (("review", "Review"), ("tune", "Tune"), ("validate", "Validate")):
            button = QPushButton(title)
            button.setCheckable(True)
            button.setObjectName("taskModeButton")
            button.setToolTip(workflow_tooltips[mode])
            button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            button.customContextMenuRequested.connect(
                lambda pos, m=mode, b=button: self._show_workflow_context_menu(m, b, pos))
            button.clicked.connect(lambda checked, m=mode: self._task_mode_selected(m))
            self.mode_buttons[mode] = button
            workflow_row.addWidget(button)
        self.mode_buttons['review'].setChecked(True)
        row.addWidget(self.workflow_mode_group)
        self.workspace_help_button = QToolButton()
        self.workspace_help_button.setObjectName('workspaceHelpButton')
        self.workspace_help_button.setAccessibleName('Workspace help')
        self.workspace_help_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.workspace_help_button.setMinimumSize(30, 30)
        row.addWidget(self.workspace_help_button)
        # The action is attached in _create_menus, so all entry points share it.
        self.workspace_toolbar.addWidget(bar)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, self.workspace_toolbar)

    def _create_view_mode_row(self, layout):
        row = QHBoxLayout()
        row.setSpacing(7)
        self.view_mode = 'video'
        self.video_mode_button = QPushButton('Video')
        self.video_mode_button.setCheckable(True)
        self.video_mode_button.setChecked(True)
        self.video_mode_button.clicked.connect(lambda: self._view_mode_selected('video'))
        row.addWidget(self.video_mode_button)
        self.plot_mode_button = QPushButton('Plot')
        self.plot_mode_button.setCheckable(True)
        self.plot_mode_button.clicked.connect(lambda: self._view_mode_selected('plot'))
        row.addWidget(self.plot_mode_button)
        self.overlay_mode_button = QPushButton('Overlay')
        self.overlay_mode_button.setCheckable(True)
        self.overlay_mode_button.clicked.connect(lambda: self._view_mode_selected('overlay'))
        row.addWidget(self.overlay_mode_button)
        row.addSpacing(10)
        row.addWidget(QLabel('Plot'))
        self.plot_choice_box = QComboBox()
        for key, title in plot_choices():
            self.plot_choice_box.addItem(title, key)
        self.plot_choice_box.setCurrentIndex(0)
        self.plot_choice_box.currentIndexChanged.connect(self._plot_choice_changed)
        row.addWidget(self.plot_choice_box)
        row.addStretch(1)
        layout.addLayout(row)

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
        self.workspace_inspector = TaskInspector()
        inspector = self._add_dock("inspector", "Inspector : Review", self.workspace_inspector)
        inspector.setMinimumWidth(350)
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
        self.workspace_inspector.seek_requested.connect(self.seek_seconds)
        self.workspace_inspector.gain_commit_requested.connect(self._workspace_gain_commit)
        self.workspace_inspector.neutral_commit_requested.connect(self._workspace_neutral_commit)
        self.workspace_inspector.cue_commit_requested.connect(self._workspace_cue_commit)
        self.workspace_inspector.washout_commit_requested.connect(self._workspace_washout_commit)
        self.workspace_inspector.open_advanced_requested.connect(self._open_advanced_dock)
        self.workspace_inspector.save_notes_requested.connect(self._save_workspace_notes)
        self.workspace_inspector.examine_requested.connect(self._examine_anomaly)
        self.workspace_inspector.baseline_requested.connect(self._set_baseline)
        self.workspace_inspector.restore_baseline_requested.connect(self._restore_baseline)
        self.workspace_inspector.save_experiment_requested.connect(self._save_experiment)
        self.workspace_inspector.load_experiment_requested.connect(self._load_experiment)
        self.splitDockWidget(axis, lengths, Qt.Orientation.Vertical)
        # Reuse the lower-right dock slot; comparison can be shown, hidden,
        # floated or resized without crowding the video player.
        self.tabifyDockWidget(lengths, inspector)
        self.tabifyDockWidget(lengths, comparison)
        self.tabifyDockWidget(lengths, anomalies)
        self.tabifyDockWidget(lengths, tuning)
        inspector.raise_()
        self._create_profile_dock()
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
        # Keep one QAction for each command. Menu, icon and shortcuts cannot
        # diverge into different loaders/help windows or lose their enabled state.
        file_menu = self.menuBar().addMenu("File")
        self.open_menu = self.menuBar().addMenu("Open")
        view = self.menuBar().addMenu("View")
        self.advanced_menu = self.menuBar().addMenu("Advanced")
        self.twin_menu = self.menuBar().addMenu("Twin")
        help_menu = self.menuBar().addMenu("Help")

        open_action = QAction("Open capture...", self)
        open_action.setShortcut(QKeySequence(QKeySequence.StandardKey.Open))
        open_action.triggered.connect(self.pick_capture)
        self.open_menu.addAction(open_action)
        review_action = QAction("Review optimiser candidates...", self)
        review_action.triggered.connect(self.pick_review_candidates)
        self.open_menu.addAction(review_action)
        dynamic_open = QAction("Review dynamic motion profiles...", self)
        dynamic_open.triggered.connect(self.pick_dynamic_profiles)
        self.open_menu.addAction(dynamic_open)
        self.open_menu.addSeparator()
        self.open_menu.addAction("Open saved candidate review...", self.open_review_session)
        self.open_menu.addAction("Open saved dynamic review...", self.open_dynamic_review)
        self.open_menu.addSeparator()
        self.open_menu.addAction("Ride reference video...", self.manage_canonical_video)

        self.save_review_session_action = QAction("Save candidate review...", self)
        self.save_review_session_action.triggered.connect(self.save_review_session)
        self.save_review_session_action.setEnabled(False)
        file_menu.addAction(self.save_review_session_action)
        self.save_dynamic_action = QAction("Save dynamic review...", self)
        self.save_dynamic_action.triggered.connect(self.save_dynamic_review)
        self.save_dynamic_action.setEnabled(False)
        file_menu.addAction(self.save_dynamic_action)
        file_menu.addSeparator()
        self.save_variant_action = QAction("Save current settings as a ride variant...", self)
        self.save_variant_action.setToolTip("Save a new named candidate; existing entries are never overwritten")
        self.save_variant_action.setEnabled(False)
        self.save_variant_action.triggered.connect(self.save_variant_entry)
        file_menu.addAction(self.save_variant_action)
        file_menu.addSeparator()
        settings_action = QAction("Settings…", self)
        settings_action.triggered.connect(self.show_settings)
        file_menu.addAction(settings_action)
        file_menu.addSeparator()
        file_menu.addAction("Exit", self.close)

        view.addAction(self.panels['inspector'].toggleViewAction())
        self.raw_axes_action = QAction("Show original NL2 CSV transform instead of processed", self)
        self.raw_axes_action.setCheckable(True)
        self.raw_axes_action.setChecked(False)
        self.raw_axes_action.toggled.connect(self._display_current)
        view.addAction(self.raw_axes_action)
        self.canonical_video_action = QAction('Use ride reference video', self)
        self.canonical_video_action.setCheckable(True)
        self.canonical_video_action.setChecked(self._canonical_enabled)
        self.canonical_video_action.toggled.connect(self._canonical_toggled)
        view.addAction(self.canonical_video_action)
        view.addSeparator()
        view.addAction("Restore task workspace", self.restore_default_layout)
        self.advanced_controls_action = QAction("Show advanced controls", self)
        self.advanced_controls_action.setCheckable(True)
        self.advanced_controls_action.setChecked(False)
        self.advanced_controls_action.toggled.connect(self._toggle_advanced_controls)
        self.advanced_menu.addAction(self.advanced_controls_action)
        detailed_panels = self.advanced_menu.addMenu("Panels")
        for key, dock in self.panels.items():
            if key != 'inspector':
                detailed_panels.addAction(dock.toggleViewAction())
        self.advanced_menu.addAction("Show all advanced panels", self.show_all_panels)
        self.advanced_menu.addAction("Restore task workspace", self.restore_default_layout)
        # The existing bottom Panels button remains a compact convenience.
        panel_menu = QMenu(self)
        for dock in self.panels.values():
            panel_menu.addAction(dock.toggleViewAction())
        panel_menu.addSeparator()
        panel_menu.addAction("Restore task workspace", self.restore_default_layout)
        self.panel_button.setMenu(panel_menu)

        self.twin_state_action = self.twin_menu.addAction("Status: TWIN OFF")
        self.twin_state_action.setEnabled(False)
        self.twin_destination_action = self.twin_menu.addAction("Destination: not set")
        self.twin_destination_action.setEnabled(False)
        self.twin_menu.addSeparator()
        twin_settings_action = QAction("Visualizer settings...", self)
        twin_settings_action.triggered.connect(self.show_settings)
        self.twin_menu.addAction(twin_settings_action)

        self.workspace_help_action = QAction("Workspace help...", self)
        self.workspace_help_action.setObjectName('workspaceHelpAction')
        self.workspace_help_action.setShortcut(QKeySequence('F1'))
        self.workspace_help_action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        self.workspace_help_action.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation))
        self.workspace_help_action.setToolTip('Tabbed workspace help (F1)')
        self.workspace_help_action.triggered.connect(self.show_workspace_help)
        help_menu.addAction(self.workspace_help_action)
        self.workspace_help_button.setDefaultAction(self.workspace_help_action)
        help_menu.addSeparator()
        help_menu.addAction("About sim_player", self.about)

    def _show_workflow_context_menu(self, mode, source, pos):
        title = {'review': 'Review', 'tune': 'Tune', 'validate': 'Validate'}.get(mode, 'Workflow')
        menu = QMenu(source)
        help_action = menu.addAction(f'Help about {title}')
        help_action.triggered.connect(lambda _checked=False, m=mode: self.show_workflow_help(m))
        workspace_action = getattr(self, 'workspace_help_action', None)
        if workspace_action is not None:
            menu.addSeparator()
            menu.addAction(workspace_action)
        menu.exec(source.mapToGlobal(pos))

    def _show_workflow_group_context_menu(self, pos):
        self._show_workflow_context_menu(
            getattr(self, 'task_mode', 'review'), self.workflow_mode_group, pos)

    def show_workflow_help(self, mode):
        """Open concise help for one workflow without changing replay state."""
        from .workspace_help import WorkflowHelpDialog
        dialogs = getattr(self, '_workflow_help_dialogs', None)
        if dialogs is None:
            dialogs = {}
            self._workflow_help_dialogs = dialogs
        dialog = dialogs.get(mode)
        if dialog is None:
            dialog = WorkflowHelpDialog(mode, self)
            dialogs[mode] = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def show_workspace_help(self, *_args):
        """Menu, information icon and F1 all use the same modeless dialog."""
        from .workspace_help import WorkspaceHelpDialog
        dialog = getattr(self, '_workspace_help_dialog', None)
        if dialog is None:
            dialog = WorkspaceHelpDialog(self)
            self._workspace_help_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def show_all_panels(self):
        for dock in self.panels.values():
            dock.show()

    def restore_default_layout(self):
        """Use one inspector, not the legacy stack of independent docks."""
        self._activate_task_layout()

    def _activate_task_layout(self):
        inspector = self.panels['inspector']
        self._toggle_advanced_controls(False)
        for key, dock in self.panels.items():
            if key != 'inspector':
                dock.hide()
        inspector.setFloating(False)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, inspector)
        inspector.show()
        inspector.raise_()
        if self.isVisible():
            self.resizeDocks([inspector], [395], Qt.Orientation.Horizontal)

    def _toggle_advanced_controls(self, visible):
        self.advanced_widget.setVisible(bool(visible))
        action = getattr(self, 'advanced_controls_action', None)
        if action is not None and action.isChecked() != bool(visible):
            action.blockSignals(True)
            action.setChecked(bool(visible))
            action.blockSignals(False)



def apply_style(app):
    app.setStyle("Fusion")
    app.setStyleSheet(f"""
        QMainWindow, QWidget {{ background: {BG}; color: {TEXT}; font: 9pt 'Segoe UI'; }}
        QToolBar {{ background: #1d2e3d; border: none; spacing: 0px; }}
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
        QFrame#workflowModeGroup {{ background: #182a3a; border: 1px solid #365066; border-radius: 6px; }}
        QLabel#workflowGroupLabel {{ color: #9eb2c4; font-size: 8pt; font-weight: bold; padding: 0 3px 0 1px; }}
        QPushButton#taskModeButton {{ min-width: 62px; background: #25394b; border-color: #466279; }}
        QPushButton#taskModeButton:hover {{ background: #315069; border-color: #6688a2; }}
        QPushButton#taskModeButton:checked {{ background: #236b92; font-weight: bold; border-color: #5cb3e5; }}
        QFrame#inspectorSurface {{ background: #132536; border: 1px solid #294156; border-radius: 7px; }}
        QFrame#inspectorPanel {{ background: #14293b; border: 1px solid #2b465d; border-radius: 5px; }}
        QToolButton#inspectorSection {{ background: #223b50; border: none;
            border-right: 1px solid #426078; border-bottom: 1px solid #426078;
            border-top-left-radius: 4px; border-bottom-right-radius: 4px;
            text-align: left; font-weight: bold; padding: 7px; }}
        QToolButton#inspectorSection:hover {{ background: #2b4961; }}
        QWidget#inspectorCardBody {{ background: transparent; border: none; }}
        QLabel#inspectorContextSummary {{ color: #a9bac8; padding: 0 7px; }}
        QTableWidget, QListWidget, QLineEdit {{ background: #122639; color: {TEXT};
            border: 1px solid #344f67; gridline-color: #284258; }}
        QHeaderView::section {{ background: #233c52; color: {TEXT}; padding: 4px; border: 0; }}
        QStatusBar {{ color: {MUTED}; }}
    """)
