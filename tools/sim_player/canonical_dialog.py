# canonical_dialog.py ver 2
"""One-time canonical ride-video import and calibration, kept out of the toolbar."""
from __future__ import annotations
from pathlib import Path
from PyQt6.QtCore import QThread, pyqtSignal, Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QHBoxLayout, QLabel, QComboBox, QLineEdit,
    QPushButton, QFileDialog, QCheckBox, QDoubleSpinBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QTabWidget, QWidget, QMessageBox,
)
from .canonical_media import (
    ROOT, load_media, initial_calibration, register_media, update_calibration,
    companion_telemetry,
)
from .media_trajectory import MediaCancelled, recording_key


class ImportWorker(QThread):
    loaded = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress = pyqtSignal(str)

    def __init__(self, key, csv, video, anchors, calibrated, parent=None):
        super().__init__(parent)
        self.args = (key, csv, video, anchors, calibrated)

    def run(self):
        try:
            key, csv, video, anchors, calibrated = self.args
            media = register_media(
                key, csv, video, anchors=anchors, calibrated=calibrated,
                confirmed_pair=True, cancel=self.isInterruptionRequested,
                progress=self.progress.emit,
            )
        except Exception as exc:
            self.failed.emit(
                str(exc) if isinstance(exc, MediaCancelled)
                else f"{type(exc).__name__}: {exc}"
            )
        else:
            self.loaded.emit(media)


