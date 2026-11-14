# dynamic_source_dialog.py ver 4
"""Modal Ride Analyzer workflow for creating dynamic-source recordings."""
from __future__ import annotations

from pathlib import Path
import socket
import sys

from PyQt6.QtCore import QProcess, QTimer, QUrl, pyqtSignal, Qt
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox,
    QVBoxLayout, QWidget, QInputDialog,
)

from .dynamic_source import (
    DEFAULT_OUTPUT_ROOT, DynamicSourcePlan, PRESET_REPEATS, ROOT,
    annotate_session_scope, available_rides, capture_start_event, default_test_conditions,
    estimate_plan_duration, operator_available_ride_keys,
    plan_ride_keys, readable_ride_list, report_path_from_output, session_path_from_output,
    source_recordings, translated_capture_output, typical_ride_duration_s,
)


class CheckableRideList(QListWidget):
    """Checkbox list whose focused row toggles with Return/Enter/Space."""

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            item = self.currentItem()
            if item is not None:
                item.setCheckState(
                    Qt.CheckState.Unchecked
                    if item.checkState() == Qt.CheckState.Checked
                    else Qt.CheckState.Checked
                )
                event.accept()
                return
        super().keyPressEvent(event)


class RideSelectionDialog(QDialog):
    """Small engineering-only multi-select list for explicit study rides."""

    def __init__(self, selected=(), parent=None):
        super().__init__(parent)
        self.setWindowTitle("Choose rides for dynamic study")
        self.resize(470, 430)
        layout = QVBoxLayout(self)
        hint = QLabel(
            "Choose the NoLimits rides to record in this dynamic study. "
            "Operator-hidden parks are not listed."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.list = CheckableRideList()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        selected = set(selected)
        for ride in available_rides():
            item = QListWidgetItem(ride.name)
            item.setData(Qt.ItemDataRole.UserRole, ride.key)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if ride.key in selected else Qt.CheckState.Unchecked)
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)
        layout.addWidget(self.list, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_if_selected)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept_if_selected(self):
        if not self.selected_keys():
            QMessageBox.information(self, "Select a ride", "Select at least one ride for the dynamic study.")
            return
        self.accept()

    def selected_keys(self):
        keys = []
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                keys.append(str(item.data(Qt.ItemDataRole.UserRole)))
        return tuple(keys)


