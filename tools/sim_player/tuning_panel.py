# tuning_panel.py ver 2
"""Presentation-only manual axis-gain tuning dock for offline sim_player."""
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QGridLayout, QHBoxLayout, QLabel, QPushButton, QSlider, QDoubleSpinBox,
    QVBoxLayout, QWidget, QScrollArea, QFrame,
)

from .tuning import AXIS_NAMES, DEFAULT_GAINS, validate_gain_percent


class MotionTuningPanel(QScrollArea):
    gain_changed = pyqtSignal(object)  # tuple of six research percentages
    neutral_changed = pyqtSignal(float)  # physical platform height in mm
    reset_requested = pyqtSignal()
    baseline_requested = pyqtSignal()
    restore_requested = pyqtSignal()
    save_requested = pyqtSignal()
    load_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(10, 9, 10, 9)
        layout.setSpacing(9)
        help_label = QLabel(
            "Axis gain relative to the saved POC setting (100% = unchanged). "
            "Analysis runs after releasing a slider. No hardware output."
        )
        help_label.setWordWrap(True)
        layout.addWidget(help_label)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        self.sliders = []
        self.values = []
        for row, axis in enumerate(AXIS_NAMES):
            grid.addWidget(QLabel(axis), row, 0)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setObjectName("gain" + axis)
            slider.setMinimum(0)
            slider.setMaximum(200)
            slider.setSingleStep(5)
            slider.setPageStep(10)
            slider.setValue(100)
            slider.setMinimumWidth(110)
            slider.setToolTip(f"{axis} relative gain (0–200%, current saved POC gain = 100%)")
            value = QLabel("100%")
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            value.setMinimumWidth(44)
            slider.valueChanged.connect(lambda v, i=row: self._changed(i, v))
            slider.sliderReleased.connect(self._commit)
            grid.addWidget(slider, row, 1)
            grid.addWidget(value, row, 2)
            self.sliders.append(slider)
            self.values.append(value)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)

        # Ride-specific vertical translation: not the platform geometry's
        # NEUTRAL_HEIGHT_OFFSET_MM and not a change to Heave gain.
        layout.addWidget(QLabel("Ride neutral height (physical Z)"))
        neutral_row = QHBoxLayout()
        self.neutral_slider = QSlider(Qt.Orientation.Horizontal)
        self.neutral_slider.setObjectName("rideNeutralHeightDelta")
        self.neutral_slider.setRange(-100, 100)
        self.neutral_slider.setSingleStep(5)
        self.neutral_slider.setPageStep(10)
        self.neutral_slider.setTickInterval(20)
        self.neutral_slider.setToolTip(
            "Ride-only offset (mm): negative lowers platform/lengthens muscles; "
            "positive raises platform/shortens muscles. Analysis runs on release.")
        self.neutral_box = QDoubleSpinBox()
        self.neutral_box.setObjectName("rideNeutralHeightNumeric")
        self.neutral_box.setRange(-100, 100)
        self.neutral_box.setDecimals(1)
        self.neutral_box.setSingleStep(1.0)
        self.neutral_box.setSuffix(" mm")
        self.neutral_box.setToolTip("Exact ride-neutral offset; press Enter or leave the field to recalculate")
        self.neutral_box.setMinimumWidth(86)
        self.neutral_reset_button = QPushButton("Reset")
        self.neutral_reset_button.setToolTip("Return to this platform's configured neutral height")
        neutral_row.addWidget(self.neutral_slider, 1)
        neutral_row.addWidget(self.neutral_box)
        neutral_row.addWidget(self.neutral_reset_button)
        layout.addLayout(neutral_row)
        self.neutral_summary = QLabel("Platform neutral: unavailable")
        self.neutral_summary.setWordWrap(True)
        layout.addWidget(self.neutral_summary)
        self._platform_neutral = None
        self._neutral_last_sent = 0.0
        self._neutral_named_variant = False
        self._analysis_enabled = False
        self._neutral_debounce = QTimer(self)
        self._neutral_debounce.setSingleShot(True)
        self._neutral_debounce.setInterval(450)
        self._neutral_debounce.timeout.connect(self._commit_neutral)
        self.neutral_slider.valueChanged.connect(self._neutral_slider_changed)
        self.neutral_slider.sliderReleased.connect(self._commit_neutral)
        self.neutral_box.valueChanged.connect(self._neutral_numeric_changed)
        self.neutral_box.editingFinished.connect(self._commit_neutral)
        self.neutral_reset_button.clicked.connect(self._neutral_reset)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(450)
        self._debounce.timeout.connect(self._commit)
        self._suspend = False
        self._last_sent = DEFAULT_GAINS

        controls = QHBoxLayout()
        self.reset_button = QPushButton("Reset 100%")
        self.reset_button.setToolTip("Reset all six experimental gains to their saved POC values")
        self.reset_button.clicked.connect(self.reset_requested)
        controls.addWidget(self.reset_button)
        self.baseline_button = QPushButton("Set baseline")
        self.baseline_button.setToolTip("Use the currently calculated experiment as the comparison baseline")
        self.baseline_button.clicked.connect(self.baseline_requested)
        controls.addWidget(self.baseline_button)
        self.restore_button = QPushButton("Restore baseline")
        self.restore_button.setToolTip("Restore baseline cue and six experimental gains")
        self.restore_button.clicked.connect(self.restore_requested)
        controls.addWidget(self.restore_button)
        layout.addLayout(controls)

        persistence = QHBoxLayout()
        self.save_button = QPushButton("Save experiment…")
        self.save_button.clicked.connect(self.save_requested)
        persistence.addWidget(self.save_button)
        self.load_button = QPushButton("Load experiment…")
        self.load_button.clicked.connect(self.load_requested)
        persistence.addWidget(self.load_button)
        layout.addLayout(persistence)

        self.baseline_label = QLabel("Baseline: not set")
        self.baseline_label.setWordWrap(True)
        layout.addWidget(self.baseline_label)
        self.feedback = QLabel("Open a ride capture to calculate motion headroom.")
        self.feedback.setObjectName("tuningFeedback")
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)
        self.warning = QLabel(
            "The thresholds are analysis warnings, not validated actuator-rate or floor-safety limits. "
            "A geometrically valid pose may still be infeasible for tension-only muscles."
        )
        self.warning.setWordWrap(True)
        layout.addWidget(self.warning)
        layout.addStretch(1)
        self.setWidget(body)
        self.setMinimumSize(290, 215)
        self.set_analysis_enabled(False)

    def gain_percent(self):
        return tuple(float(slider.value()) for slider in self.sliders)

    def set_gains(self, gains):
        gains = validate_gain_percent(gains)
        self._suspend = True
        self._debounce.stop()
        try:
            for i, (slider, value) in enumerate(zip(self.sliders, gains)):
                slider.blockSignals(True)
                slider.setValue(round(value))
                slider.blockSignals(False)
                self.values[i].setText(f"{slider.value()}%")
        finally:
            self._suspend = False
        self._last_sent = self.gain_percent()

    def neutral_offset_mm(self):
        return float(self.neutral_box.value())

    def _neutral_summary_update(self):
        if self._platform_neutral is None:
            self.neutral_summary.setText("Platform neutral: unavailable")
        else:
            self.neutral_summary.setText(
                f"Platform neutral: {self._platform_neutral:.1f} mm   |   "
                f"Ride adjustment: {self.neutral_offset_mm():+g} mm   |   "
                f"Effective: {self._platform_neutral+self.neutral_offset_mm():.1f} mm")

    def set_neutral_reference(self, platform_neutral_mm):
        self._platform_neutral = (None if platform_neutral_mm is None
                                  else float(platform_neutral_mm))
        self._neutral_summary_update()

    def set_neutral_offset(self, value):
        from core.ride_neutral import validated_ride_neutral_delta
        offset = validated_ride_neutral_delta(value)
        self._neutral_debounce.stop()
        for widget in (self.neutral_box, self.neutral_slider):
            widget.blockSignals(True)
        try:
            self.neutral_box.setValue(offset)
            self.neutral_slider.setValue(round(offset / 5) * 5)
        finally:
            for widget in (self.neutral_box, self.neutral_slider):
                widget.blockSignals(False)
        self._neutral_last_sent = float(self.neutral_box.value())
        self._neutral_summary_update()

    def set_neutral_enabled(self, named_variant):
        self._neutral_named_variant = bool(named_variant)
        self._update_neutral_enabled()

    def _update_neutral_enabled(self):
        enabled = self._analysis_enabled and self._neutral_named_variant
        for widget in (self.neutral_slider, self.neutral_box, self.neutral_reset_button):
            widget.setEnabled(enabled)

    def _neutral_slider_changed(self, value):
        nearest = round(value / 5) * 5
        if nearest != value:
            self.neutral_slider.blockSignals(True)
            self.neutral_slider.setValue(nearest)
            self.neutral_slider.blockSignals(False)
        self.neutral_box.blockSignals(True)
        self.neutral_box.setValue(nearest)
        self.neutral_box.blockSignals(False)
        self._neutral_summary_update()
        if not self.neutral_slider.isSliderDown():
            self._neutral_debounce.start()

    def _neutral_numeric_changed(self, value):
        self.neutral_slider.blockSignals(True)
        self.neutral_slider.setValue(round(value / 5) * 5)
        self.neutral_slider.blockSignals(False)
        self._neutral_summary_update()

    def _commit_neutral(self):
        self._neutral_debounce.stop()
        if not (self._analysis_enabled and self._neutral_named_variant):
            return
        value = self.neutral_offset_mm()
        if value != self._neutral_last_sent:
            self._neutral_last_sent = value
            self.neutral_changed.emit(value)

    def _neutral_reset(self):
        self.set_neutral_offset(0)
        self.neutral_changed.emit(0.0)

    def set_analysis_enabled(self, enabled):
        self._analysis_enabled = bool(enabled)
        for control in (*self.sliders, self.reset_button, self.baseline_button,
                        self.restore_button, self.save_button, self.load_button):
            control.setEnabled(enabled)
        self._update_neutral_enabled()

    def _changed(self, index, value):
        self.values[index].setText(f"{value}%")
        if self._suspend:
            return
        if any(slider.isSliderDown() for slider in self.sliders):
            self._debounce.stop()
        else:
            # Keyboard arrows and PageUp/PageDown have no sliderReleased event.
            self._debounce.start()

    def _commit(self):
        self._debounce.stop()
        if self._suspend:
            return
        gains = self.gain_percent()
        if gains != self._last_sent:
            self._last_sent = gains
            self.gain_changed.emit(gains)

    def set_baseline_label(self, text):
        self.baseline_label.setText(text)

    def set_feedback(self, text):
        self.feedback.setText(text)
