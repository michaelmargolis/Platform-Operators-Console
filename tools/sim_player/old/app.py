"""sim_player: synchronised NoLimits2 video + POC telemetry replay.

Install in the POC repository as tools/sim_player/. Run from the root:
    python -m tools.sim_player [captures/nl2_ride_study/RIDE.csv]

Standalone OFFLINE research utility. No NL2 connection, no SHS, no Festo,
no platform activation and no Digital Twin UDP in this first release.
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path
import sys
import time

from PyQt6.QtCore import (Qt, QSettings, QThread, pyqtSignal, QRectF,
                          QPointF, QTimer, QSortFilterProxyModel)
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QKeySequence, QAction
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QDockWidget, QFileDialog, QDoubleSpinBox,
    QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton, QSlider,
    QStackedWidget, QToolButton, QVBoxLayout, QWidget,
)

from .video_backend import QVideoWidget, Qt6VideoBackend

from .recording import AXES, RideRecording, load_recording, write_saved_calibration
from .video_sync import VideoSeekGuard, is_ride_telemetry_csv

BG = "#111a25"
PANEL = "#1b2938"
RAIL = "#314459"
TEXT = "#e6ecf3"
MUTED = "#a9bad0"
ACCENT = "#5ca9d5"
GREEN = "#67bdaf"
ORANGE = "#edac6b"


def clock_label(seconds):
    seconds = max(0.0, seconds)
    return f"{int(seconds // 60):02d}:{seconds % 60:05.2f}"


class LoadWorker(QThread):
    loaded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, csv_path, parent=None):
        super().__init__(parent)
        self.csv_path = csv_path

    def run(self):
        try:
            result = load_recording(self.csv_path)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        else:
            self.loaded.emit(result)


class ResponsiveBars(QWidget):
    """Fast and resize-safe 6-channel bars; later strip charts are separate docks."""

    def __init__(self, signed, parent=None):
        super().__init__(parent)
        self.signed = signed
        self.values = (0.0,) * 6
        self.lo, self.hi = 750.0, 1000.0
        self.axis_context = "PROCESSED"
        self.labels = AXES if signed else tuple(f"M{i+1}" for i in range(6))
        self.setMinimumSize(245, 190)

    def update_values(self, values, lo=None, hi=None):
        self.values = tuple(values)
        if lo is not None:
            self.lo = float(lo)
        if hi is not None:
            self.hi = float(hi)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width, height = self.width(), self.height()
        painter.fillRect(self.rect(), QColor(PANEL))
        painter.setFont(QFont("Segoe UI", 8))
        painter.setPen(QColor(MUTED))
        heading = (self.axis_context + " TRANSFORM   -100% to +100%") if self.signed else (
            f"MODELED MUSCLE LENGTH   {self.lo:.0f}–{self.hi:.0f} mm")
        if width < 330:
            heading = (self.axis_context + "  6DOF (%)") if self.signed else "MUSCLE LENGTHS (mm)"
        painter.drawText(10, 6, width - 20, 22, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, heading)
        left = 72
        right = 76 if not self.signed else 67
        bar_width = max(30, width - left - right - 8)
        top, bottom = 30, 5
        row_height = max(22, (height - top - bottom) / 6)
        bar_height = max(9, min(17, row_height * .43))
        for i, (label, value) in enumerate(zip(self.labels, self.values)):
            y = top + i * row_height
            by = y + (row_height - bar_height) / 2
            bar = QRectF(left, by, bar_width, bar_height)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(RAIL))
            painter.drawRoundedRect(bar, 3, 3)
            if self.signed:
                clamped = max(-1.0, min(1.0, float(value)))
                centre = bar.center().x()
                half = bar_width / 2
                bx = centre + min(0.0, clamped) * half
                fill = QRectF(bx, by, max(0.9, abs(clamped) * half), bar_height)
                painter.setBrush(QColor(ORANGE if i in (3, 4) else ACCENT))
                painter.drawRoundedRect(fill, 2, 2)
                painter.setPen(QPen(QColor("#d6e4f2"), 1))
                painter.drawLine(QPointF(centre, by - 2), QPointF(centre, by + bar_height + 2))
                display = f"{float(value) * 100:+.0f}%"
            else:
                fraction = max(0, min(1, (float(value) - self.lo) / max(1, self.hi - self.lo)))
                painter.setBrush(QColor(GREEN))
                painter.drawRoundedRect(QRectF(left, by, max(.9, bar_width * fraction), bar_height), 2, 2)
                display = f"{value:.0f} mm"
            painter.setPen(QColor(TEXT))
            painter.drawText(9, int(y), 54, int(row_height), Qt.AlignmentFlag.AlignVCenter, label)
            painter.drawText(int(left + bar_width + 7), int(y), right, int(row_height),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, display)
        painter.end()


class TimelineMarkers(QWidget):
    seek_requested = pyqtSignal(float)
    inspected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.duration = 1.0
        self.current = 0.0
        self.events = ()
        self.setMinimumHeight(42)
        self.setMaximumHeight(52)
        self.setMouseTracking(True)

    def set_recording(self, events, duration):
        self.events = events
        self.duration = max(0.01, duration)
        self.update()

    def set_current(self, seconds):
        self.current = seconds
        self.update()

    def x_at(self, seconds):
        return 18 + (self.width() - 36) * max(0.0, min(1.0, seconds / self.duration))

    def closest_event(self, x):
        if not self.events:
            return None
        event = min(self.events, key=lambda e: abs(self.x_at(e.seconds) - x))
        return event if abs(self.x_at(event.seconds) - x) < 12 else None

    def mouseMoveEvent(self, event):
        chosen = self.closest_event(event.position().x())
        if chosen:
            self.setToolTip(f"{chosen.name} — {clock_label(chosen.seconds)}\n{chosen.details}")
        else:
            self.setToolTip("Click to seek; hover over an event for details")

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        chosen = self.closest_event(event.position().x())
        if chosen is not None:
            self.seek_requested.emit(chosen.seconds)
            self.inspected.emit(f"{chosen.name} at {clock_label(chosen.seconds)}")
        else:
            ratio = (event.position().x() - 18) / max(1, self.width() - 36)
            self.seek_requested.emit(self.duration * max(0.0, min(1.0, ratio)))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        painter.setPen(QPen(QColor("#52677a"), 2))
        painter.drawLine(18, 18, w - 18, 18)
        # Sparse, non-overlapping captions. Detailed event names live in hover tooltips.
        last_label_end = -1000
        for marker in self.events:
            x = self.x_at(marker.seconds)
            color = QColor(marker.color)
            painter.setPen(QPen(color, 1.5))
            painter.drawLine(int(x), 10, int(x), 27)
            painter.setBrush(color)
            painter.drawEllipse(QRectF(x - 2.5, 15.5, 5, 5))
            if x - 32 > last_label_end and x > 45 and x < w - 45:
                painter.setFont(QFont("Segoe UI", 7))
                painter.setPen(QColor(MUTED))
                short = marker.name.replace(" request", "").replace(" train", "")
                painter.drawText(int(x - 36), 29, 72, 16, Qt.AlignmentFlag.AlignHCenter, short)
                last_label_end = x + 40
        x = self.x_at(self.current)
        painter.setPen(QPen(QColor("#eef7ff"), 2))
        painter.drawLine(int(x), 3, int(x), 30)
        painter.end()


class JumpSlider(QSlider):
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            range_size = self.maximum() - self.minimum()
            if range_size > 0:
                proportion = max(0, min(1, (event.position().x() - 9) / max(1, self.width() - 18)))
                self.setValue(round(self.minimum() + proportion * range_size))
                event.accept()
        super().mousePressEvent(event)


class RideCsvDialogFilter(QSortFilterProxyModel):
    """Hide state-event CSV sidecars while preserving the folder tree."""

    def filterAcceptsRow(self, source_row, source_parent):
        source = self.sourceModel()
        item = source.index(source_row, 0, source_parent)
        # The file dialog already filters for .csv; we only hide event sidecars.
        name = str(source.data(item, Qt.ItemDataRole.DisplayRole) or "")
        return not name.casefold().endswith("_state_events.csv")


class ReplayWindow(QMainWindow):
    def __init__(self, initial_path=None):
        super().__init__()
        self.recording: RideRecording | None = None
        self.current_s = 0.0
        self.playing = False
        self.speed_ratio = 1.0
        self.anchor_time = time.monotonic()
        self.anchor_ride_s = 0.0
        self.was_playing_before_scrub = False
        self.video_seek_guard = VideoSeekGuard()
        self.scrub_seek_timer = QTimer(self)
        self.scrub_seek_timer.setSingleShot(True)
        # Throttle video seeks during dragging; the telemetry bars still update
        # immediately. FFmpeg then decodes the requested frame while paused.
        self.scrub_seek_timer.setInterval(125)
        self.scrub_seek_timer.timeout.connect(lambda: self._sync_video(force=True))
        self.panels = {}
        self.worker = None
        self.loading = False
        self.has_video = False
        self.video_offset_s = 0.0
        self.settings = QSettings("MDXSAT", "sim_player")
        self.setWindowTitle("MDX sim_player — No recording loaded")
        self.resize(1320, 860)
        self.setDockOptions(QMainWindow.DockOption.AllowNestedDocks | QMainWindow.DockOption.AllowTabbedDocks |
                            QMainWindow.DockOption.AnimatedDocks)
        self._create_central()
        self._create_docks()
        self._create_menus()
        self.statusBar().showMessage("Offline replay • no NoLimits2, SHS, Festo or UDP connections")
        geometry = self.settings.value("geometry")
        layout = self.settings.value("dock_layout")
        if geometry is not None:
            self.restoreGeometry(geometry)
        if layout is None or not self.restoreState(layout):
            self.restore_default_layout()
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        if initial_path:
            QTimer.singleShot(1, lambda: self.open_capture(Path(initial_path)))

    def _create_central(self):
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(7)
        heading = QHBoxLayout()
        self.ride_label = QLabel("SIM PLAYER   /   OPEN A RIDE CAPTURE")
        self.ride_label.setObjectName("rideTitle")
        heading.addWidget(self.ride_label, 1)
        self.info_badge = QLabel("OFFLINE")
        self.info_badge.setObjectName("badge")
        heading.addWidget(self.info_badge)
        layout.addLayout(heading)

        self.video_stack = QStackedWidget()
        self.video_message = QLabel("Open a NoLimits2 telemetry CSV to begin\n\nFile → Open capture")
        self.video_message.setObjectName("videoMessage")
        self.video_message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_message.setWordWrap(True)
        self.video_stack.addWidget(self.video_message)
        self.video_screen = QVideoWidget()
        self.video_stack.addWidget(self.video_screen)
        self.video_backend = Qt6VideoBackend(self.video_screen, self)
        self.video_backend.error.connect(self._video_error)
        self.video_stack.setMinimumSize(440, 250)
        layout.addWidget(self.video_stack, 1)

        markers_row = QHBoxLayout()
        markers_row.addWidget(QLabel("RIDE EVENTS  •  click to seek; hover for details"))
        markers_row.addStretch(1)
        self.position_label = QLabel("00:00.00 / 00:00.00")
        self.position_label.setObjectName("position")
        markers_row.addWidget(self.position_label)
        layout.addLayout(markers_row)
        self.markers = TimelineMarkers()
        self.markers.seek_requested.connect(self.seek_seconds)
        self.markers.inspected.connect(lambda s: self.statusBar().showMessage(s, 5000))
        layout.addWidget(self.markers)

        self.slider = JumpSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 1)
        self.slider.setValue(0)
        self.slider.setEnabled(False)
        self.slider.valueChanged.connect(lambda ms: self.seek_seconds(ms / 1000))
        self.slider.sliderPressed.connect(self._scrub_started)
        self.slider.sliderReleased.connect(self._scrub_finished)
        layout.addWidget(self.slider)
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
        self.panel_button = QToolButton()
        self.panel_button.setText("Panels ▾")
        self.panel_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        controls.addWidget(self.panel_button)
        layout.addLayout(controls)

        video_row = QHBoxLayout()
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
        layout.addLayout(video_row)

        self.setCentralWidget(central)

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
        self.splitDockWidget(axis, lengths, Qt.Orientation.Vertical)

    def _create_menus(self):
        file_menu = self.menuBar().addMenu("File")
        open_action = QAction("Open capture…", self)
        open_action.setShortcut(QKeySequence(QKeySequence.StandardKey.Open))
        open_action.triggered.connect(self.pick_capture)
        file_menu.addAction(open_action)
        file_menu.addSeparator()
        file_menu.addAction("Exit", self.close)
        view = self.menuBar().addMenu("View")
        for dock in self.panels.values():
            view.addAction(dock.toggleViewAction())
        view.addSeparator()
        self.raw_axes_action = QAction("Show raw NL2 transform instead of processed", self)
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
        axis, muscle = self.panels["axes"], self.panels["muscles"]
        for dock in (axis, muscle):
            dock.setFloating(False)
            dock.show()
            self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)
        self.splitDockWidget(axis, muscle, Qt.Orientation.Vertical)
        self.resizeDocks([axis, muscle], [310, 310], Qt.Orientation.Vertical)
        self.resizeDocks([axis], [380], Qt.Orientation.Horizontal)

    def about(self):
        QMessageBox.information(self, "About sim_player",
            "sim_player v5 — Qt6/FFmpeg video + telemetry replay.\n\n"
            "Motion lengths use existing POC offline analysis; no new kinematics.\n"
            "Video timing requires empirical calibration.\n"
            "No SHS, NoLimits2 network, Festo or Digital Twin communication.\n\n"
            "Later docks can show strip charts, clearance and tension feasibility.")

    def pick_capture(self):
        directory = str(self.settings.value("last_directory", str(Path.cwd() / "captures")))
        # Set DontUseNativeDialog *before* setting other properties, as Qt
        # requires, so the proxy filename filter is active on Windows.
        dialog = QFileDialog(self)
        dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
        dialog.setWindowTitle("Open ride telemetry CSV")
        dialog.setDirectory(directory)
        dialog.setNameFilter("Ride telemetry CSV (*.csv)")
        dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
        dialog.setProxyModel(RideCsvDialogFilter(dialog))
        if dialog.exec():
            selected = dialog.selectedFiles()
            if selected and is_ride_telemetry_csv(Path(selected[0]).name):
                self.open_capture(Path(selected[0]))

    def open_capture(self, path):
        path = Path(path)
        if not is_ride_telemetry_csv(path.name):
            QMessageBox.warning(self, "Invalid capture", "Select a ride telemetry CSV, not an event log.")
            return
        if self.loading or (self.worker is not None and self.worker.isRunning()):
            self.statusBar().showMessage("Capture loading already in progress", 3000)
            return
        self._set_playing(False)
        self.scrub_seek_timer.stop()
        self.video_seek_guard.reset()
        self.video_backend.unload()
        self.has_video = False
        self.loading = True
        self.ride_label.setText("LOADING  /  " + path.name)
        self.info_badge.setText("LOADING")
        self.statusBar().showMessage("Loading telemetry and calculating POC muscle lengths…")
        self.worker = LoadWorker(path, self)
        self.worker.loaded.connect(self._load_success)
        self.worker.failed.connect(self._load_failure)
        self.worker.finished.connect(self._worker_finished)
        self.worker.start()

    def _load_success(self, recording):
        self.loading = False
        self.recording = recording
        self.settings.setValue("last_directory", str(recording.csv_path.parent))
        self.ride_label.setText(recording.ride.upper() + "  /  REPLAY")
        self.setWindowTitle(f"MDX sim_player — {recording.ride} — {recording.csv_path.name}")
        self.info_badge.setText(f"{len(recording.frames):,} SAMPLES")
        self.markers.set_recording(recording.events, recording.duration)
        self.slider.setEnabled(True)
        self.slider.setRange(0, int(round(recording.duration * 1000)))
        self.offset_box.blockSignals(True)
        self.offset_box.setValue(recording.video_offset_s)
        self.offset_box.blockSignals(False)
        self.offset_box.setEnabled(bool(recording.video_path))
        self.save_offset_button.setEnabled(bool(recording.video_path))
        self.video_offset_s = recording.video_offset_s
        self.sync_label.setText(recording.sync_source)
        self.sync_label.setToolTip(recording.sync_source + ("\n" + recording.diagnostics if recording.diagnostics else ""))
        if recording.video_path is not None:
            self.has_video = True
            self.video_stack.setCurrentWidget(self.video_screen)
            # A single initial decoder warm-up freezes after the first valid
            # frame; Qt6 FFmpeg then updates paused seeks without play pulses.
            self.video_backend.load(recording.video_path, initial_ms=self._video_target_ms())
        else:
            self.has_video = False
            self.video_backend.unload()
            self.video_stack.setCurrentWidget(self.video_message)
            self.video_message.setText("No matching video recording\nTelemetry, muscle lengths and markers are available")
        self.axis_bars.update_values((0,) * 6)
        self.muscle_bars.update_values((0,) * 6, recording.min_length_mm, recording.max_length_mm)
        self.seek_seconds(0, force_video=True)
        notes = "; ".join(filter(None, (recording.diagnostics,)))
        self.statusBar().showMessage(
            f"{recording.analysis_settings} | {len(recording.events)} markers" + (" | " + notes if notes else ""))

    def _load_failure(self, message):
        self.loading = False
        self.info_badge.setText("LOAD FAILED")
        self.statusBar().showMessage("Could not load capture: " + message)
        QMessageBox.warning(self, "Cannot open capture", message)

    def _worker_finished(self):
        # Retain the QThread object until its actual finished signal. Dropping
        # the last Python reference in loaded() can crash Qt on Windows.
        worker = self.sender()
        if worker is self.worker:
            self.worker = None
        if worker is not None:
            worker.deleteLater()

    def _video_error(self, message):
        if self.has_video:
            self.statusBar().showMessage("Qt6 FFmpeg video error: " + message)

    def current_sample(self):
        return self.recording.at(self.current_s) if self.recording else None

    def _display_current(self):
        recording = self.recording
        if recording is None:
            return
        frame = recording.at(self.current_s)
        show_raw = self.raw_axes_action.isChecked()
        self.axis_bars.axis_context = "RAW NL2" if show_raw else "PROCESSED"
        self.panels["axes"].setWindowTitle("Transform · 6DOF (raw NL2)" if show_raw else
                                            "Transform · 6DOF (processed)")
        self.axis_bars.update_values(frame.input_axes if show_raw else frame.processed_axes)
        self.muscle_bars.update_values(frame.muscle_lengths_mm, recording.min_length_mm,
                                       recording.max_length_mm)
        self.markers.set_current(self.current_s)
        self.position_label.setText(clock_label(self.current_s) + " / " + clock_label(recording.duration))
        self.slider.blockSignals(True)
        self.slider.setValue(round(self.current_s * 1000))
        self.slider.blockSignals(False)
        self.info_badge.setText("WORKSPACE LIMITED" if frame.workspace_limited else
                                f"{len(recording.frames):,} SAMPLES")

    def _video_target_ms(self):
        return max(0, round((self.current_s + self.video_offset_s) * 1000))

    def _sync_video(self, force=False):
        if not self.has_video:
            return
        target_ms = self._video_target_ms()
        difference_ms = self.video_backend.position() - target_ms
        now = time.monotonic()
        if force:
            # Qt 6 FFmpeg reliably decodes video at a new timestamp while
            # PausedState. Never toggle play/pause for an explicit seek.
            self.video_seek_guard.user_seek(now)
            self.video_backend.seek(target_ms)
        elif self.video_seek_guard.should_correct_drift(now, difference_ms):
            # Continuous play requires no seeks unless a large persistent
            # drift remains. A normal frame-rate timing difference is ignored.
            self.video_backend.seek(target_ms)

    def seek_seconds(self, seconds, *, force_video=True):
        if self.recording is None:
            return
        self.current_s = max(0.0, min(self.recording.duration, float(seconds)))
        self._display_current()
        if self.slider.isSliderDown() and force_video:
            # Update video at up to 8 seeks per second while dragging.
            # Final slider release always seeks to the exact requested time.
            if not self.scrub_seek_timer.isActive():
                self.scrub_seek_timer.start()
        else:
            self.scrub_seek_timer.stop()
            self._sync_video(force=force_video)
        if self.playing:
            self.anchor_time = time.monotonic()
            self.anchor_ride_s = self.current_s

    def step_frame(self, direction):
        if self.recording is None:
            return
        self._set_playing(False)
        i = self.recording.nearest_index(self.current_s)
        target = max(0, min(len(self.recording.frames) - 1, i + direction))
        self.seek_seconds(self.recording.times[target])

    def _scrub_started(self):
        self.was_playing_before_scrub = self.playing
        self._set_playing(False)

    def _scrub_finished(self):
        self.scrub_seek_timer.stop()
        self._sync_video(force=True)
        if self.was_playing_before_scrub:
            self._set_playing(True)
        self.was_playing_before_scrub = False

    def _set_playing(self, active):
        if active and self.recording is None:
            return
        if active and self.current_s >= self.recording.duration:
            self.seek_seconds(0)
        self.playing = active
        self.anchor_ride_s = self.current_s
        self.anchor_time = time.monotonic()
        self.play_button.setText("❚❚  Pause" if active else "▶  Play")
        if self.has_video:
            if active:
                # No unconditional seek on Play: simply resume the decoder.
                drift = self.video_backend.position() - self._video_target_ms()
                if self.video_seek_guard.should_seek_on_resume(drift, time.monotonic()):
                    self._sync_video(force=True)
                self.video_seek_guard.resumed(time.monotonic())
                self.video_backend.set_speed(self.speed_ratio)
                self.video_backend.play()
            else:
                self.video_backend.pause()
                self.video_backend.set_speed(self.speed_ratio)

    def toggle_play(self):
        self._set_playing(not self.playing)

    def _speed_changed(self):
        self.speed_ratio = float(self.speed_box.currentData())
        self.anchor_time = time.monotonic()
        self.anchor_ride_s = self.current_s
        self.video_backend.set_speed(self.speed_ratio)

    def _offset_changed(self, seconds):
        self.video_offset_s = float(seconds)
        self._sync_video(force=True)

    def save_sync_offset(self):
        if self.recording is None or not self.has_video:
            return
        try:
            path = write_saved_calibration(self.recording.csv_path, self.video_offset_s)
        except OSError as exc:
            QMessageBox.warning(self, "Save calibration failed", str(exc))
        else:
            self.sync_label.setText("Saved calibration (" + path.name + ")")
            self.statusBar().showMessage(f"Saved video synchronization: {path}", 5000)

    def _tick(self):
        if not self.playing or self.recording is None or self.slider.isSliderDown():
            return
        target = self.anchor_ride_s + (time.monotonic() - self.anchor_time) * self.speed_ratio
        if target >= self.recording.duration:
            self.current_s = self.recording.duration
            self._display_current()
            self._set_playing(False)
            return
        self.current_s = target
        self._display_current()
        self._sync_video()

    def closeEvent(self, event):
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.information(self, "Loading", "A capture is still loading. Close after it finishes.")
            event.ignore()
            return
        self._set_playing(False)
        self.video_backend.unload()
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("dock_layout", self.saveState())
        super().closeEvent(event)


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
        QLabel#badge {{ background: #3a4d5a; padding: 4px 7px; border-radius: 4px; }}
        QLabel#syncInfo {{ color: {MUTED}; font-size: 8pt; }}
        QLabel#videoMessage {{ background: #101e2d; color: {MUTED}; font-size: 12pt; }}
        QSlider::groove:horizontal {{ height: 6px; background: #405367; border-radius: 3px; }}
        QSlider::handle:horizontal {{ background: {ACCENT}; width: 15px; margin: -5px 0; border-radius: 7px; }}
        QStatusBar {{ color: {MUTED}; }}
    """)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline NoLimits2 video + telemetry replay")
    parser.add_argument("csv", nargs="?", help="Ride telemetry CSV to open")
    args = parser.parse_args(argv)
    app = QApplication(sys.argv[:1])
    apply_style(app)
    window = ReplayWindow(args.csv)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
