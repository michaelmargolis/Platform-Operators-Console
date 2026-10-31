# app.py ver 4
"""MDX sim_player: offline replay controller (PyQt6 only).

UI layout is in player_ui.py; presentation panels in panels.py. This module
owns playback, capture loading, video synchronisation and optional Twin output.
The operational POC application continues using PyQt5 in its own process.
"""
from __future__ import annotations
import argparse
from collections import OrderedDict
from pathlib import Path
import sys
import time
from PyQt6.QtCore import Qt, QSettings, QThread, pyqtSignal, QTimer
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox, QMainWindow, QDialog, QDockWidget, QWidget, QInputDialog
from .panels import RideCsvDialogFilter, clock_label
from .player_ui import PlayerUiMixin, apply_style
from .recording import RideRecording, load_recording, write_saved_calibration
from .video_sync import VideoSeekGuard, is_ride_telemetry_csv
from .settings import load_visualizer_settings, save_visualizer_settings
from .options_dialog import PlayerOptionsDialog
from .twin_output import ReplayTwinOutput
from .angular_cues import EULER, MODE_LABELS
from .metrics import measure_recording
from .loop_range import PlaybackRange
from .anomaly_detection import AnomalyThresholds, detect_anomalies, AnomalyEvent
from .muscle_extrema import MuscleExtrema, plot_bounds
from .variant_presets import (
    choices_for_capture, gain_tuple, snapshot_for_capture, candidate_entry, default_label_for_capture,
    dropdown_choices_for_capture,
)
from .platform_selection import initial_platform_key, platform_metadata
from sims.nolimits2.variant_store import VariantStore
from .tuning import (
    DEFAULT_GAINS, ExperimentSettings, load_experiment, save_experiment,
    muscle_flags, tuning_metrics, validate_gain_percent, current_poc_global_settings,
)

class LoadWorker(QThread):
    loaded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, csv_path, *, cue_mode=EULER, gain_percent=DEFAULT_GAINS,
                 variant_label=None, variant_snapshot=None,
                 default_standard_variant=False, platform_key="platform_v2",
                 variant_store=None, parent=None):
        super().__init__(parent)
        self.csv_path = csv_path
        self.platform_key = platform_key
        self.variant_store = variant_store
        self.cue_mode = cue_mode
        self.gain_percent = validate_gain_percent(gain_percent)
        self.variant_label = variant_label
        self.variant_snapshot = variant_snapshot
        self.default_standard_variant = default_standard_variant

    def run(self):
        try:
            result = load_recording(self.csv_path, platform=self.platform_key, variant_store=self.variant_store,
                                    cue_mode=self.cue_mode,
                                    gain_percent=self.gain_percent,
                                    variant_label=self.variant_label,
                                    variant_snapshot=self.variant_snapshot,
                                    default_standard_variant=self.default_standard_variant)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        else:
            self.loaded.emit(result)


