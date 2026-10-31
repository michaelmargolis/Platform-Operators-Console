# options_dialog.py ver 1
"""Qt6 preferences dialog for the offline sim_player.

Video keeps its source aspect ratio automatically; there is deliberately no
aspect ratio control. The v7 Settings dialog contains the optional Digital Twin UDP destination.
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox,
    QLabel, QLineEdit, QMessageBox, QSpinBox, QVBoxLayout,
)
from .settings import VisualizerSettings


class PlayerOptionsDialog(QDialog):
    def __init__(self, preferences: VisualizerSettings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("sim_player — Settings")
        self.setMinimumWidth(390)
        self._result: VisualizerSettings | None = None
        layout = QVBoxLayout(self)
        visualizer = QGroupBox("MDX Platform Twin (UDP)")
        form = QFormLayout(visualizer)
        # A high-visibility On/Off selector avoids an easily overlooked
        # checkbox (especially with dark theme/Windows checkbox rendering).
        self.enabled_box = QComboBox()
        self.enabled_box.addItem("Off — no UDP output", False)
        self.enabled_box.addItem("On — send replay to Visualizer", True)
        self.enabled_box.setCurrentIndex(1 if preferences.enabled else 0)
        form.addRow("Visualizer output:", self.enabled_box)
        self.address = QLineEdit(preferences.ip_address)
        self.address.setPlaceholderText("127.0.0.1")
        form.addRow("IP address:", self.address)
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(preferences.port)
        form.addRow("UDP port:", self.port)
        self.enabled_box.currentIndexChanged.connect(self._enabled_changed)
        self._enabled_changed()
        layout.addWidget(visualizer)
        explanation = QLabel(
            "Uses POC_TWIN UDP packets with calculated muscle lengths and "
            "zero pressures. No hardware control. Pausing holds the current "
            "pose; disabling the output stops transmission."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_validated)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _enabled_changed(self, *_args):
        active = bool(self.enabled_box.currentData())
        self.address.setEnabled(active)
        self.port.setEnabled(active)

    def _accept_validated(self):
        try:
            self._result = VisualizerSettings(
                bool(self.enabled_box.currentData()), self.address.text(), self.port.value()
            ).validate()
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid Visualizer destination", str(exc))
            return
        self.accept()

    def selected_settings(self) -> VisualizerSettings:
        if self._result is None:
            raise RuntimeError("Settings dialog was not accepted")
        return self._result
