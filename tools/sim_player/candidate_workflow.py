# candidate_workflow.py ver 2
"""UI controller for candidate discovery, independent A/B tuning, explicit save.

ReplayWindow owns the normal reanalysis, video and timeline. This mixin only
manages candidate working-copy state. The optimised files are always immutable.
"""
from __future__ import annotations

from pathlib import Path
import json
from PyQt6.QtWidgets import QDialog, QFileDialog, QMessageBox

from .candidate_browser import CandidateBrowserDialog
from .candidate_review import reopen_session
from .review_save_dialog import SaveReviewVariantsDialog
from .tuning import validate_gain_percent


class CandidateReviewMixin:
    def _init_review_state(self):
        self._candidate_session = None
        self._candidate_switch_previous = None
        self._review_resume_target = None

    def _review_busy(self) -> bool:
        return bool(self.loading or (self.worker is not None and self.worker.isRunning()))

    def _confirm_replace_review(self) -> bool:
        session = self._candidate_session
        if session is None:
            return True
        if any(slot.modified and not slot.saved_label for slot in session.slots.values()):
            result = QMessageBox.question(self, "Discard unsaved candidate edits?",
                "The current A/B review contains unsaved manual changes. "
                "Open a new capture/review and discard those working copies?\n\n"
                "You can first use Save review… to preserve both configurations.")
            return result == QMessageBox.StandardButton.Yes
        return True

    def pick_review_candidates(self):
        if self._review_busy() or not self._confirm_replace_review():
            return
        folder = Path(str(self.settings.value(
            "optimizer_results_directory", str(Path(__file__).resolve().parents[2] / "studies" / "optimizer"))))
        dialog = CandidateBrowserDialog(folder, self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.session is not None:
            self.settings.setValue("optimizer_results_directory", str(dialog.directory))
            self._open_review_session(dialog.session)

    def _open_review_session(self, session):
        if self._review_busy():
            return
        self._review_resume_target = session.active if session.active != "A" else None
        session.active = "A"
        self._candidate_switch_previous = None
        self.open_capture(session.slots["A"].csv_path, review_session=session, skip_discard=True)

    def _update_candidate_ui(self):
        if not hasattr(self, "candidate_box"):
            return
        session = self._candidate_session
        if hasattr(self, "open_dynamic_button"):
            # Keep the original static A/B toolbar usable on a 1280px display.
            # Dynamic review remains accessible from File in this workspace.
            self.open_dynamic_button.setVisible(session is None)
        busy = self._review_busy()
        widgets = (self.candidate_box, self.candidate_status_label,
                   self.reset_candidate_button, self.save_review_button,
                   self.save_review_session_button)
        for widget in widgets:
            widget.setVisible(session is not None)
        if hasattr(self, "save_review_session_action"):
            self.save_review_session_action.setEnabled(session is not None and not busy)
        if session is None:
            return
        self.candidate_box.blockSignals(True)
        self.candidate_box.clear()
        for key, slot in session.slots.items():
            description = slot.choice.name
            self.candidate_box.addItem(f"{key} — {description}", key)
            if slot.modified:
                idx = self.candidate_box.count() - 1
                self.candidate_box.setItemText(idx, f"{key} — {description} *")
        self.candidate_box.setCurrentIndex(self.candidate_box.findData(session.active))
        self.candidate_box.blockSignals(False)
        active = session.slots[session.active]
        status = "Modified" if active.modified else "Original optimiser settings"
        if active.saved_label:
            status += " · saved: " + active.saved_label
        self.candidate_status_label.setText(status)
        self.candidate_status_label.setToolTip(
            status + "\nWorking copies are independent. Strict/5% describe the source only. "
            "A manually tuned variant may no longer meet that classification.")
        self.candidate_box.setEnabled(not busy and len(session.slots) > 1)
        self.reset_candidate_button.setEnabled(not busy and active.modified)
        self.save_review_button.setEnabled(not busy and any(s.recording is not None for s in session.slots.values()))
        self.save_review_session_button.setEnabled(not busy)

    def _candidate_slot_changed(self, *_args):
        key = self.candidate_box.currentData()
        if key:
            self._activate_candidate(key)

    def _activate_candidate(self, key):
        session = self._candidate_session
        if not session or key not in session.slots:
            return
        if key == session.active or self._review_busy():
            self._update_candidate_ui()
            return
        old = session.active
        if self.recording is not None:
            session.slots[old].recording = self.recording
        slot = session.slots[key]
        self._candidate_switch_previous = old
        session.active = key
        self._baseline_recording = slot.original_recording
        self._refresh_baseline_label()
        self._set_playing(False)
        self._update_candidate_ui()
        if slot.recording is not None:
            self._apply_reanalysis(slot.recording)
        else:
            # First visit: compute the selected optimiser candidate through
            # the existing player pipeline (never from opt_* derived columns).
            self._apply_experiment_values(slot.mode, slot.gains,
                variant_label=slot.snapshot["label"], variant_snapshot=slot.snapshot,
                global_settings=slot.globals)
            if not self._review_busy() and self.recording is not None:
                # The A and B source settings could be numerically identical;
                # _request_reanalysis then returns without a worker.
                self._apply_reanalysis(self.recording)

    def _review_recording_applied(self, recording):
        session = self._candidate_session
        if session is None:
            return
        slot = session.slots[session.active]
        slot.incorporate(recording)
        self._candidate_switch_previous = None
        self._baseline_recording = slot.original_recording
        self._refresh_baseline_label()
        self._update_tuning_feedback()
        self._update_candidate_ui()

    def _review_analysis_failed(self):
        if self._candidate_session and self._candidate_switch_previous:
            self._candidate_session.active = self._candidate_switch_previous
            self._candidate_switch_previous = None
            self._baseline_recording = self._candidate_session.slots[
                self._candidate_session.active].original_recording
            self._refresh_baseline_label()
        self._update_candidate_ui()

    def _after_review_worker_finished(self):
        session = self._candidate_session
        if not session or self._review_busy() or self.recording is None:
            self._update_candidate_ui()
            return
        slot = session.slots[session.active]
        if slot.pending_override is not None:
            desired = slot.pending_override
            slot.pending_override = None
            if (desired[0] != slot.mode or desired[1] != slot.gains
                    or desired[2] != slot.snapshot or desired[3] != slot.globals):
                self._apply_experiment_values(desired[0], desired[1],
                    variant_label=desired[2]["label"], variant_snapshot=desired[2],
                    global_settings=desired[3])
                if self._review_busy():
                    return
        if self._review_resume_target and self._review_resume_target != session.active:
            next_slot = self._review_resume_target
            self._review_resume_target = None
            self._activate_candidate(next_slot)
        self._update_candidate_ui()

    def reset_review_candidate(self):
        session = self._candidate_session
        if not session or self._review_busy():
            return
        slot = session.slots[session.active]
        slot.restore_original()
        # Reset replays the original candidate; original_recording is a fast
        # exact snapshot, if this slot has already been calculated.
        if slot.recording is not None:
            self._apply_reanalysis(slot.recording)
        else:
            self._apply_experiment_values(slot.mode, slot.gains,
                variant_label=slot.snapshot["label"], variant_snapshot=slot.snapshot,
                global_settings=slot.globals)
        self._update_candidate_ui()

    def save_review_variants(self):
        session = self._candidate_session
        if session is None:
            self.save_variant_entry()
            return
        if self._review_busy():
            return
        dialog = SaveReviewVariantsDialog(session, self.variant_store, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            labels = self.variant_store.add_candidates(dialog.entries)
        except (OSError, KeyError, TypeError, ValueError) as exc:
            QMessageBox.warning(self, "Candidate variants not saved", str(exc))
            return
        for key, label in zip(dialog.entry_keys, labels):
            session.slots[key].saved_label = label
        self._fill_variant_choices(self.recording)
        self._update_candidate_ui()
        self.statusBar().showMessage(
            f"Saved {len(labels)} new candidate(s) to {self.variant_store.path}. "
            "POC loads variants on restart; preferred rides may hide trials from operator selection.", 15000)

    def save_review_session(self):
        session = self._candidate_session
        if not session or self._review_busy():
            return
        suggested = str(session.run_root / f"{session.park}_{session.platform}_review.json")
        path, _ = QFileDialog.getSaveFileName(self, "Save A/B research review", suggested,
                                               "Candidate review (*.json)")
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        try:
            final = Path(path)
            data = session.to_dict()
            payload = json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
            tmp = final.with_suffix(final.suffix + ".tmp")
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(final)
        except (OSError, ValueError, TypeError) as exc:
            QMessageBox.warning(self, "Review not saved", str(exc))
            return
        self.statusBar().showMessage(f"A/B review saved to {path}", 8000)

    def open_review_session(self):
        if self._review_busy() or not self._confirm_replace_review():
            return
        suggested = str(self.settings.value("optimizer_results_directory", ""))
        path, _ = QFileDialog.getOpenFileName(self, "Open saved A/B review", suggested,
                                              "Candidate review (*.json)")
        if not path:
            return
        try:
            session = reopen_session(Path(path))
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            QMessageBox.warning(self, "Review could not be opened", str(exc))
            return
        changed = sorted({name for slot in session.slots.values()
                          for name in slot.source_bundle.get("_model_changed", [])})
        if changed:
            if QMessageBox.question(self, "Optimiser model changed",
                "The player processing code differs from this archived optimiser run. "
                "Old strict/relaxed qualifications are no longer valid. Continue?\n\n" +
                "\n".join(changed[:12])) != QMessageBox.StandardButton.Yes:
                return
        self._open_review_session(session)
