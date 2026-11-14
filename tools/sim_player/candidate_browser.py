# candidate_browser.py ver 3
"""Qt6 candidate browser: select any two optimizer outputs for A/B tuning."""
from __future__ import annotations
from pathlib import Path
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout,
)

from .candidate_review import discover_run_dirs, load_run_index, prepare_session, shared_captures
from .platform_selection import platform_choices
from sims.nolimits2 import ride_config


class CandidateBrowserDialog(QDialog):
    def __init__(self, directory: Path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Review optimiser candidates")
        self.resize(970, 630)
        self.session = None
        self.run_data = None
        self.directory = Path(directory).expanduser()
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Choose a platform and park; any two optimiser outputs can be A and B. "
                                "Strict/5% are source labels, not restrictions on your edits."))
        directory_row = QHBoxLayout()
        self.directory_label = QLineEdit(str(self.directory))
        self.directory_label.setReadOnly(True)
        directory_row.addWidget(self.directory_label, 1)
        self.browse_button = QPushButton("Choose results folder…")
        self.browse_button.clicked.connect(self._browse)
        directory_row.addWidget(self.browse_button)
        layout.addLayout(directory_row)
        filters = QGridLayout()
        self.run_box = QComboBox()
        self.platform_box = QComboBox()
        self.park_box = QComboBox()
        for key, label in platform_choices():
            if key in ("chair_v3", "platform_v2"):
                self.platform_box.addItem(label, key)
        filters.addWidget(QLabel("Optimiser run"), 0, 0)
        filters.addWidget(self.run_box, 1, 0)
        filters.addWidget(QLabel("Platform"), 0, 1)
        filters.addWidget(self.platform_box, 1, 1)
        filters.addWidget(QLabel("Park"), 0, 2)
        filters.addWidget(self.park_box, 1, 2)
        filters.setColumnStretch(0, 3)
        filters.setColumnStretch(1, 1)
        filters.setColumnStretch(2, 2)
        layout.addLayout(filters)
        layout.addWidget(QLabel("All candidates for this platform / park (select A and optional B below):"))
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(("Optimiser result", "Cue", "Midpoint Δ", "Surge / Sway / Heave / Roll / Pitch / Yaw", "Max excess", "Captures", "Variant source"))
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().hide()
        self.table.setColumnWidth(0, 117)
        self.table.setColumnWidth(1, 88)
        self.table.setColumnWidth(2, 105)
        self.table.setColumnWidth(3, 260)
        self.table.setColumnWidth(4, 95)
        self.table.setColumnWidth(5, 70)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)
        assignment = QFormLayout()
        self.a_box, self.b_box, self.capture_box = QComboBox(), QComboBox(), QComboBox()
        assignment.addRow("Candidate A", self.a_box)
        assignment.addRow("Candidate B (optional)", self.b_box)
        assignment.addRow("Common recording", self.capture_box)
        layout.addLayout(assignment)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Open for review")
        self.buttons.accepted.connect(self._accept_selected)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.run_box.currentIndexChanged.connect(self._run_changed)
        self.platform_box.currentIndexChanged.connect(self._platform_changed)
        self.park_box.currentIndexChanged.connect(self._park_changed)
        self.a_box.currentIndexChanged.connect(self._selection_changed)
        self.b_box.currentIndexChanged.connect(self._selection_changed)
        self._load_runs()

    def _browse(self):
        new = QFileDialog.getExistingDirectory(self, "Choose optimiser results folder",
                                               str(self.directory))
        if new:
            self.directory = Path(new)
            self.directory_label.setText(new)
            self._load_runs()

    def _load_runs(self):
        self.run_box.blockSignals(True)
        self.run_box.clear()
        for directory in discover_run_dirs(self.directory):
            self.run_box.addItem(directory.name, str(directory))
        self.run_box.blockSignals(False)
        self._run_changed()

    def _run_changed(self, *_args):
        self.run_data = None
        chosen = self.run_box.currentData()
        if chosen:
            try:
                self.run_data = load_run_index(Path(chosen))
            except (OSError, ValueError) as exc:
                self.status_label.setText(f"Cannot read optimiser run: {exc}")
        self._platform_changed()

    def _platform_changed(self, *_args):
        current = self.park_box.currentData()
        self.park_box.blockSignals(True)
        self.park_box.clear()
        if self.run_data:
            platform = self.platform_box.currentData()
            parks = sorted({c.park for c in self.run_data.candidates
                            if c.platform == platform and c.park not in ride_config.ANALYZER_HIDDEN_PARKS})
            for park in parks:
                self.park_box.addItem(ride_config.PARKS[park]["name"], park)
            idx = self.park_box.findData(current)
            self.park_box.setCurrentIndex(max(0, idx))
        self.park_box.blockSignals(False)
        self._park_changed()

    def _park_changed(self, *_args):
        choices = self._choices()
        self.table.setRowCount(len(choices))
        for i, c in enumerate(choices):
            target = "Strict" if c.target == "strict" else "5%" if c.target == "relaxed5" else c.target
            values = (f"{target} {c.rank:02d}", c.cue, f"{c.neutral_mm:+g} mm",
                      " / ".join(str(int(g)) for g in c.gains),
                      f"{c.maximum_excess_mm:.2f} mm", str(len(c.captures)), c.label)
            for j, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setToolTip(c.label)
                if j < 6:
                    cell.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
                self.table.setItem(i, j, cell)
        for box in (self.a_box, self.b_box):
            box.blockSignals(True)
            box.clear()
        self.b_box.addItem("No B candidate — tune A only", None)
        for idx, c in enumerate(choices):
            self.a_box.addItem(c.name, idx)
            self.b_box.addItem(c.name, idx)
        if len(choices) > 1:
            target_b = next((i for i, c in enumerate(choices) if c.target == "relaxed5"), 1)
            self.b_box.setCurrentIndex(target_b + 1)
        self.a_box.blockSignals(False)
        self.b_box.blockSignals(False)
        self._selection_changed()

    def _choices(self):
        if self.run_data is None:
            return ()
        platform, park = self.platform_box.currentData(), self.park_box.currentData()
        return tuple(c for c in self.run_data.candidates if c.platform == platform and c.park == park)

    def _selection_changed(self, *_args):
        choices = self._choices()
        first_idx, second_idx = self.a_box.currentData(), self.b_box.currentData()
        first = choices[first_idx] if isinstance(first_idx, int) and first_idx < len(choices) else None
        second = choices[second_idx] if isinstance(second_idx, int) and second_idx < len(choices) else None
        previous = self.capture_box.currentData()
        self.capture_box.clear()
        if first is None:
            self._set_status("Choose a folder containing a completed optimiser run (index.csv).", False)
            return
        if first is second:
            self._set_status("Choose two different results, or select 'No B candidate'.", False)
            return
        try:
            captures = shared_captures(first, second)
        except ValueError as exc:
            self._set_status(str(exc), False)
            return
        for c in captures:
            self.capture_box.addItem(c.original_name + "  [" + c.source_id[:8] + "]", c.source_id)
        idx = self.capture_box.findData(previous)
        self.capture_box.setCurrentIndex(max(0, idx))
        issue_count = len(self.run_data.issues) if self.run_data else 0
        self._set_status(f"{len(choices)} candidate(s) available. " +
                         (f"{issue_count} invalid index row(s) ignored. " if issue_count else "") +
                         ("Select the same capture for both candidates." if second else "Reviewing one candidate."),
                         bool(captures))

    def _set_status(self, text, enabled):
        self.status_label.setText(text)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(enabled)

    def _accept_selected(self):
        choices = self._choices()
        a, b = self.a_box.currentData(), self.b_box.currentData()
        if a is None or (b is not None and a == b) or not self.capture_box.currentData():
            return
        try:
            self.session = prepare_session(choices[a], choices[b] if b is not None else None,
                                           self.capture_box.currentData(), self.run_data.root)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            QMessageBox.warning(self, "Candidate could not be opened", str(exc))
            return
        changed = sorted({module for slot in self.session.slots.values()
                          for module in slot.source_bundle.get("_model_changed", [])})
        if changed:
            question = ("The optimisation used different POC model files. Prior strict/5% qualification "
                        "no longer applies; Ride Analyzer will recalculate using the installed code.\n\n"
                        + "\n".join(changed[:10]) + ("\n…" if len(changed) > 10 else "") +
                        "\n\nContinue to review?")
            if QMessageBox.question(self, "Model differs from optimiser run", question) != QMessageBox.StandardButton.Yes:
                return
        self.accept()