class DynamicSourceCaptureDialog(QDialog):
    """Launch the existing capture tool without duplicating its NL2 logic."""

    capture_started = pyqtSignal()

    def __init__(self, current_ride_key=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Capture Dynamic Source Recordings")
        self.setModal(True)
        self.resize(760, 650)
        self.current_ride_key = current_ride_key
        self.selected_rides = (current_ride_key,) if current_ride_key else ()
        self.session_dir: Path | None = None
        self.report_path: Path | None = None
        self.open_recording_path: Path | None = None
        self._partial = ""
        self._aborting = False
        self._estimated_remaining_s: float | None = None
        self._active_estimate_duration_s: float | None = None
        self._capture_scope: str | None = None

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Create a dynamic study containing repeated NoLimits ride recordings and the "
            "timing/provenance data required to generate and validate dynamic profiles. "
            "Qualification recordings should be made on a deployment PC with VR active."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form_host = QWidget()
        self.form = QFormLayout(form_host)
        self.form.setContentsMargins(0, 4, 0, 4)

        self.api_status = QLabel()
        self.api_refresh = QPushButton("Refresh")
        api_row = QWidget(); api_layout = QHBoxLayout(api_row)
        api_layout.setContentsMargins(0, 0, 0, 0)
        api_layout.addWidget(self.api_status, 1); api_layout.addWidget(self.api_refresh)
        self.api_refresh.clicked.connect(self.refresh_api_status)
        self.form.addRow("NoLimits 2 API", api_row)

        self.scope = QComboBox()
        self.scope.addItem("Current ride", "current")
        self.scope.addItem("Custom rides", "selected")
        self.scope.addItem("Quick study", "quick")
        self.scope.addItem("Standard study", "standard")
        self.scope.addItem("Extended study", "extended")
        if current_ride_key is None:
            self.scope.setCurrentIndex(self.scope.findData("quick"))
        self.scope.currentIndexChanged.connect(self._scope_changed)
        self.form.addRow("Study scope", self.scope)

        self.rides_label = QLabel()
        self.rides_label.setWordWrap(True)
        self.select_rides_button = QPushButton("Choose rides...")
        self.select_rides_button.clicked.connect(self._select_rides)
        ride_row = QWidget(); ride_layout = QHBoxLayout(ride_row)
        ride_layout.setContentsMargins(0, 0, 0, 0)
        ride_layout.addWidget(self.rides_label, 1); ride_layout.addWidget(self.select_rides_button)
        self.form.addRow("Ride(s)", ride_row)

        self.repeats = QSpinBox(); self.repeats.setRange(1, 10); self.repeats.setValue(3)
        self.repeats.setToolTip("Measured dynamic source recordings per ride")
        self.form.addRow("Measured recordings", self.repeats)
        self.warmups = QSpinBox(); self.warmups.setRange(0, 5); self.warmups.setValue(0)
        self.form.addRow("Warm-up recordings", self.warmups)
        self.repeats.valueChanged.connect(self._update_estimate)
        self.warmups.valueChanged.connect(self._update_estimate)

        self.estimate_label = QLabel()
        self.estimate_label.setWordWrap(True)
        self.estimate_label.setToolTip(
            "Approximate ride-running time. Uses installed canonical ride trajectories where available, "
            "otherwise the existing timing-study duration hints. Loading, reset, retries and optional "
            "uncertainty-extension recordings are not included."
        )
        self.form.addRow("Estimated capture time", self.estimate_label)

        self.conditions = QLineEdit()
        self.conditions.setText(default_test_conditions())
        self.conditions.setPlaceholderText("Normal deployed configuration; CPU/GPU detection unavailable")
        self.conditions.setToolTip(
            "Descriptive provenance, prefilled with this PC's detected CPU/GPU where available. "
            "Edit it for unusual graphics/background-load conditions. VR state is recorded automatically."
        )
        self.form.addRow("Test conditions (optional)", self.conditions)
        self.extend_uncertain = QCheckBox("Add up to two recordings when coverage/drift/gaps warrant it")
        self.extend_uncertain.toggled.connect(self._update_estimate)
        self.form.addRow("Extend uncertain study", self.extend_uncertain)

        self.output = QLineEdit(str(DEFAULT_OUTPUT_ROOT)); self.output.setReadOnly(True)
        self.output_browse = QPushButton("Browse...")
        self.output_browse.clicked.connect(self._browse_output)
        output_row = QWidget(); output_layout = QHBoxLayout(output_row)
        output_layout.setContentsMargins(0, 0, 0, 0)
        output_layout.addWidget(self.output, 1); output_layout.addWidget(self.output_browse)
        self.form.addRow("Output", output_row)
        layout.addWidget(form_host)

        note = QLabel(
            "The selected Ride Analyzer candidate is not used. Dynamic source recordings capture the "
            "simulator trajectory so the same study can support different later motion configurations. "
            "Video is not recorded by this capture workflow."
        )
        note.setWordWrap(True)
        note.setObjectName("dynamicSourceCaptureNote")
        layout.addWidget(note)

        self.progress = QPlainTextEdit(); self.progress.setReadOnly(True)
        self.progress.setPlaceholderText("Capture progress appears here after Start capture.")
        layout.addWidget(self.progress, 1)

        self.status = QLabel("Ready")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        action_row = QHBoxLayout()
        self.start_button = QPushButton("Start capture")
        self.start_button.clicked.connect(self.start_capture)
        action_row.addWidget(self.start_button)
        self.abort_button = QPushButton("Abort capture")
        self.abort_button.setEnabled(False)
        self.abort_button.clicked.connect(self.abort_capture)
        action_row.addWidget(self.abort_button)
        action_row.addStretch(1)
        self.open_report_button = QPushButton("Open study report")
        self.open_report_button.setEnabled(False)
        self.open_report_button.clicked.connect(self.open_report)
        action_row.addWidget(self.open_report_button)
        self.open_recording_button = QPushButton("Open recording")
        self.open_recording_button.setEnabled(False)
        self.open_recording_button.clicked.connect(self.open_recording)
        action_row.addWidget(self.open_recording_button)
        layout.addLayout(action_row)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read_process_output)
        self.process.finished.connect(self._process_finished)
        self.process.errorOccurred.connect(self._process_error)

        self._scope_changed()
        QTimer.singleShot(0, self.refresh_api_status)

    @staticmethod
    def _api_reachable(port=15151):
        try:
            with socket.create_connection(("127.0.0.1", int(port)), timeout=0.35):
                return True
        except OSError:
            return False

    def refresh_api_status(self):
        if self.process.state() != QProcess.ProcessState.NotRunning:
            return
        reachable = self._api_reachable()
        self.api_status.setText("Connected - localhost:15151" if reachable else "Not detected - localhost:15151")
        self.api_status.setStyleSheet("color: #8fd694;" if reachable else "color: #f0b56a;")

    def _scope_changed(self):
        scope = self.scope.currentData()
        if scope in PRESET_REPEATS:
            self.repeats.setValue(PRESET_REPEATS[scope])
        elif scope in ("current", "selected"):
            self.repeats.setValue(3)
        self.select_rides_button.setVisible(scope == "selected")
        if scope == "current":
            self.rides_label.setText(readable_ride_list((self.current_ride_key,)) if self.current_ride_key else "No current ride loaded")
        elif scope == "selected":
            self.rides_label.setText(readable_ride_list(self.selected_rides) if self.selected_rides else "No rides selected")
        elif scope == "quick":
            self.rides_label.setText("Blackbird, Fenrir, Tilt Coaster")
        elif scope in ("standard", "extended"):
            keys = operator_available_ride_keys()
            names = readable_ride_list(keys) if keys else "No operator-available rides configured"
            self.rides_label.setText(names)
        if hasattr(self, "estimate_label"):
            self._update_estimate()

    def _update_estimate(self, *_args):
        try:
            estimate = estimate_plan_duration(self._plan())
        except (ValueError, OSError):
            self.estimate_label.setText("Unavailable")
            return
        if estimate.seconds <= 0 and estimate.missing:
            self.estimate_label.setText("Unavailable - no duration reference for selected rides")
            return
        minutes = max(1, round(estimate.seconds / 60.0)) if estimate.seconds > 0 else 0
        text = f"~{minutes} min" if minutes else "Unavailable"
        if estimate.missing:
            text += f" + {len(estimate.missing)} ride(s) without duration data"
        self.estimate_label.setText(text)
        details = [
            "Approximate ride-running time only; loading, reset and retries are excluded."
        ]
        if estimate.fallback:
            details.append(
                "Timing-study duration hints are used for: " + readable_ride_list(estimate.fallback) + "."
            )
        if estimate.missing:
            details.append("No duration data for: " + readable_ride_list(estimate.missing) + ".")
        if self.extend_uncertain.isChecked():
            details.append("Optional uncertainty-extension recordings are not included.")
        self.estimate_label.setToolTip(" ".join(details))

    def _select_rides(self):
        dialog = RideSelectionDialog(self.selected_rides, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.selected_rides = dialog.selected_keys()
            self._scope_changed()

    def _browse_output(self):
        selected = QFileDialog.getExistingDirectory(self, "Dynamic source output folder", self.output.text())
        if selected:
            self.output.setText(selected)

    def _plan(self):
        scope = str(self.scope.currentData())
        if scope == "current":
            rides = (self.current_ride_key,) if self.current_ride_key else ()
        elif scope == "selected":
            rides = tuple(self.selected_rides)
        else:
            rides = ()
        return DynamicSourcePlan(
            scope=scope, rides=rides, repeats=self.repeats.value(), warmups=self.warmups.value(),
            conditions=self.conditions.text(),
            extend_uncertain=self.extend_uncertain.isChecked(), output_root=Path(self.output.text()),
        )

    def _set_form_enabled(self, enabled):
        for widget in (self.scope, self.select_rides_button, self.repeats, self.warmups,
                       self.conditions, self.extend_uncertain,
                       self.output_browse, self.api_refresh):
            widget.setEnabled(enabled)
        self.buttons.button(QDialogButtonBox.StandardButton.Close).setEnabled(enabled)

    def start_capture(self):
        try:
            plan = self._plan()
            args = plan.command_arguments()
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Dynamic study not started", str(exc))
            return
        if not self._api_reachable():
            self.refresh_api_status()
            QMessageBox.warning(
                self, "NoLimits 2 API not available",
                "NoLimits 2 was not detected on localhost port 15151. Start NoLimits 2, enable its API, "
                "and ensure another controller is not using the connection, then try again."
            )
            return
        try:
            Path(plan.output_root).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, "Dynamic study not started", f"Cannot create the output folder:\n{exc}")
            return
        self.progress.clear(); self.session_dir = None; self.report_path = None
        self.open_recording_path = None; self._partial = ""; self._aborting = False
        self._capture_scope = plan.scope
        estimate = estimate_plan_duration(plan)
        self._estimated_remaining_s = estimate.seconds if estimate.seconds > 0 and not estimate.missing else None
        self._active_estimate_duration_s = None
        self.open_report_button.setEnabled(False); self.open_recording_button.setEnabled(False)
        self._set_form_enabled(False)
        self.start_button.setEnabled(False); self.abort_button.setEnabled(True)
        self.status.setText("Dynamic study running. Keep NoLimits in the intended test configuration.")
        self.capture_started.emit()
        self.process.setWorkingDirectory(str(ROOT))
        self.process.start(sys.executable, args)

    def _capture_started_line(self, line):
        event = capture_start_event(line)
        if event is None:
            return
        park_key, repeat, warmup = event
        if self._estimated_remaining_s is not None and self._active_estimate_duration_s is not None:
            self._estimated_remaining_s = max(0.0, self._estimated_remaining_s - self._active_estimate_duration_s)
        duration, _source = typical_ride_duration_s(park_key)
        self._active_estimate_duration_s = duration
        ride_name = readable_ride_list((park_key,)) or park_key
        kind = "warm-up" if warmup else "dynamic source"
        if self._estimated_remaining_s is not None:
            minutes = max(1, round(self._estimated_remaining_s / 60.0))
            self.estimate_label.setText(f"~{minutes} min remaining")
            self.status.setText(f"{ride_name} {kind} {repeat} started - estimated ~{minutes} min remaining.")
        else:
            self.estimate_label.setText("Remaining time unavailable")
            self.status.setText(f"{ride_name} {kind} {repeat} started.")

    def _read_process_output(self):
        data = bytes(self.process.readAllStandardOutput()).decode("utf-8", "replace")
        text = self._partial + data
        lines = text.splitlines(keepends=True)
        self._partial = ""
        if lines and not lines[-1].endswith(("\n", "\r")):
            self._partial = lines.pop()
        for raw in lines:
            line = raw.rstrip("\r\n")
            if not line:
                self.progress.appendPlainText("")
                continue
            self._capture_started_line(line)
            session = session_path_from_output(line)
            if session is not None:
                self.session_dir = session
            report = report_path_from_output(line)
            if report is not None:
                self.report_path = report
            self.progress.appendPlainText(translated_capture_output(line))
        bar = self.progress.verticalScrollBar(); bar.setValue(bar.maximum())

    def _process_error(self, error):
        if self.process.state() == QProcess.ProcessState.NotRunning:
            name = getattr(error, "name", str(error))
            self.status.setText(f"Dynamic study process error: {name}")
            self._set_form_enabled(True)
            self.start_button.setEnabled(True)
            self.abort_button.setEnabled(False)

    def _process_finished(self, exit_code, _exit_status):
        if self._partial:
            line = self._partial; self._partial = ""
            self.progress.appendPlainText(translated_capture_output(line))
            session = session_path_from_output(line)
            if session is not None:
                self.session_dir = session
            report = report_path_from_output(line)
            if report is not None:
                self.report_path = report
        self._set_form_enabled(True)
        self.start_button.setEnabled(True); self.abort_button.setEnabled(False)
        if self.session_dir is not None and self._capture_scope:
            annotate_session_scope(self.session_dir, self._capture_scope)
        if self.session_dir is not None and self.report_path is None:
            candidate = self.session_dir / "report" / "report.html"
            if candidate.is_file():
                self.report_path = candidate
        recordings = source_recordings(self.session_dir)
        self.open_report_button.setEnabled(bool(self.report_path and self.report_path.is_file()))
        self.open_recording_button.setEnabled(bool(recordings))
        if self._aborting:
            self.estimate_label.setText("Capture aborted")
            self.status.setText("Capture aborted. Completed recordings remain in the dynamic study; the current recording may be incomplete.")
        elif exit_code == 0:
            self.estimate_label.setText("Complete")
            self.status.setText(f"Dynamic study complete. {len(recordings)} usable dynamic source recording(s).")
        else:
            self.estimate_label.setText("Finished with errors")
            self.status.setText(
                f"Dynamic study finished with errors (exit code {exit_code}). "
                f"{len(recordings)} completed recording(s) remain available."
            )
        self.refresh_api_status()

    def abort_capture(self):
        if self.process.state() == QProcess.ProcessState.NotRunning:
            return
        answer = QMessageBox.question(
            self, "Abort dynamic study?",
            "Abort the capture process now? Completed recordings are retained, but the current recording may be incomplete.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._aborting = True
        self.abort_button.setEnabled(False)
        self.status.setText("Aborting dynamic study...")
        self.process.kill()

    def open_report(self):
        if self.report_path and self.report_path.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.report_path)))

    def open_recording(self):
        recordings = source_recordings(self.session_dir)
        if not recordings:
            QMessageBox.information(self, "No completed recordings", "This study does not yet contain a completed measured recording.")
            return
        if len(recordings) == 1:
            selected = recordings[0]
        else:
            labels = [str(p.relative_to(self.session_dir)) if self.session_dir else p.name for p in recordings]
            label, ok = QInputDialog.getItem(self, "Open dynamic source recording", "Recording", labels, 0, False)
            if not ok:
                return
            selected = recordings[labels.index(label)]
        self.open_recording_path = selected
        self.accept()

    def reject(self):
        if self.process.state() != QProcess.ProcessState.NotRunning:
            QMessageBox.information(self, "Dynamic study running", "Abort or allow the current dynamic study to finish before closing this dialog.")
            return
        super().reject()

    def closeEvent(self, event):
        if self.process.state() != QProcess.ProcessState.NotRunning:
            QMessageBox.information(
                self, "Dynamic study running",
                "Abort or allow the current dynamic study to finish before closing this dialog."
            )
            event.ignore()
            return
        super().closeEvent(event)
