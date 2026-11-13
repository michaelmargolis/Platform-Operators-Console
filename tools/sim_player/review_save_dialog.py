# review_save_dialog.py ver 1
"""Explicitly name and select one/two A/B working variants for live-test review."""
from __future__ import annotations
from PyQt6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFormLayout,
                             QLabel, QLineEdit, QMessageBox, QVBoxLayout, QWidget)
from sims.nolimits2 import ride_config
from .tuning import tuning_metrics
from .variant_presets import candidate_entry


def proposed_name(store, park, key):
    base = ride_config.PARKS[park]["name"]
    known = {entry["label"] for entry in store.entries()}
    for i in range(1, 1000):
        label = f"{base} [Trial {key}{' ' + str(i) if i > 1 else ''}]"
        if label not in known:
            return label
    raise ValueError("No available trial label")


class SaveReviewVariantsDialog(QDialog):
    def __init__(self, session, variant_store, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Save named variants for physical testing")
        self.setMinimumWidth(620)
        self.session, self.store = session, variant_store
        self.names = {}
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Save the CURRENT manually tuned A/B settings as NEW named candidates.\n"
            "The original optimiser results, existing POC variants and preferred selection are untouched."))
        for key, slot in session.slots.items():
            record = slot.recording
            if record is None:
                continue  # We cannot certify unviewed/unrecalculated source settings.
            checkbox = QCheckBox(f"Candidate {key} — {slot.choice.name}" +
                                 ("  •  Modified" if slot.modified else "  •  Original"))
            checkbox.setChecked(key == session.active)
            layout.addWidget(checkbox)
            fields = QFormLayout()
            name = QLineEdit(proposed_name(variant_store, session.park, key))
            fields.addRow("New variant label", name)
            metrics = tuning_metrics(record)
            description = (f"Maximum requested muscle excess {metrics.worst_requested_excess_mm:.2f} mm; "
                           f"{metrics.requested_oob_frames} out-of-range frames; "
                           f"{metrics.workspace_frames} workspace interventions; "
                           f"peak step {metrics.peak_step_mm:.1f} mm")
            detail = QLabel(description)
            detail.setWordWrap(True)
            fields.addRow("Current analysis", detail)
            container = QWidget()
            container.setLayout(fields)
            layout.addWidget(container)
            checkbox.toggled.connect(container.setEnabled)
            container.setEnabled(checkbox.isChecked())
            self.names[key] = (checkbox, name, record)
        note = QLabel("Saved variants have status 'candidate' only. Optimiser Strict/5% labels do not "
                      "carry over to the new variant. This is not physical or passenger-safety validation. "
                      "Existing global POC settings must match the study before saving.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self._check)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.entries = []
        self.entry_keys = []

    def _check(self):
        from .tuning import current_poc_global_settings
        entries = []
        keys = []
        for key, (checkbox, edit, recording) in self.names.items():
            if checkbox.isChecked():
                try:
                    if (recording.global_settings_snapshot is not None
                            and recording.global_settings_snapshot != current_poc_global_settings()):
                        raise ValueError("Archived global POC settings differ from installed live settings. "
                                         "Reanalyse with current globals before saving.")
                    entries.append(candidate_entry(edit.text().strip(), recording))
                    keys.append(key)
                except (KeyError, TypeError, ValueError) as exc:
                    QMessageBox.warning(self, f"Candidate {key} cannot be saved", str(exc))
                    return
        if not entries:
            QMessageBox.information(self, "Nothing selected", "Select at least one candidate to save.")
            return
        # The catalogue performs one atomic validation/write for all selected slots.
        if len({e["label"] for e in entries}) != len(entries):
            QMessageBox.warning(self, "Duplicate labels", "A and B require different labels.")
            return
        if any(e["label"] in {x["label"] for x in self.store.entries()} for e in entries):
            QMessageBox.warning(self, "Name already used", "Choose a new name; existing variants are never replaced.")
            return
        problem = []
        for key, (checkbox, _, recording) in self.names.items():
            if not checkbox.isChecked():
                continue
            result = tuning_metrics(recording)
            if result.worst_requested_excess_mm > 0 or result.workspace_frames or result.final_clipped_frames:
                problem.append(f"{key}: {result.worst_requested_excess_mm:.1f} mm requested excess; "
                               f"{result.workspace_frames} workspace interventions")
        if problem:
            message = ("These manually tuned configurations have kinematic anomalies:\n\n"
                       + "\n".join(problem) +
                       "\n\nSave as research candidates for review anyway? "
                       "This does not approve them for passenger operation.")
            if QMessageBox.question(self, "Kinematic warnings", message) != QMessageBox.StandardButton.Yes:
                return
        self.entries = entries
        self.entry_keys = keys
        self.accept()