class CanonicalVideoDialog(QDialog):
    library_changed = pyqtSignal(str)

    def __init__(self, recording=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Ride reference video - shared across candidates")
        self.resize(810, 650)
        self.worker = None
        self.media = None
        self._initial_verified = False

        layout = QVBoxLayout(self)
        self.ride = QComboBox()
        from sims.nolimits2 import ride_config
        for key, info in ride_config.PARKS.items():
            if key in ride_config.ANALYZER_HIDDEN_PARKS:
                continue
            self.ride.addItem(info["name"], key)
        if recording:
            try:
                self.ride.setCurrentIndex(
                    max(0, self.ride.findData(recording_key(recording)))
                )
            except ValueError:
                pass
        row = QHBoxLayout()
        row.addWidget(QLabel("Ride"))
        row.addWidget(self.ride, 1)
        layout.addLayout(row)

        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.info)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        setup = QWidget()
        form = QFormLayout(setup)
        intro = QLabel(
            "Choose the reference video. Ride Analyzer automatically uses a telemetry CSV "
            "with the same base filename in the same folder (for example ride.mp4 + "
            "ride.csv). Existing videos can be reused; no new capture is needed when "
            "only motion candidates change. Files are copied, not moved."
        )
        intro.setWordWrap(True)
        form.addRow(intro)

        self.video = QLineEdit()
        host = QWidget()
        r = QHBoxLayout(host)
        r.setContentsMargins(0, 0, 0, 0)
        r.addWidget(self.video, 1)
        button = QPushButton("Browse...")
        button.clicked.connect(self._browse_video)
        r.addWidget(button)
        form.addRow("Video", host)

        self.csv = QLineEdit()
        self.csv.setReadOnly(True)
        self.csv.setPlaceholderText("Selected automatically from the video filename")
        form.addRow("Original telemetry CSV", self.csv)

        self.pair_status = QLabel("Select a video to locate its matching telemetry CSV.")
        self.pair_status.setWordWrap(True)
        form.addRow(self.pair_status)

        self.offset = QDoubleSpinBox()
        self.offset.setRange(-600.0, 600.0)
        self.offset.setDecimals(3)
        self.offset.setSuffix(" s")
        self.offset.setToolTip(
            "Video timestamp at original telemetry elapsed zero. Positive = video "
            "began before telemetry."
        )
        self.offset.setEnabled(False)
        form.addRow("Initial video offset", self.offset)

        self.calibration_hint = QLabel(
            "After a matching CSV is found, Ride Analyzer reads any saved calibration; "
            "otherwise the offset remains an estimate."
        )
        self.calibration_hint.setWordWrap(True)
        form.addRow(self.calibration_hint)

        self.import_button = QPushButton("Import as this ride's reference video")
        self.import_button.clicked.connect(self._import)
        self.import_button.setEnabled(False)
        form.addRow(self.import_button)
        self.tabs.addTab(setup, "Reference video")

        cal = QWidget()
        cl = QVBoxLayout(cal)
        label = QLabel(
            "Calibration relates the ORIGINAL reference CSV clock to video timestamps. "
            "One row is a constant offset. Add a second or later anchor only to correct "
            "measured video-clock drift. These times do not change any motion or kinematics."
        )
        label.setWordWrap(True)
        cl.addWidget(label)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(
            ["Reference CSV elapsed (s)", "Video timestamp (s)"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        cl.addWidget(self.table, 1)

        er = QHBoxLayout()
        self.events = QComboBox()
        er.addWidget(self.events, 1)
        add_event = QPushButton("Add event anchor")
        add_event.clicked.connect(self._add_event)
        er.addWidget(add_event)
        cl.addLayout(er)

        self.calibration_verified = QCheckBox(
            "I have checked this calibration against a visible video event"
        )
        self.calibration_verified.setToolTip(
            "Tick only after visually comparing at least one identifiable event in the "
            "reference video."
        )
        self.calibration_verified.hide()
        cl.addWidget(self.calibration_verified)

        buttons = QHBoxLayout()
        add = QPushButton("Add row")
        add.clicked.connect(lambda: self._add_row())
        remove = QPushButton("Remove selected")
        remove.clicked.connect(self._remove)
        self.save = QPushButton("Save calibration")
        self.save.clicked.connect(self._save)
        for b in (add, remove, self.save):
            buttons.addWidget(b)
        cl.addLayout(buttons)

        warning = QLabel(
            "Event names locate the telemetry event only. Inspect the corresponding "
            "video to enter its actual timestamp. Saving without the checkbox leaves "
            "the calibration marked as estimated."
        )
        warning.setWordWrap(True)
        cl.addWidget(warning)
        self.tabs.addTab(cal, "Calibration")

        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        bottom = QHBoxLayout()
        self.cancel = QPushButton("Cancel import")
        self.cancel.clicked.connect(self._cancel)
        self.cancel.hide()
        bottom.addWidget(self.cancel)
        bottom.addStretch(1)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.accept)
        bottom.addWidget(self.close_button)
        layout.addLayout(bottom)

        self.ride.currentIndexChanged.connect(self._refresh)
        if (
            recording
            and not getattr(recording, "dynamic_profile", None)
            and recording.video_path
        ):
            self.video.setText(str(recording.video_path))
            self._resolve_video_pair(show_error=False)
        self._refresh()

    def _browse_video(self):
        start = str(Path(self.video.text()).parent) if self.video.text() else ""
        value, _ = QFileDialog.getOpenFileName(
            self,
            "Select reference video",
            start,
            "Video (*.mp4 *.mkv *.mov *.avi *.webm)",
        )
        if value:
            self.video.setText(value)
            self._resolve_video_pair(show_error=True)

    def _resolve_video_pair(self, *, show_error=False):
        self.csv.clear()
        self.offset.setEnabled(False)
        self.import_button.setEnabled(False)
        self._initial_verified = False
        self.pair_status.setText("Select a video to locate its matching telemetry CSV.")
        if not self.video.text():
            return False
        try:
            csv_path = companion_telemetry(Path(self.video.text()))
        except (OSError, ValueError) as exc:
            message = str(exc)
            self.pair_status.setText(message)
            self.calibration_hint.setText(
                "Reference cannot be imported until the matching telemetry CSV is present."
            )
            if show_error:
                QMessageBox.warning(self, "Matching telemetry not found", message)
            return False
        self.csv.setText(str(csv_path))
        self.pair_status.setText(f"Automatically matched telemetry: {csv_path.name}")
        self.offset.setEnabled(True)
        self.import_button.setEnabled(True)
        self._read_initial()
        return True

    def _read_initial(self):
        p = Path(self.csv.text())
        if not p.is_file():
            return
        try:
            anchors, verified, note = initial_calibration(p)
            self.offset.setValue(anchors[0][1] - anchors[0][0])
            self._initial_verified = bool(verified)
            if verified:
                note = note + " (already visually checked/saved)"
            else:
                note = note + " (estimate until checked against the video)"
            self.calibration_hint.setText(note)
        except (OSError, ValueError) as exc:
            self._initial_verified = False
            self.calibration_hint.setText(str(exc))

    def _refresh(self):
        self.media = None
        try:
            self.media = load_media(self.ride.currentData())
        except (OSError, ValueError, KeyError) as exc:
            self.status.setText(str(exc))
        self.table.setRowCount(0)
        self.events.clear()
        if self.media:
            m = self.media
            state = (
                "checked"
                if m.data["calibration"]["verified_by_user"]
                else "estimated - verify visually"
            )
            self.info.setText(
                f"Reference: {m.video_path.name}\n{m.manifest_path.parent}\n"
                f"Calibration: {state}"
            )
            for a, b in m.anchors:
                self._add_row(a, b)
            self.events.addItem("First sustained movement", m.trajectory.first_motion)
            for e in m.trajectory.events:
                self.events.addItem(
                    f'{e["name"]} - {e["seconds"]:.3f}s', e["seconds"]
                )
            self.calibration_verified.setChecked(
                bool(m.data["calibration"]["verified_by_user"])
            )
            self.calibration_verified.show()
            self.save.setEnabled(True)
        else:
            self.info.setText(
                "No reference registered. Destination: "
                + str(ROOT / self.ride.currentData())
            )
            self.calibration_verified.hide()
            self.calibration_verified.setChecked(False)
            self.save.setEnabled(False)

    def _add_row(self, a=0.0, b=0.0):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(f"{float(a):.6f}"))
        self.table.setItem(row, 1, QTableWidgetItem(f"{float(b):.6f}"))

    def _add_event(self):
        if self.media and self.events.currentData() is not None:
            t = float(self.events.currentData())
            self._add_row(
                t, float(self.media.video_time(t - self.media.trajectory.first_motion))
            )

    def _remove(self):
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)

    def _save(self):
        if not self.media:
            return
        try:
            anchors = sorted(
                [
                    [float(self.table.item(r, c).text()) for c in range(2)]
                    for r in range(self.table.rowCount())
                ]
            )
            self.media = update_calibration(
                self.media,
                anchors,
                calibrated=self.calibration_verified.isChecked(),
            )
        except (OSError, ValueError, AttributeError, TypeError) as exc:
            QMessageBox.warning(self, "Calibration not saved", str(exc))
            return
        state = "visually checked" if self.calibration_verified.isChecked() else "estimated"
        self.status.setText(
            f"Reference calibration saved as {state}. Original video and telemetry are unchanged."
        )
        self.library_changed.emit(self.media.key)
        self._refresh()

    def _import(self):
        if self.worker:
            return
        if not self._resolve_video_pair(show_error=True):
            return
        if self.media and QMessageBox.question(
            self,
            "Replace active reference?",
            "Import a new reference for this ride? Previous imported assets will be retained.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        self.worker = ImportWorker(
            self.ride.currentData(),
            self.csv.text(),
            self.video.text(),
            [[0.0, self.offset.value()]],
            self._initial_verified,
            self,
        )
        self.worker.loaded.connect(self._imported)
        self.worker.failed.connect(self._failed)
        self.worker.progress.connect(self.status.setText)
        self.worker.finished.connect(self._finished)
        self.tabs.setEnabled(False)
        self.ride.setEnabled(False)
        self.close_button.setEnabled(False)
        self.cancel.setEnabled(True)
        self.cancel.show()
        self.status.setText("Importing original video and automatically matched telemetry...")
        self.worker.start()

    def _imported(self, media):
        self.status.setText("Reference imported. It is reused automatically for this ride.")
        self.library_changed.emit(media.key)
        self._refresh()

    def _failed(self, message):
        self.status.setText(message)
        QMessageBox.warning(self, "Reference not imported", message)

    def _finished(self):
        old = self.worker
        self.worker = None
        if old:
            old.deleteLater()
        self.tabs.setEnabled(True)
        self.ride.setEnabled(True)
        self.close_button.setEnabled(True)
        self.cancel.hide()

    def _cancel(self):
        if self.worker:
            self.worker.requestInterruption()
            self.cancel.setEnabled(False)

    def reject(self):
        if self.worker:
            self._cancel()
            return
        super().reject()

    def closeEvent(self, event):
        if self.worker:
            self._cancel()
            event.ignore()
            return
        super().closeEvent(event)