class ReplayWindow(PlayerUiMixin, QMainWindow):
    def __init__(self, initial_path=None):
        super().__init__()
        self.platform_key = initial_platform_key()
        self.variant_store = VariantStore.load(self.platform_key)
        self._previous_platform_store = None
        self.recording: RideRecording | None = None
        self.loop_range = PlaybackRange()
        self._loop_holding = False
        self.loop_hold_timer = QTimer(self)
        self.loop_hold_timer.setSingleShot(True)
        self.loop_hold_timer.timeout.connect(self._restart_loop)
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
        # A bounded cache of already processed (cue, six gains) experiments.
        # Unlimited slider variants would otherwise retain hundreds of large rides.
        self._mode_cache = OrderedDict()
        self._archived_variant_snapshot = None
        self.gain_percent = DEFAULT_GAINS
        self._baseline_recording = None
        self.anomalies = ()
        self.muscle_extrema = MuscleExtrema()
        self.muscle_plot_bounds = (735.0, 1015.0)
        self.worker = None
        self.loading = False
        self.has_video = False
        self.video_offset_s = 0.0
        self.settings = QSettings("MDXSAT", "sim_player")
        self.visualizer_settings = load_visualizer_settings(self.settings)
        self.twin_output = ReplayTwinOutput(self.visualizer_settings)
        self.setWindowTitle("MDX sim_player — No recording loaded")
        # Compact default; the video grows/shrinks with the outer window.
        self.resize(1320, 720)
        self.setDockOptions(QMainWindow.DockOption.AllowNestedDocks | QMainWindow.DockOption.AllowTabbedDocks |
                            QMainWindow.DockOption.AnimatedDocks)
        self._create_central()
        self._create_docks()
        self._create_menus()
        self._update_twin_indicator()
        self.statusBar().showMessage("Offline replay • no NoLimits2, SHS or Festo connections")
        geometry = self.settings.value("geometry")
        layout = self.settings.value("dock_layout")
        if geometry is not None:
            self.restoreGeometry(geometry)
        # Restore the v16 blank spacer by name, then remove it from the live
        # layout.  Without this temporary widget, Qt can retain a vacant slot
        # in an existing saved dock layout on upgrade.  User docks remain in
        # their previous positions, even when they are not tabbed together.
        legacy_space = QDockWidget("", self)
        legacy_space.setObjectName("sim_player_right_spacer_v16")
        legacy_space.setWidget(QWidget(legacy_space))
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, legacy_space)
        if layout is None or not self.restoreState(layout):
            self.restore_default_layout()
        self.removeDockWidget(legacy_space)
        legacy_space.deleteLater()
        # The new dock is intentionally opt-in.  Existing saved layouts
        # cannot contain it, so don't let Qt display it by default.
        if not self.settings.value("video_sync_dock_v15_seen", False, type=bool):
            self.panels["video_sync"].hide()
            self.settings.setValue("video_sync_dock_v15_seen", True)
        # Former video-splitter state is intentionally obsolete.  Qt now
        # lays out video (stretch) and playback controls (fixed height) directly.
        self.settings.remove("video_splitter_state")
        self.settings.remove("video_splitter_v12_initialized")
        QTimer.singleShot(0, self._initialize_layout_v17)
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self.twin_timer = QTimer(self)
        self.twin_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.twin_timer.setInterval(50)  # Twin's nominal 20 Hz update rate
        self.twin_timer.timeout.connect(self._twin_tick)
        self.twin_timer.start()
        if initial_path:
            QTimer.singleShot(1, lambda: self.open_capture(Path(initial_path)))

    def _initialize_layout_v17(self):
        """Apply fixed playback-strip height and compact startup dock rows."""
        self._fix_playback_controls_height()
        self._compact_right_docks()

    def _reset_muscle_extrema(self):
        """Start a new per-muscle extrema history for this replay/experiment."""
        self.muscle_extrema.clear()
        if self.recording is not None:
            self.muscle_plot_bounds = plot_bounds(self.recording)
        self.muscle_bars.clear_extrema()

    def about(self):
        QMessageBox.information(self, "About sim_player",
            "sim_player — POC v182 — Qt6/FFmpeg video + telemetry replay.\n\n"
            "A/B playback markers, Full/Range/Loop modes and adjustable loop hold.\n"
            "One selectable ride/kinematic marker lane and grouped anomaly replay.\n"
            "Manual 6DOF gains and ride-neutral height, baseline and experiment JSON.\n"
            "Red muscle bars indicate out-of-range requests/final clamps.\n"
            "Muscle minimum/maximum requested markers persist until replay restart.\n"
            "Playback controls stay fixed-height; resize the window to resize video.\n"
            "Angular cues: current Euler, sine Roll and quaternion gravity vector.\n"
            "Per-platform JSON candidates can be saved explicitly for live POC review.\n"
            "All candidate cues are offline research only, not validated for rides.\n"
            "Motion lengths use existing POC offline analysis; no new kinematics.\n"
            "Video timing requires empirical calibration.\n"
            "No SHS, NoLimits2 live connection or Festo communication.\n"
            "Optional POC_TWIN UDP replay uses zero pressures.\n\n"
            "Later docks can show strip charts, clearance and tension feasibility.")

    @staticmethod
    def _variant_cache_token(recording):
        # Snapshot is relevant even when a label was renamed or removed later.
        import json
        return json.dumps({"platform": recording.platform_key,
                           "geometry": getattr(recording, "platform_config_sha256", ""),
                           "variant": recording.variant_snapshot}, sort_keys=True, default=str)

    def _cache_recording(self, recording):
        key = (recording.cue_mode, recording.gain_percent,
               self._variant_cache_token(recording))
        self._mode_cache[key] = recording
        self._mode_cache.move_to_end(key)
        while len(self._mode_cache) > 5:
            self._mode_cache.popitem(last=False)

    def _refresh_cue_comparison(self):
        """Do not compare cue metrics calculated with *different* gains."""
        self.cue_comparison.reset()
        active_variant = self._variant_cache_token(self.recording) if self.recording else "null"
        for (mode, gains, variant_token), ride in self._mode_cache.items():
            if gains == self.gain_percent and variant_token == active_variant:
                self.cue_comparison.add_metrics(mode, measure_recording(ride))
        if self.recording is not None:
            self.cue_comparison.set_active(self.recording.cue_mode)
        pct = ", ".join(f"{v:g}" for v in self.gain_percent)
        self.cue_comparison.help_label.setText(
            f"Comparisons at same six relative gains ({pct}%). "
            "Select each cue to calculate; results are modeled, not measured."
        )

    def _update_tuning_feedback(self):
        if self.recording is None:
            self.tuning_panel.set_feedback("Open a ride capture to calculate motion headroom.")
            return
        current = tuning_metrics(self.recording)
        thresholds = AnomalyThresholds(
            self.anomaly_panel.step_box.value(), self.anomaly_panel.floor_box.value())
        floor = (f"{current.min_clearance_mm:.1f} mm" if current.min_clearance_mm is not None
                 else "not available")
        problems = []
        if current.peak_step_mm > thresholds.muscle_step_mm:
            problems.append("step warning")
        if (current.min_clearance_mm is not None and
                current.min_clearance_mm < thresholds.low_clearance_mm):
            problems.append("clearance warning")
        if current.requested_oob_frames:
            problems.append("requested length exceeded")
        if current.final_clipped_frames:
            problems.append("final command clamped")
        step_margin = thresholds.muscle_step_mm - current.peak_step_mm
        floor_margin = (None if current.min_clearance_mm is None else
                        current.min_clearance_mm - thresholds.low_clearance_mm)
        text = (f"Max step: {current.peak_step_mm:.1f} mm "
                f"(warning {thresholds.muscle_step_mm:g}; margin {step_margin:+.1f} mm)\n")
        if floor_margin is not None:
            text += (f"Min applied floor: {floor} (warning {thresholds.low_clearance_mm:g}; "
                     f"margin {floor_margin:+.1f} mm)\n")
        else:
            text += "Min applied floor: not available\n"
        if current.initial_muscle_min_mm is not None:
            text += (f"Initial muscles: {current.initial_muscle_min_mm:.1f}.."
                     f"{current.initial_muscle_max_mm:.1f} mm\n")
        text += (f"Requested out-of-range: {current.requested_oob_frames} frames "
                 f"(max {current.worst_requested_excess_mm:.1f} mm)\n"
                 f"Workspace interventions: {current.workspace_frames} frames; "
                 f"final clamps: {current.final_clipped_frames} frames "
                 f"(short {current.short_clip_frames}, long {current.long_clip_frames}; "
                 f"worst {current.worst_short_clip_mm:.1f}/{current.worst_long_clip_mm:.1f} mm)")
        if problems:
            text += "\nWarnings: " + ", ".join(problems)
        baseline = self._baseline_recording
        if baseline is not None:
            reference = tuning_metrics(baseline)
            text += (f"\nΔ vs baseline: peak step "
                     f"{current.peak_step_mm - reference.peak_step_mm:+.1f} mm; "
                     f"workspace {current.workspace_frames-reference.workspace_frames:+d} frames")
            if current.min_clearance_mm is not None and reference.min_clearance_mm is not None:
                text += f"; floor {current.min_clearance_mm-reference.min_clearance_mm:+.1f} mm"
        self.tuning_panel.set_feedback(text)

    def _set_baseline(self):
        if self.recording is None or self.loading:
            return
        self._baseline_recording = self.recording
        self._refresh_baseline_label()
        self._update_tuning_feedback()

    def _refresh_baseline_label(self):
        baseline = self._baseline_recording
        if baseline is None:
            self.tuning_panel.set_baseline_label("Baseline: not set")
        else:
            gains = "/".join(f"{g:g}" for g in baseline.gain_percent)
            self.tuning_panel.set_baseline_label(
                f"Baseline: {MODE_LABELS[baseline.cue_mode]} · gains {gains}% · "
                f"neutral {float((baseline.variant_snapshot or {}).get('neutral_height_delta_mm',0.0)):+g} mm"
            )

    def _restore_baseline(self):
        if self._baseline_recording is None or self.loading:
            return
        baseline = self._baseline_recording
        self._apply_experiment_values(baseline.cue_mode, baseline.gain_percent,
                                      variant_label=baseline.variant_label,
                                      variant_snapshot=baseline.variant_snapshot,
                                      platform_key=baseline.platform_key)

    def _reset_gains(self):
        if self.recording is None or self.loading:
            return
        self._apply_experiment_values(self.recording.cue_mode, DEFAULT_GAINS)

    def _gain_changed(self, gains):
        if self.recording is None or self.loading:
            return
        self._request_reanalysis(self.recording.cue_mode, gains)

    def _neutral_offset_changed(self, offset_mm):
        """Research-only ride height, never persisted until Save as candidate."""
        if self.recording is None or self.loading or self.recording.variant_snapshot is None:
            return
        from core.ride_neutral import validated_ride_neutral_delta
        neutral = validated_ride_neutral_delta(offset_mm)
        snapshot = dict(self.recording.variant_snapshot)
        snapshot["neutral_height_delta_mm"] = neutral
        self._request_reanalysis(self.recording.cue_mode,
                                 self.tuning_panel.gain_percent(),
                                 variant_snapshot=snapshot)

    def _sync_neutral_panel(self, recording):
        snapshot = recording.variant_snapshot or {}
        self.tuning_panel.set_neutral_reference(recording.platform_neutral_height_mm)
        self.tuning_panel.set_neutral_offset(snapshot.get("neutral_height_delta_mm", 0.0))
        self.tuning_panel.set_neutral_enabled(recording.variant_snapshot is not None)

    def _apply_experiment_values(self, mode, gains, *, variant_label="keep", variant_snapshot=None, platform_key=None):
        if platform_key is not None and platform_key != self.platform_key:
            try:
                self._stage_platform(platform_key)
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "Platform unavailable", str(exc))
                return
        gains = validate_gain_percent(gains)
        self.tuning_panel.set_gains(gains)
        self.cue_box.blockSignals(True)
        self.cue_box.setCurrentIndex(self.cue_box.findData(mode))
        self.cue_box.blockSignals(False)
        self._request_reanalysis(mode, gains, variant_label=variant_label,
                                 variant_snapshot=variant_snapshot)

    def _save_experiment(self):
        if self.recording is None or self.loading:
            return
        suggested = str(self.recording.csv_path.parent /
                        (self.recording.csv_path.stem + "_experiment.json"))
        path, _ = QFileDialog.getSaveFileName(
            self, "Save research experiment", suggested, "Research experiments (*.json)")
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        try:
            saved = save_experiment(path, ExperimentSettings(
                cue_mode=self.recording.cue_mode,
                gain_percent=self.recording.gain_percent,
                variant_label=self.recording.variant_label,
                variant_snapshot=self.recording.variant_snapshot,
                global_settings=current_poc_global_settings(),
                platform_key=self.recording.platform_key,
                platform_config_module=self.recording.platform_config_module,
                platform_config_sha256=self.recording.platform_config_sha256,
                step_warning_mm=self.anomaly_panel.step_box.value(),
                clearance_warning_mm=self.anomaly_panel.floor_box.value(),
            ))
        except (OSError, ValueError, TypeError) as exc:
            QMessageBox.warning(self, "Experiment not saved", str(exc))
        else:
            self.statusBar().showMessage(f"Research experiment saved: {saved}", 6000)

    def _load_experiment(self):
        if self.recording is None or self.loading:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Load research experiment", str(self.recording.csv_path.parent),
            "Research experiments (*.json)")
        if not path:
            return
        try:
            values = load_experiment(path)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            QMessageBox.warning(self, "Experiment not loaded", str(exc))
            return
        self.anomaly_panel.step_box.blockSignals(True)
        self.anomaly_panel.floor_box.blockSignals(True)
        self.anomaly_panel.step_box.setValue(values.step_warning_mm)
        self.anomaly_panel.floor_box.setValue(values.clearance_warning_mm)
        self.anomaly_panel.step_box.blockSignals(False)
        self.anomaly_panel.floor_box.blockSignals(False)
        if values.variant_label and values.variant_snapshot:
            from sims.nolimits2 import ride_config
            expected_park = ride_config.park_key_for_capture(self.recording.ride)
            if values.variant_snapshot.get("park") != expected_park:
                QMessageBox.warning(self, "Experiment not loaded", "Saved variant is for another physical park")
                return
        if values.global_settings is not None and values.global_settings != current_poc_global_settings():
            choice = QMessageBox.question(
                self, "POC global gains/washout differ",
                "This experiment was saved with different global POC gains or washout. "
                "Playback currently uses the installed values, so results may differ. Continue?",
            )
            if choice != QMessageBox.StandardButton.Yes:
                return
        target_platform = values.platform_key or self.platform_key
        try:
            metadata = platform_metadata(target_platform)
        except (OSError, ValueError, ImportError) as exc:
            QMessageBox.warning(self, "Experiment not loaded", str(exc))
            return
        if values.platform_key is None:
            message = "This older experiment has no platform identity. Use the currently selected platform?"
        elif (values.platform_config_sha256 and
              values.platform_config_sha256 != metadata['platform_config_sha256']):
            message = "The platform configuration has changed since this experiment was saved. Recalculate using the installed geometry?"
        else:
            message = None
        if message and QMessageBox.question(self, "Experiment platform", message) != QMessageBox.StandardButton.Yes:
            return
        self._apply_experiment_values(values.cue_mode, values.gain_percent,
                                      variant_label=values.variant_label,
                                      variant_snapshot=values.variant_snapshot,
                                      platform_key=target_platform)
        self._refresh_anomalies()
        self.statusBar().showMessage(f"Experiment settings selected: {path} — recalculating motion", 6000)

    def show_settings(self):
        """Offer user-only preferences; never mutate the operational POC config."""
        dialog = PlayerOptionsDialog(self.visualizer_settings, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        updated = dialog.selected_settings()
        if updated == self.visualizer_settings:
            return
        previous = self.visualizer_settings
        if not self.twin_output.configure(updated) and updated.enabled:
            error = self.twin_output.last_error or "Cannot initialize UDP output"
            self.twin_output.configure(previous)
            QMessageBox.warning(self, "Visualizer unavailable", error)
            return
        self.visualizer_settings = updated
        save_visualizer_settings(self.settings, updated)
        if updated.enabled:
            self._send_twin(force=True)
        self._update_twin_indicator()
        self.statusBar().showMessage(
            f"Visualizer UDP {'enabled' if updated.enabled else 'disabled'}"
            + (f" — {updated.ip_address}:{updated.port}" if updated.enabled else ""),
            5000)

    def _update_twin_indicator(self):
        if not self.twin_output.enabled:
            label = "TWIN OFF"
            tooltip = "Visualizer UDP output is off. Open File → Settings to enable it."
        elif self.twin_output.last_error:
            label = "TWIN ERROR"
            tooltip = self.twin_output.last_error
        elif self.recording is None:
            label = "TWIN ENABLED"
            tooltip = "UDP is enabled, but no ride is loaded."
        elif self._loop_holding:
            label = "TWIN HOLD"
            tooltip = "Loop hold at B: retaining the selected pose until restart."
        elif self.playing:
            label = "TWIN SENDING"
            tooltip = "Sending calculated replay pose to the Visualizer at up to 20 Hz."
        else:
            label = "TWIN HOLD"
            tooltip = "Visualizer UDP is enabled; the selected frame is held while paused."
        self.twin_status.setText(label)
        self.twin_status.setToolTip(tooltip +
            f"\nDestination: {self.visualizer_settings.ip_address}:{self.visualizer_settings.port}")

    def _send_twin(self, *, force=False):
        if self.recording is None or not self.twin_output.enabled:
            return
        index = self.recording.nearest_index(self.current_s)
        self.twin_output.send_frame(
            self.recording, index,
            state="RUNNING" if self.playing and not self._loop_holding else "PAUSED",
            force=force,
        )
        if self.twin_output.last_error:
            self.statusBar().showMessage("Visualizer UDP: " + self.twin_output.last_error, 5000)
            self._update_twin_indicator()

    def _twin_tick(self):
        if self.playing and not self._loop_holding and self.recording is not None and not self.slider.isSliderDown():
            self._send_twin()

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

    def _stage_platform(self, key):
        catalog = VariantStore.load(key)  # Validate before changing UI/state.
        if key != self.platform_key:
            self._previous_platform_store = (self.platform_key, self.variant_store)
        self.platform_key, self.variant_store = key, catalog
        self._sync_platform_selector()

    def _sync_platform_selector(self):
        self.platform_box.blockSignals(True)
        self.platform_box.setCurrentIndex(self.platform_box.findData(self.platform_key))
        metadata = platform_metadata(self.platform_key)
        self.platform_box.setToolTip(
            f"{self.platform_key}: {metadata['platform_config_module']}\nVariants: {self.variant_store.path}")
        self.platform_box.blockSignals(False)

    def _platform_changed(self, *_args):
        key = self.platform_box.currentData()
        if self.loading or (self.worker is not None and self.worker.isRunning()):
            self._sync_platform_selector()
            return
        if key == self.platform_key:
            return
        try:
            self._stage_platform(key)
            if self.recording is None:
                self._previous_platform_store = None
                return
            label = default_label_for_capture(self.recording.ride, self.variant_store)
            snapshot = snapshot_for_capture(self.recording.ride, label, self.variant_store)
            self._apply_experiment_values(snapshot['angular_cue'] if snapshot else EULER,
                                          gain_tuple(snapshot), variant_label=label,
                                          variant_snapshot=snapshot)
        except (OSError, ValueError, KeyError, ImportError) as exc:
            self._restore_platform_selection()
            QMessageBox.warning(self, "Platform not selected", str(exc))

    def _restore_platform_selection(self):
        if self._previous_platform_store is not None:
            self.platform_key, self.variant_store = self._previous_platform_store
            self._previous_platform_store = None
        self._sync_platform_selector()

    def _set_analysis_busy(self):
        self.platform_box.setEnabled(False)
        self.variant_box.setEnabled(False)
        self.save_variant_action.setEnabled(False)

    def _fill_variant_choices(self, recording):
        """Only show variants for the captured physical park."""
        self.variant_box.blockSignals(True)
        self.variant_box.clear()
        for displayed, full_label in dropdown_choices_for_capture(recording.ride, self.variant_store):
            self.variant_box.addItem(displayed, full_label)
        index = self.variant_box.findData(recording.variant_label)
        if recording.variant_label and index < 0 and recording.variant_snapshot:
            self.variant_box.addItem("Archived: " + recording.variant_label, recording.variant_label)
            index = self.variant_box.count() - 1
        self.variant_box.setCurrentIndex(max(0, index))
        busy = self.loading or (self.worker is not None and self.worker.isRunning())
        self.variant_box.setEnabled(not busy and bool(recording.frames) and self.variant_box.count() > 1)
        self.save_variant_action.setEnabled(not busy and bool(recording.variant_snapshot))
        self.tuning_panel.set_neutral_enabled(bool(recording.variant_snapshot))
        self.variant_box.blockSignals(False)

    def _variant_changed(self, *_args):
        if self.recording is None or self.loading:
            return
        label = self.variant_box.currentData()
        if label is None:
            self._apply_experiment_values(EULER, DEFAULT_GAINS, variant_label=None)
            return
        try:
            # Re-select an archived experiment after the production variant has
            # been renamed/deleted, without resolving it against live config.
            if (self.recording.variant_label == label and
                    self.recording.variant_snapshot and
                    label not in choices_for_capture(self.recording.ride, self.variant_store)):
                snapshot = self.recording.variant_snapshot
            else:
                snapshot = snapshot_for_capture(self.recording.ride, label, self.variant_store)
            gains = gain_tuple(snapshot)
        except (ValueError, KeyError) as exc:
            QMessageBox.warning(self, "Invalid ride variant", str(exc))
            self._fill_variant_choices(self.recording)
            return
        self._apply_experiment_values(snapshot["angular_cue"], gains,
                                      variant_label=label, variant_snapshot=snapshot)

    def save_variant_entry(self):
        """Append a new candidate, never overwrite another variant or accept it."""
        recording = self.recording
        if recording is None or self.loading or (self.worker is not None and self.worker.isRunning()):
            return
        if recording.variant_snapshot is None:
            QMessageBox.information(self, "Named configuration required",
                "Select a named ride configuration first. Raw replay has no reproducible live heave preset.")
            return
        proposed = str(recording.variant_label or recording.variant_snapshot['label']) + " [Trial]"
        label, confirmed = QInputDialog.getText(
            self, "Save ride variant", "Unique variant label ([modifiers] after the park name):", text=proposed)
        if not confirmed:
            return
        try:
            entry = candidate_entry(label.strip(), recording)
            self.variant_store.add_candidate(entry)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            QMessageBox.warning(self, "Variant not saved", str(exc))
            return
        self._fill_variant_choices(recording)
        preferred = [v for v in self.variant_store.entries()
                     if v['park'] == entry['park'] and v.get('status') == 'preferred']
        note = (" A preferred ride already exists; this candidate stays hidden in POC until that preference is edited."
                if preferred else "")
        self.statusBar().showMessage(
            f"Candidate saved: {entry['label']} -> {self.variant_store.path}. "
            "POC reads added/tuned variants on restart." + note, 15000)

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
        self.recording = None  # never send the previous ride during loading
        self.muscle_extrema.clear()
        self.muscle_bars.clear_extrema()
        self.loop_range.reset(0.0)
        self._refresh_range_controls()
        self._mode_cache.clear()
        self.gain_percent = DEFAULT_GAINS
        self._baseline_recording = None
        self.variant_box.blockSignals(True)
        self.variant_box.clear()
        self.variant_box.addItem("Raw (no capture loaded)", None)
        self.variant_box.setEnabled(False)
        self.variant_box.blockSignals(False)
        self.save_variant_action.setEnabled(False)
        self.tuning_panel.set_gains(DEFAULT_GAINS)
        self.tuning_panel.set_neutral_offset(0.0)
        self.tuning_panel.set_neutral_reference(None)
        self.tuning_panel.set_neutral_enabled(False)
        self.tuning_panel.set_analysis_enabled(False)
        self._refresh_baseline_label()
        self._update_tuning_feedback()
        self.cue_comparison.reset()
        self.anomalies = ()
        self.anomaly_panel.set_events(())
        self._refresh_marker_lane()
        self._update_twin_indicator()
        self.loading = True
        self._set_analysis_busy()
        self.cue_box.setEnabled(False)
        self.ride_label.setText("LOADING  /  " + path.name)
        self.info_badge.setText("LOADING")
        self.statusBar().showMessage("Loading telemetry and calculating POC muscle lengths…")
        self.worker = LoadWorker(path, default_standard_variant=True, platform_key=self.platform_key,
                                 variant_store=self.variant_store, parent=self)
        self.worker.loaded.connect(self._load_success)
        self.worker.failed.connect(self._load_failure)
        self.worker.finished.connect(self._worker_finished)
        self.worker.start()

    def _load_success(self, recording):
        self.loading = False
        self.recording = recording
        self._fill_variant_choices(recording)
        self._reset_muscle_extrema()
        self.gain_percent = recording.gain_percent
        self.cue_box.blockSignals(True)
        self.cue_box.setCurrentIndex(self.cue_box.findData(recording.cue_mode))
        self.cue_box.blockSignals(False)
        self._cache_recording(recording)
        self._baseline_recording = recording
        self.tuning_panel.set_gains(recording.gain_percent)
        self._sync_neutral_panel(recording)
        self._refresh_baseline_label()
        self._refresh_cue_comparison()
        self._update_tuning_feedback()
        # Wait for QThread.finished before re-enabling mode selection: a user
        # must not start a new worker while the previous thread is still live.
        self._update_twin_indicator()
        self._sync_platform_selector()
        self.settings.setValue("last_directory", str(recording.csv_path.parent))
        self.ride_label.setText(recording.ride.upper() + "  /  REPLAY")
        self.setWindowTitle(f"MDX sim_player — {recording.ride} — {recording.csv_path.name}")
        self.info_badge.setText(f"{len(recording.frames):,} SAMPLES")
        self._refresh_anomalies()
        self.loop_range.reset(recording.duration)
        self._refresh_range_controls()
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
            f"{recording.analysis_settings} | {len(recording.events)} ride events | "
            f"{len(self.anomalies)} anomaly intervals" + (" | " + notes if notes else ""))

    def _load_failure(self, message):
        self.loading = False
        self.cue_box.setEnabled(True)
        self.tuning_panel.set_analysis_enabled(False)
        self.info_badge.setText("LOAD FAILED")
        self.statusBar().showMessage("Could not load capture: " + message)
        QMessageBox.warning(self, "Cannot open capture", message)

    def _worker_finished(self):
        # Retain the QThread object until its actual finished signal. Dropping
        # the last Python reference in loaded() can crash Qt on Windows.
        worker = self.sender()
        if worker is self.worker:
            self.worker = None
            if not self.loading:
                self.cue_box.setEnabled(self.recording is not None)
                self.platform_box.setEnabled(True)
                if self.recording is not None:
                    self._fill_variant_choices(self.recording)
                self.tuning_panel.set_analysis_enabled(self.recording is not None)
        if worker is not None:
            worker.deleteLater()

    def _cue_changed(self, *_args):
        if self.recording is None or self.loading:
            return
        if self.worker is not None and self.worker.isRunning():
            self.cue_box.blockSignals(True)
            self.cue_box.setCurrentIndex(self.cue_box.findData(self.recording.cue_mode))
            self.cue_box.blockSignals(False)
            return
        self._request_reanalysis(self.cue_box.currentData(), self.tuning_panel.gain_percent())

    def _request_reanalysis(self, mode, gains, *, variant_label="keep", variant_snapshot=None):
        """Re-run entire capture through unchanged POC washout and kinematics.

        No video load or decoder seek; the current playback position is retained.
        """
        if self.recording is None or self.loading:
            return
        gains = validate_gain_percent(gains)
        if variant_label == "keep":
            variant_label = getattr(self.recording, "variant_label", None)
            if variant_snapshot is None:
                variant_snapshot = getattr(self.recording, "variant_snapshot", None)
        elif variant_label is not None and variant_snapshot is None:
            try:
                variant_snapshot = snapshot_for_capture(self.recording.ride, variant_label, self.variant_store)
            except (ValueError, KeyError) as exc:
                self.statusBar().showMessage(f"Variant error: {exc}", 7000)
                self._fill_variant_choices(self.recording)
                return
        import json
        key_snapshot = json.dumps({"platform": self.platform_key,
                                   "geometry": platform_metadata(self.platform_key)['platform_config_sha256'],
                                   "variant": variant_snapshot}, sort_keys=True, default=str)
        if (mode == self.recording.cue_mode and gains == self.recording.gain_percent
                and key_snapshot == self._variant_cache_token(self.recording)):
            self.tuning_panel.set_gains(gains)
            self._update_tuning_feedback()
            return
        self._set_playing(False)
        cache_key = (mode, gains, key_snapshot)
        if cache_key in self._mode_cache:
            self._mode_cache.move_to_end(cache_key)
            self._apply_reanalysis(self._mode_cache[cache_key])
            return
        self.loading = True
        self._set_analysis_busy()
        self.cue_box.setEnabled(False)
        self.tuning_panel.set_analysis_enabled(False)
        self.statusBar().showMessage("Recalculating experimental gains/cue using POC kinematics…")
        self.worker = LoadWorker(self.recording.csv_path, cue_mode=mode,
                                 gain_percent=gains, variant_label=variant_label,
                                 variant_snapshot=variant_snapshot, platform_key=self.platform_key,
                                 variant_store=self.variant_store, parent=self)
        self.worker.loaded.connect(self._reanalysis_success)
        self.worker.failed.connect(self._reanalysis_failure)
        self.worker.finished.connect(self._worker_finished)
        self.worker.start()

    def _reanalysis_success(self, calculated):
        self._cache_recording(calculated)
        self.loading = False
        self._apply_reanalysis(calculated)

    def _apply_reanalysis(self, calculated):
        # Do not reload or seek video when only calculated motion changes.
        current_seconds = self.current_s
        changed_platform = self.recording is not None and self.recording.platform_key != calculated.platform_key
        self.recording = calculated
        self.platform_key = calculated.platform_key
        self._previous_platform_store = None
        self._sync_platform_selector()
        if changed_platform:
            self._baseline_recording = calculated
            self._refresh_baseline_label()
        self._fill_variant_choices(calculated)
        self._reset_muscle_extrema()
        self.gain_percent = calculated.gain_percent
        self.current_s = min(current_seconds, calculated.duration)
        self.tuning_panel.set_gains(calculated.gain_percent)
        self._sync_neutral_panel(calculated)
        self._refresh_cue_comparison()
        self._refresh_anomalies()
        self._update_tuning_feedback()
        self._display_current()
        self._send_twin(force=True)
        self.statusBar().showMessage(
            f"Research: {MODE_LABELS[calculated.cue_mode]} | "
            "gains recalculated, video unchanged | "
            f"{calculated.analysis_settings}", 9000)

    def _reanalysis_failure(self, error):
        self.loading = False
        self._restore_platform_selection()
        # Revert any speculative UI change to the last successfully analysed ride.
        self.cue_box.blockSignals(True)
        current_mode = self.recording.cue_mode
        index = self.cue_box.findData(current_mode)
        if index >= 0:
            self.cue_box.setCurrentIndex(index)
        self.cue_box.blockSignals(False)
        self.tuning_panel.set_gains(self.recording.gain_percent)
        self._sync_neutral_panel(self.recording)
        self._fill_variant_choices(self.recording)
        self._refresh_cue_comparison()
        self.statusBar().showMessage("Experimental analysis failed: " + error)
        QMessageBox.warning(self, "Research analysis failed", error)

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
        self.axis_bars.axis_context = "RAW CSV" if show_raw else "PROCESSED"
        self.panels["axes"].setWindowTitle("Transform · 6DOF (original NL2 CSV)" if show_raw else
                                            "Transform · 6DOF (processed)")
        self.axis_bars.update_values(frame.source_axes if show_raw else frame.processed_axes)
        requested = frame.analysis.get("requested", ())
        request_oob, final_clipped = muscle_flags(
            frame, recording.min_length_mm, recording.max_length_mm)
        self.muscle_extrema.advance(recording.frames, recording.nearest_index(self.current_s))
        self.muscle_bars.update_values(
            frame.muscle_lengths_mm, recording.min_length_mm, recording.max_length_mm,
            requested=requested if len(requested) == 6 else None,
            request_oob=request_oob, final_clipped=final_clipped,
            extrema=self.muscle_extrema.extrema(), plot_bounds=self.muscle_plot_bounds)
        self.markers.set_current(self.current_s)
        self.position_label.setText(clock_label(self.current_s) + " / " + clock_label(recording.duration))
        self.slider.blockSignals(True)
        self.slider.setValue(round(self.current_s * 1000))
        self.slider.blockSignals(False)
        self.info_badge.setText("WORKSPACE LIMITED" if frame.workspace_limited else
                                f"{len(recording.frames):,} SAMPLES")

    def _refresh_anomalies(self):
        """Reanalyse cached POC results, never recompute kinematics here."""
        if self.recording is None:
            self.anomalies = ()
        else:
            thresholds = AnomalyThresholds(
                muscle_step_mm=self.anomaly_panel.step_box.value(),
                low_clearance_mm=self.anomaly_panel.floor_box.value(),
            )
            self.anomalies = detect_anomalies(self.recording, thresholds)
        self.anomaly_panel.set_events(self.anomalies)
        self._refresh_marker_lane()

    def _anomaly_thresholds_changed(self, _step, _floor):
        self._refresh_anomalies()
        self._update_tuning_feedback()

    def _marker_mode_changed(self, *_args):
        self._refresh_marker_lane()

    def _refresh_marker_lane(self):
        category = self.marker_mode_box.currentData()
        recording = self.recording
        if category == "kinematic":
            events = self.anomalies
            self.marker_title.setText("KINEMATIC ANOMALIES  •  orange bars = duration; click to peak; hover for details")
        else:
            events = recording.events if recording is not None else ()
            self.marker_title.setText("RIDE EVENTS  •  click to seek; hover for details")
        self.markers.category = category
        self.markers.set_recording(events, recording.duration if recording else 1.0)

    def _marker_selected(self, event):
        if isinstance(event, AnomalyEvent):
            self.anomaly_panel.select_event(event)

    def _examine_anomaly(self, event):
        """Reuse existing A/B looping with 1 s context around one episode."""
        if self.recording is None or event not in self.anomalies:
            return
        self._set_playing(False)
        self.loop_range.select_event(event.start_s, event.end_s, context_s=1.0)
        self._refresh_range_controls()
        self.seek_seconds(self.loop_range.start, force_video=True)
        self._set_playing(True)
        self.statusBar().showMessage(
            f"Examining {event.name}: {clock_label(event.start_s)}–"
            f"{clock_label(event.end_s)} (+1 s context); looping", 5000
        )

    def _refresh_range_controls(self):
        """Update A/B presentation without seeking the video or Twin."""
        region = self.loop_range
        self.slider.set_playback_range(region.duration, region.start, region.end, region.mode)
        self.range_label.setText(
            f"A {clock_label(region.start)}  –  B {clock_label(region.end)}"
        )
        self.range_mode_box.blockSignals(True)
        selected = self.range_mode_box.findData(region.mode)
        if selected >= 0:
            self.range_mode_box.setCurrentIndex(selected)
        self.range_mode_box.blockSignals(False)
        self.loop_delay_box.blockSignals(True)
        for index in range(self.loop_delay_box.count()):
            if self.loop_delay_box.itemData(index) == region.loop_delay_s:
                self.loop_delay_box.setCurrentIndex(index)
                break
        self.loop_delay_box.blockSignals(False)
        available = self.recording is not None and not self.loading
        self.mark_button.setEnabled(available)
        self.range_mode_box.setEnabled(available)
        self.loop_delay_box.setEnabled(available and region.mode == "loop")
        self.reset_range_button.setEnabled(available)

    def _range_marker_edited(self, marker, seconds):
        """A/B marker dragging deliberately never seeks the video or Twin."""
        if self.recording is None:
            return
        if self._loop_holding:
            self._cancel_loop_hold(resume_video=self.playing)
        if marker == "start":
            self.loop_range.set_start(seconds)
        elif marker == "end":
            self.loop_range.set_end(seconds)
        else:
            return
        self._refresh_range_controls()

    def _mark_range(self):
        if self.recording is None:
            return
        if self._loop_holding:
            self._cancel_loop_hold(resume_video=self.playing)
        self.loop_range.mark(self.current_s, self.range_duration_box.value())
        self._refresh_range_controls()

    def _range_mode_changed(self):
        if self.recording is None:
            return
        mode = self.range_mode_box.currentData()
        if mode == self.loop_range.mode:
            return
        if self._loop_holding:
            self._cancel_loop_hold(resume_video=self.playing)
        self.loop_range.set_mode(mode)
        self._refresh_range_controls()
        if self.playing and mode != "full":
            if self.current_s >= self.loop_range.end and mode == "range":
                self.seek_seconds(self.loop_range.end)
                self._set_playing(False)
            elif self.current_s < self.loop_range.start or self.current_s >= self.loop_range.end:
                self.seek_seconds(self.loop_range.start)

    def _loop_delay_changed(self):
        if self.recording is None:
            return
        self.loop_range.set_delay(float(self.loop_delay_box.currentData()))
        if self._loop_holding:
            self.loop_hold_timer.start(round(self.loop_range.loop_delay_s * 1000))

    def _reset_range(self):
        if self.recording is None:
            return
        if self._loop_holding:
            self._cancel_loop_hold(resume_video=self.playing)
        self.loop_range.reset()
        self._refresh_range_controls()

    def _cancel_loop_hold(self, *, resume_video=False):
        """Cancel pending loop restart, e.g. when user pauses or seeks."""
        if not self._loop_holding:
            return
        self.loop_hold_timer.stop()
        self._loop_holding = False
        self.loop_feedback_label.setVisible(False)
        self._update_twin_indicator()
        if resume_video and self.has_video:
            self.video_backend.play()  # Resume without another decoder seek.
        self.anchor_ride_s = self.current_s
        self.anchor_time = time.monotonic()

    def _begin_loop_hold(self):
        """Freeze at B, hold Twin pose, then asynchronously restart at A."""
        self.current_s = self.loop_range.end
        self._display_current()
        if self.loop_range.loop_delay_s <= 0:
            self._restart_loop()  # Zero delay: no decoder play/pause pulse.
            return
        self._loop_holding = True
        self._update_twin_indicator()
        if self.has_video:
            self.video_backend.pause()
            self._sync_video(force=True)  # One exact seek at B, while paused.
        self._send_twin(force=True)  # This frame is sent as PAUSED.
        self.loop_feedback_label.setText("LOOP HOLD")
        self.loop_feedback_label.setVisible(True)
        self.loop_hold_timer.start(round(self.loop_range.loop_delay_s * 1000))

    def _restart_loop(self):
        """Continue from A after the hold without a decoder preview pulse."""
        self.loop_hold_timer.stop()
        self._loop_holding = False
        self.loop_feedback_label.setVisible(False)
        self._update_twin_indicator()
        if not self.playing or self.recording is None or self.loop_range.mode != "loop":
            return
        self.seek_seconds(self.loop_range.start, force_video=True)
        if self.has_video:
            self.video_backend.set_speed(self.speed_ratio)
            self.video_backend.play()
        self.statusBar().showMessage("LOOP RESTART — returned to A", 1000)

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
        if self._loop_holding:
            self._cancel_loop_hold(resume_video=self.playing)
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
        # Explicit seeks update the Twin too. Slider dragging is rate-limited;
        # a released slider always sends its final selected frame.
        self._send_twin(force=force_video and not self.slider.isSliderDown())
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
        self._send_twin(force=True)
        if self.was_playing_before_scrub:
            self._set_playing(True)
        self.was_playing_before_scrub = False

    def _set_playing(self, active):
        if active and self.recording is None:
            return
        if self._loop_holding:
            self._cancel_loop_hold(resume_video=False)
        if active:
            # Deliberate replays clear previous extrema. Automatic A/B loop
            # restarts do not call _set_playing and keep history intact.
            restarted = (self.loop_range.mode == "full" and
                         (self.current_s >= self.recording.duration or
                          (self.current_s <= self.recording.times[0] + 1.e-6 and
                           self.muscle_extrema.frames_seen > 1))) or (
                         self.loop_range.mode == "range" and
                         self.current_s >= self.loop_range.end)
            if restarted:
                self._reset_muscle_extrema()
            start = self.loop_range.play_from(self.current_s)
            if start != self.current_s:
                self.seek_seconds(start)
            elif self.loop_range.mode == "full" and self.current_s >= self.recording.duration:
                self.seek_seconds(0)
        self.playing = active
        self._update_twin_indicator()
        self.anchor_ride_s = self.current_s
        self.anchor_time = time.monotonic()
        self.play_button.setText("❚❚  Pause" if active else "▶  Play")
        # Tell the Twin immediately when replay is paused/resumed. Pausing
        # holds the current pose rather than lowering it unexpectedly.
        self._send_twin(force=True)
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
        if (not self.playing or self.recording is None or
                self.slider.isSliderDown() or self._loop_holding):
            return
        target = self.anchor_ride_s + (time.monotonic() - self.anchor_time) * self.speed_ratio
        if self.loop_range.mode != "full" and target < self.loop_range.start:
            # Can happen if the operator moves A forward during playback.
            self.seek_seconds(self.loop_range.start, force_video=True)
            return
        if self.loop_range.boundary_reached(target):
            if self.loop_range.mode == "loop":
                self._begin_loop_hold()
            else:
                # Play range: one exact position at B, then stop without looping.
                self.current_s = self.loop_range.end
                self._display_current()
                self._sync_video(force=True)
                self._set_playing(False)
            return
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
        self.twin_output.close()
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("dock_layout", self.saveState())
        super().closeEvent(event)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline NoLimits2 video + telemetry replay")
    parser.add_argument("csv", nargs="?", help="Ride telemetry CSV to open")
    args = parser.parse_args(argv)
    app = QApplication(sys.argv[:1])
    apply_style(app)
    try:
        window = ReplayWindow(args.csv)
    except (OSError, ValueError, ImportError) as exc:
        QMessageBox.critical(None, "sim_player configuration error", str(exc))
        return 1
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
