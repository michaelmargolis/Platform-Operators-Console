# anomaly_panel.py ver 1
"""Qt6 presentation-only dock for precomputed kinematic anomaly episodes."""
from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView, QDoubleSpinBox, QHBoxLayout, QHeaderView, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)
from .panels import clock_label


class AnomalyPanel(QWidget):
    seek_requested = pyqtSignal(float)
    examine_requested = pyqtSignal(object)
    thresholds_changed = pyqtSignal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.events = ()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Muscle step >"))
        self.step_box = QDoubleSpinBox()
        self.step_box.setRange(0.1, 1000)
        self.step_box.setDecimals(1)
        self.step_box.setValue(50)
        self.step_box.setSuffix(" mm")
        self.step_box.setToolTip("Final permitted muscle-length change between successive telemetry samples")
        controls.addWidget(self.step_box)
        controls.addSpacing(8)
        controls.addWidget(QLabel("Floor <"))
        self.floor_box = QDoubleSpinBox()
        self.floor_box.setRange(0, 1000)
        self.floor_box.setDecimals(1)
        self.floor_box.setValue(30)
        self.floor_box.setSuffix(" mm")
        self.floor_box.setToolTip("Applied modeled clearance warning; does not alter POC's floor limit")
        controls.addWidget(self.floor_box)
        controls.addStretch(1)
        layout.addLayout(controls)
        self.step_box.valueChanged.connect(self._threshold_changed)
        self.floor_box.valueChanged.connect(self._threshold_changed)

        description = QLabel(
            "Requests, workspace interventions, final clamps, large steps and floor warnings. "
            "Select to seek; Examine sets A/B around the event and starts looping."
        )
        description.setWordWrap(True)
        layout.addWidget(description)
        self.table = QTableWidget(0, 5)
        self.table.setObjectName("anomalyTable")
        self.table.setHorizontalHeaderLabels(("Time", "Anomaly", "Muscle", "Peak / value", "Frames"))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().hide()
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.cellDoubleClicked.connect(lambda row, _col: self._examine(row))
        layout.addWidget(self.table, 1)
        footer = QHBoxLayout()
        self.summary = QLabel("No recording loaded")
        self.summary.setWordWrap(True)
        footer.addWidget(self.summary, 1)
        self.examine_button = QPushButton("Examine event")
        self.examine_button.setToolTip("Set A/B to event ±1 second, select Loop range and play")
        self.examine_button.setEnabled(False)
        self.examine_button.clicked.connect(self._examine_selected)
        footer.addWidget(self.examine_button)
        layout.addLayout(footer)
        self.setMinimumSize(310, 200)

    def _threshold_changed(self, *_args):
        self.thresholds_changed.emit(self.step_box.value(), self.floor_box.value())

    def set_events(self, events):
        self.events = tuple(events)
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.events))
        for i, event in enumerate(self.events):
            if event.category == "low_clearance":
                value = event.details.split(" (")[0].replace("Applied clearance ", "")
            elif event.category == "requested_floor":
                value = "Floor limit"
            else:
                value = f"{event.magnitude:.1f} {event.units}"
            fields = (clock_label(event.seconds), event.name, event.muscles,
                      value, str(event.frame_count))
            for col, text in enumerate(fields):
                item = QTableWidgetItem(text)
                item.setToolTip(event.details +
                                f"\nEpisode: {clock_label(event.start_s)} to {clock_label(event.end_s)}")
                self.table.setItem(i, col, item)
        self.table.clearSelection()
        self.table.setCurrentCell(-1, -1)
        self.table.blockSignals(False)
        self.examine_button.setEnabled(False)
        self.summary.setText(
            f"{len(self.events)} grouped anomaly intervals · modeled commands, not measured motion"
            if self.events else "No anomalies at these thresholds (or no capture loaded)"
        )

    def select_event(self, event):
        try:
            row = self.events.index(event)
        except ValueError:
            return
        self.table.blockSignals(True)
        self.table.setCurrentCell(row, 0)
        self.table.selectRow(row)
        self.table.scrollToItem(self.table.item(row, 0))
        self.table.blockSignals(False)
        self.examine_button.setEnabled(True)

    def _selected_row(self):
        row = self.table.currentRow()
        return row if 0 <= row < len(self.events) else None

    def _selection_changed(self):
        row = self._selected_row()
        self.examine_button.setEnabled(row is not None)
        if row is not None:
            self.seek_requested.emit(self.events[row].seconds)

    def _examine_selected(self):
        row = self._selected_row()
        if row is not None:
            self._examine(row)

    def _examine(self, row):
        if 0 <= row < len(self.events):
            self.examine_requested.emit(self.events[row])
