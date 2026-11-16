# dynamic_ride_workspace.py ver 4
"""Guided engineer workflow: study -> generate -> review -> prepare -> export.

All expensive profile generation runs in a managed QProcess; no simulator,
Festo or SHS connection is opened by this dialog. The normal Analyzer playback
window remains the review surface and can return to this workspace afterwards.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

from PyQt6.QtCore import QProcess, QUrl, Qt
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame,
    QGroupBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QMessageBox, QPlainTextEdit, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from .dynamic_review import ROOT
from .dynamic_ride_studies import (
    SUPPORTED_RIDES, find_studies, rated_recordings, recording_ride,
)
from .dynamic_workflow_state import (
    candidate_is_qualified, candidate_sort_key, recommended_candidate_paths,
    workflow_stage,
)
from tools.nl2_dynamic_profiles.package import prepare_bundle
from tools.nl2_sync.profile import DynamicProfile

PLATFORM_LABELS = {'chair_v3': 'Chair V3', 'platform_v2': 'Ground-Loading Platform'}
RIDE_LABELS = {'blackbird': 'Blackbird', 'mdxlogo': 'Mdx Logo Coaster'}
TARGET_LABELS = {'strict': 'Strict', 'relaxed5': 'Relaxed 5%'}


class WorkflowProgressStrip(QWidget):
    """Passive segmented progress indicator; deliberately not button-like."""

    _ORDER = ['source', 'generate', 'review', 'prepare', 'export']
    _TITLES = ('1 Source', '2 Generate', '3 Review', '4 Prepare', '5 Export')

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        self._segments = []
        for title in self._TITLES:
            frame = QFrame()
            frame.setObjectName('workflowSegment')
            frame.setMinimumHeight(40)
            frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            inner = QVBoxLayout(frame)
            inner.setContentsMargins(10, 6, 10, 6)
            inner.setSpacing(0)
            label = QLabel(title)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            inner.addStretch(1)
            inner.addWidget(label)
            inner.addStretch(1)
            row.addWidget(frame, 1)
            self._segments.append((frame, label, title))
        outer.addLayout(row)

        info = QHBoxLayout()
        info.setContentsMargins(0, 0, 0, 0)
        info.setSpacing(18)
        self.current_label = QLabel('Current step: Source')
        self.next_label = QLabel('Next action: Select or capture a source study')
        info.addWidget(self.current_label)
        info.addWidget(self.next_label, 1)
        outer.addLayout(info)

    def set_stage(self, stage, *, current_text='', next_text=''):
        current = {'generating': 'generate'}.get(stage, stage)
        try:
            current_index = self._ORDER.index(current)
        except ValueError:
            current_index = 0
        for index, (frame, label, title) in enumerate(self._segments):
            if index < current_index:
                style = (
                    'QFrame#workflowSegment {background:#1fa750; border:1px solid #249a50; '
                    'border-radius:3px;} QLabel {color:white; font-weight:700;}'
                )
                label.setText('✓ ' + title)
            elif index == current_index:
                style = (
                    'QFrame#workflowSegment {background:#1d88e5; border:1px solid #1976c9; '
                    'border-radius:3px;} QLabel {color:white; font-weight:700;}'
                )
                label.setText('● ' + title)
            else:
                style = (
                    'QFrame#workflowSegment {background:#102236; border:1px solid #5a6a78; '
                    'border-radius:3px;} QLabel {color:#b4c3d1; font-weight:500;}'
                )
                label.setText(title)
            frame.setStyleSheet(style)
        if current_text:
            self.current_label.setText('Current step: ' + current_text)
        if next_text:
            self.next_label.setText('Next action: ' + next_text)


class DynamicRideWorkspace(QDialog):
    """Persistent, state-driven workflow for preparing a dynamic ride."""

    def __init__(self, settings, parent=None, *, context=None):
        super().__init__(parent)
        self.setWindowTitle('Dynamic rides - preparation workspace')
        self.resize(1040, 780)
        self.settings = settings
        self.context = dict(context or {})
        self.action = None
        self.selected_path = None
        self.process = None
        self.output_directory = None
        self._stdout = ''
        self._generation_output = []
        self.studies = ()
        self._result_dir = None
        self._dismissed = set()
        self._reviewed = set()
        self._prepared = {}
        self._candidate_choices = {}
        self._recommended = {}
        self._prepared_package = None

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        self.setStyleSheet(
            'QComboBox {padding: 5px 30px 5px 8px; border:1px solid #768798; border-radius:4px;}'
            'QComboBox::drop-down {subcontrol-origin: padding; subcontrol-position: top right; width: 24px; border-left:1px solid #768798;}'
            'QPushButton {padding: 6px 14px; border:1px solid #6a7d90; border-radius:4px; min-height: 18px;}'
            'QPushButton#primaryActionButton {padding: 8px 18px; font-weight: 700;}'
        )

        intro = QLabel(
            'Prepare a dynamic ride as one guided workflow. Analyzer chooses the '
            'reference and held-out validation runs automatically; source playback '
            'and alternative candidates remain available for engineering review.'
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # Progress strip: passive segmented indicator, not a control row.
        self.progress_strip = WorkflowProgressStrip(self)
        layout.addWidget(self.progress_strip)

        source_group = QGroupBox('Source study')
        source_layout = QVBoxLayout(source_group)
        study_row = QHBoxLayout()
        self.study = QComboBox()
        self.study.setMinimumContentsLength(52)
        study_row.addWidget(self.study, 1)
        browse = QPushButton('Change / browse study...')
        browse.setMaximumWidth(190)
        browse.clicked.connect(self.browse_studies)
        study_row.addWidget(browse)
        capture_btn = QPushButton('Capture new study...')
        capture_btn.setMaximumWidth(170)
        capture_btn.clicked.connect(self.capture_study)
        study_row.addWidget(capture_btn)
        source_layout.addLayout(study_row)

        options = QHBoxLayout()
        options.addWidget(QLabel('Ride'))
        self.ride = QComboBox()
        for key, name in RIDE_LABELS.items():
            self.ride.addItem(name, key)
        options.addWidget(self.ride, 1)
        options.addWidget(QLabel('Generate for'))
        self.platform = QComboBox()
        self.platform.addItem('Both platforms', 'both')
        for key, name in PLATFORM_LABELS.items():
            self.platform.addItem(name, key)
        options.addWidget(self.platform, 1)
        source_layout.addLayout(options)

        self.study_note = QLabel()
        self.study_note.setWordWrap(True)
        source_layout.addWidget(self.study_note)

        source_toggle_row = QHBoxLayout()
        self.source_toggle = QPushButton('Source recordings (optional)')
        self.source_toggle.setCheckable(True)
        self.source_toggle.setChecked(False)
        self.source_toggle.toggled.connect(self._toggle_source_recordings)
        self.source_toggle.setMaximumWidth(320)
        source_toggle_row.addWidget(self.source_toggle)
        source_toggle_row.addStretch(1)
        source_layout.addLayout(source_toggle_row)

        self.recordings_panel = QWidget()
        recording_panel_layout = QVBoxLayout(self.recordings_panel)
        recording_panel_layout.setContentsMargins(0, 0, 0, 0)
        self.recordings = QListWidget()
        self.recordings.setMaximumHeight(135)
        recording_panel_layout.addWidget(self.recordings)
        recording_row = QHBoxLayout()
        recording_hint = QLabel(
            'Playback only. Ratings help inspection; they do not choose the optimiser reference.'
        )
        recording_hint.setWordWrap(True)
        recording_row.addWidget(recording_hint, 1)
        self.open_recording_btn = QPushButton('Play selected recording')
        self.open_recording_btn.setMaximumWidth(210)
        self.open_recording_btn.clicked.connect(self.open_recording)
        recording_row.addWidget(self.open_recording_btn)
        recording_panel_layout.addLayout(recording_row)
        self.recordings_panel.setVisible(False)
        source_layout.addWidget(self.recordings_panel)
        layout.addWidget(source_group)

        candidate_group = QGroupBox('Generated profiles')
        candidate_layout = QVBoxLayout(candidate_group)
        self.candidate_status = QLabel('No profiles generated for the current selection.')
        self.candidate_status.setWordWrap(True)
        candidate_layout.addWidget(self.candidate_status)

        candidate_row = QHBoxLayout()
        candidate_row.addWidget(QLabel('Candidate'))
        self.candidate = QComboBox()
        self.candidate.setMinimumContentsLength(50)
        candidate_row.addWidget(self.candidate, 1)
        self.show_dismissed = QCheckBox('Show dismissed')
        self.show_dismissed.toggled.connect(self.populate_candidates)
        candidate_row.addWidget(self.show_dismissed)
        candidate_layout.addLayout(candidate_row)

        self.candidate_detail = QLabel()
        self.candidate_detail.setWordWrap(True)
        candidate_layout.addWidget(self.candidate_detail)

        secondary = QHBoxLayout()
        self.report_btn = QPushButton('View generation report')
        self.report_btn.setMaximumWidth(190)
        self.report_btn.clicked.connect(self.open_report)
        secondary.addWidget(self.report_btn)
        self.review_again_btn = QPushButton('Review profile again')
        self.review_again_btn.setMaximumWidth(175)
        self.review_again_btn.clicked.connect(self.review_profile)
        secondary.addWidget(self.review_again_btn)
        self.regenerate_btn = QPushButton('Regenerate profiles...')
        self.regenerate_btn.setMaximumWidth(180)
        self.regenerate_btn.clicked.connect(self.regenerate_profiles)
        secondary.addWidget(self.regenerate_btn)
        self.dismiss_btn = QPushButton('Dismiss candidate')
        self.dismiss_btn.setMaximumWidth(170)
        self.dismiss_btn.setToolTip(
            'Hide this research candidate from the normal list; source evidence is preserved'
        )
        self.dismiss_btn.clicked.connect(self.dismiss_profile)
        secondary.addWidget(self.dismiss_btn)
        secondary.addStretch(1)
        candidate_layout.addLayout(secondary)
        layout.addWidget(candidate_group)

        next_group = QGroupBox('Next step')
        next_layout = QVBoxLayout(next_group)
        self.stage_title = QLabel()
        self.stage_title.setStyleSheet('font-size: 15px; font-weight: 600;')
        next_layout.addWidget(self.stage_title)
        self.stage_detail = QLabel()
        self.stage_detail.setWordWrap(True)
        next_layout.addWidget(self.stage_detail)
        primary_row = QHBoxLayout()
        self.primary_btn = QPushButton()
        self.primary_btn.setMinimumHeight(42)
        self.primary_btn.setMinimumWidth(240)
        self.primary_btn.setMaximumWidth(280)
        self.primary_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.primary_btn.setDefault(True)
        self.primary_btn.setObjectName('primaryActionButton')
        self.primary_btn.clicked.connect(self.advance_workflow)
        primary_row.addWidget(self.primary_btn)
        primary_row.addStretch(1)
        next_layout.addLayout(primary_row)
        layout.addWidget(next_group)

        self.package_group = QGroupBox('Prepared POC package')
        package_layout = QVBoxLayout(self.package_group)
        self.package_note = QLabel()
        self.package_note.setWordWrap(True)
        package_layout.addWidget(self.package_note)
        package_buttons = QHBoxLayout()
        self.open_export_btn = QPushButton('Open package folder')
        self.open_export_btn.setMaximumWidth(170)
        self.open_export_btn.clicked.connect(self.open_export_folder)
        package_buttons.addWidget(self.open_export_btn)
        self.copy_export_btn = QPushButton('Copy folder path')
        self.copy_export_btn.setMaximumWidth(150)
        self.copy_export_btn.clicked.connect(self.copy_export_path)
        package_buttons.addWidget(self.copy_export_btn)
        package_buttons.addStretch(1)
        package_layout.addLayout(package_buttons)
        self.package_group.setVisible(False)
        layout.addWidget(self.package_group)

        details_row = QHBoxLayout()
        self.details_toggle = QPushButton('Show generation details')
        self.details_toggle.setCheckable(True)
        self.details_toggle.setChecked(False)
        self.details_toggle.toggled.connect(self._toggle_generation_details)
        self.details_toggle.setMaximumWidth(230)
        details_row.addWidget(self.details_toggle)
        details_row.addStretch(1)
        layout.addLayout(details_row)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2500)
        self.log.setMaximumHeight(175)
        self.log.setPlaceholderText('Generation progress and diagnostic output')
        self.log.setVisible(False)
        layout.addWidget(self.log)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_btn = QPushButton('Close / return to Analyzer')
        close_btn.setMaximumWidth(210)
        close_btn.clicked.connect(self.reject)
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)

        self.study.currentIndexChanged.connect(self._source_selection_changed)
        self.ride.currentIndexChanged.connect(self._source_selection_changed)
        self.platform.currentIndexChanged.connect(self._source_selection_changed)
        self.candidate.currentIndexChanged.connect(self.update_workflow_state)

        self.populate_studies()
        chosen = self.context.get('ride') or settings.value('dynamic_ride_workflow_ride', 'blackbird')
        idx = self.ride.findData(chosen)
        if idx >= 0:
            self.ride.setCurrentIndex(idx)
        plat = self.context.get('platform') or settings.value('dynamic_ride_workflow_platform', 'both')
        idx = self.platform.findData(plat)
        if idx >= 0:
            self.platform.setCurrentIndex(idx)
        output = self.context.get('output') or settings.value('dynamic_ride_workflow_output', '')
        if output:
            self.set_result_dir(Path(str(output)))
        self.update_workflow_state()

    def append_log(self, line):
        if line:
            self.log.appendPlainText(str(line))

    def _toggle_source_recordings(self, visible):
        self.recordings_panel.setVisible(bool(visible))
        self.source_toggle.setText(
            'Hide source recordings' if visible else
            f'Source recordings (optional) - {self.recordings.count()} available'
        )

    def _toggle_generation_details(self, visible):
        self.log.setVisible(bool(visible))
        if visible and not self._generation_running():
            self._refresh_generation_details()
        self._update_details_toggle_text()

    def _set_details_visible(self, visible):
        self.details_toggle.setChecked(bool(visible))

    def _generation_running(self):
        return self.process is not None and self.process.state() != QProcess.ProcessState.NotRunning

    def _generation_log_path(self):
        return self._result_dir / 'analyzer_generation_output.txt' if self._result_dir else None

    def _update_details_toggle_text(self):
        if self._generation_running():
            noun = 'generation details'
        elif self._result_dir:
            noun = 'generation summary'
        else:
            noun = 'generation details'
        self.details_toggle.setText(('Hide ' if self.details_toggle.isChecked() else 'Show ') + noun)

    def _generation_summary_text(self):
        if not self._result_dir:
            return 'No completed generation result is currently selected.'
        lines = ['Generated profile study: ' + str(self._result_dir)]
        report = self._result_dir / 'report.html'
        if report.is_file():
            lines.append('Generation report: ' + str(report))
        if self._candidate_choices:
            lines.extend(['', 'Candidates for the current ride/platform selection:'])
            choices = sorted(self._candidate_choices.values(), key=candidate_sort_key)
            for choice in choices:
                path = Path(choice.path)
                rel = self._choice_relative(path)
                recommended = self._recommended.get(choice.platform) == str(path) and rel not in self._dismissed
                state = ['Qualified' if candidate_is_qualified(choice) else str(choice.qualification)]
                if recommended:
                    state.append('Recommended')
                if self._candidate_reviewed(path):
                    state.append('Reviewed')
                if self._candidate_prepared_path(path):
                    state.append('Prepared')
                if rel in self._dismissed:
                    state.append('Dismissed')
                lines.append(
                    '- ' + PLATFORM_LABELS.get(choice.platform, choice.platform) + ' / ' +
                    TARGET_LABELS.get(choice.target, choice.target) + ': ' + ', '.join(state)
                )
        log_path = self._generation_log_path()
        if log_path and log_path.is_file():
            try:
                detail = log_path.read_text(encoding='utf-8', errors='replace').strip()
            except OSError:
                detail = ''
            if detail:
                lines.extend(['', 'Optimiser output:', detail])
        else:
            lines.extend([
                '',
                'Detailed optimiser output was not retained for this older generated study.',
                'Use View generation report for the full qualification results.',
            ])
        return '\n'.join(lines)

    def _refresh_generation_details(self):
        if self._generation_running():
            return
        self.log.setPlainText(self._generation_summary_text())

    def populate_studies(self, directory=None):
        root = Path(directory or self.settings.value(
            'dynamic_ride_workflow_studies_root', str(ROOT / 'captures' / 'dynamic_source')
        ))
        self.studies = find_studies(root)
        desired = str(self.context.get('session') or self.settings.value('dynamic_ride_workflow_session', ''))
        self.study.blockSignals(True)
        self.study.clear()
        for row in self.studies:
            self.study.addItem(row.description, str(row.path))
        self.study.blockSignals(False)
        index = self.study.findData(desired)
        if index >= 0:
            self.study.setCurrentIndex(index)
        self.populate_recordings()

    def browse_studies(self):
        directory = QFileDialog.getExistingDirectory(
            self, 'Dynamic-source session or session parent folder',
            str(ROOT / 'captures' / 'dynamic_source')
        )
        if directory:
            path = Path(directory)
            self.settings.setValue('dynamic_ride_workflow_studies_root', str(path))
            self.populate_studies(path)

    def capture_study(self):
        from .dynamic_source_dialog import DynamicSourceCaptureDialog
        dialog = DynamicSourceCaptureDialog(str(self.ride.currentData()), self)
        dialog.exec()
        self.populate_studies()
        if dialog.open_recording_path is not None:
            from .dynamic_source_rating import _find_session_dir
            source = Path(dialog.open_recording_path)
            session = _find_session_dir(source)
            if session is not None:
                index = self.study.findData(str(session))
                if index >= 0:
                    self.study.setCurrentIndex(index)
            self.action = 'recording'
            self.selected_path = source
            self.context = self.save_context()
            self.accept()

    def current_study(self):
        index = self.study.currentIndex()
        return self.studies[index] if 0 <= index < len(self.studies) else None

    def _source_selection_changed(self):
        self.populate_recordings()
        self.populate_candidates()
        self.update_workflow_state()

    def populate_recordings(self):
        st = self.current_study()
        self.recordings.clear()
        if st:
            rows = rated_recordings(st, self.ride.currentData())
            for path, hint in rows:
                item = QListWidgetItem(f'{hint.rating:10s}  {path.name}')
                item.setData(Qt.ItemDataRole.UserRole, str(path))
                item.setToolTip(hint.tooltip())
                self.recordings.addItem(item)
            self.study_note.setText(
                f'{len(rows)} compatible recordings for {RIDE_LABELS.get(self.ride.currentData())}. '
                'Generation uses the complete study and selects reference, fit and held-out runs automatically.'
            )
            self.settings.setValue('dynamic_ride_workflow_session', str(st.path))
        else:
            self.study_note.setText(
                'No complete dynamic-source study found. Capture one or browse to an existing session.'
            )
        self.open_recording_btn.setEnabled(self.recordings.count() > 0)
        if self.recordings.count():
            self.recordings.setCurrentRow(0)
        self.source_toggle.setText(
            'Hide source recordings' if self.source_toggle.isChecked() else
            f'Source recordings (optional) - {self.recordings.count()} available'
        )

    def save_context(self):
        st = self.current_study()
        data = {
            'session': str(st.path) if st else '',
            'ride': str(self.ride.currentData()),
            'platform': str(self.platform.currentData()),
            'output': str(self._result_dir) if self._result_dir else '',
        }
        pending = self.context.get('pending_review_candidate')
        if pending:
            data['pending_review_candidate'] = str(pending)
        for key, value in data.items():
            self.settings.setValue('dynamic_ride_workflow_' + key, value)
        return data

    def open_recording(self):
        item = self.recordings.currentItem()
        if item is None:
            return
        self.action = 'recording'
        self.selected_path = Path(item.data(Qt.ItemDataRole.UserRole))
        self.context = self.save_context()
        self.accept()

    def generate_profiles(self):
        study = self.current_study()
        if not study:
            QMessageBox.warning(self, 'No study', 'Select a complete study first.')
            return
        if sum(recording_ride(p) == self.ride.currentData() for p in study.recordings) < 2:
            QMessageBox.warning(self, 'Insufficient recordings', 'At least two recordings are required.')
            return
        if self.process is not None and self.process.state() != QProcess.ProcessState.NotRunning:
            return
        self.save_context()
        self.output_directory = None
        self._stdout = ''
        self._generation_output = []
        self.log.clear()
        self._set_details_visible(True)
        self.append_log(
            'Generating with the existing offline dynamic optimiser; source recordings remain unchanged.'
        )
        process = QProcess(self)
        self.process = process
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.setWorkingDirectory(str(ROOT))
        process.readyReadStandardOutput.connect(self.on_progress)
        process.finished.connect(self.on_generation_finished)
        process.errorOccurred.connect(
            lambda _: self.append_log('Generation process error: ' + process.errorString())
        )
        process.start(
            sys.executable,
            [
                '-m', 'tools.sim_player.dynamic_optimizer', str(study.path),
                '--rides', str(self.ride.currentData()),
                '--platforms', str(self.platform.currentData()),
                '--output', str(ROOT / 'studies/nl2/dynamic_profiles'),
            ],
        )
        self.update_workflow_state()

    def regenerate_profiles(self):
        if self.process is not None and self.process.state() != QProcess.ProcessState.NotRunning:
            return
        if self._result_dir:
            answer = QMessageBox.question(
                self, 'Regenerate profiles',
                'Generate a new profile study for the current source/ride/platform selection?\n\n'
                'The existing generated study and reviews will be preserved.'
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.generate_profiles()

    def on_progress(self):
        if self.process is None:
            return
        chunk = bytes(self.process.readAllStandardOutput()).decode('utf-8', errors='replace')
        self._generation_output.append(chunk)
        self._stdout += chunk
        if len(self._stdout) > 5000:
            self._stdout = self._stdout[-5000:]
        self.append_log(chunk.rstrip())
        for line in self._stdout.splitlines():
            if line.startswith('Output: '):
                self.output_directory = Path(line[len('Output: '):].strip())

    def on_generation_finished(self, code, status):
        self.on_progress()
        if code == 0 and self.output_directory and (self.output_directory / 'report.json').is_file():
            generation_log = self.output_directory / 'analyzer_generation_output.txt'
            try:
                generation_log.write_text(
                    ''.join(self._generation_output).rstrip() + '\nProfile generation finished successfully.\n',
                    encoding='utf-8',
                )
            except OSError:
                pass
            self.set_result_dir(self.output_directory)
            self._set_details_visible(False)
        else:
            self.append_log(
                f'Generation ended with exit code {code}; do not prepare incomplete candidates.'
            )
            self._set_details_visible(True)
        self.update_workflow_state()

    def cancel_generation(self):
        if self.process is not None and self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()
            self.append_log('Generation cancelled; no incomplete profile may be prepared.')
            self._set_details_visible(True)

    def _workflow_state_path(self):
        return self._result_dir / 'analyzer_dynamic_workflow_state.json' if self._result_dir else None

    def _load_workflow_state(self):
        self._reviewed = set()
        self._prepared = {}
        path = self._workflow_state_path()
        if not path:
            return
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
            self._reviewed = set(str(v) for v in payload.get('reviewed', []))
            prepared = payload.get('prepared', {})
            if isinstance(prepared, dict):
                self._prepared = {str(k): str(v) for k, v in prepared.items() if Path(str(v)).is_file()}
        except (OSError, ValueError, TypeError):
            self._reviewed = set()
            self._prepared = {}

    def _save_workflow_state(self):
        path = self._workflow_state_path()
        if not path:
            return
        payload = {'reviewed': sorted(self._reviewed), 'prepared': dict(sorted(self._prepared.items()))}
        path.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')

    def _consume_pending_review(self):
        if not self._result_dir:
            return
        pending = self.context.get('pending_review_candidate') or self.settings.value(
            'dynamic_ride_workflow_pending_review_candidate', ''
        )
        if not pending:
            return
        try:
            path = Path(str(pending)).resolve()
            rel = path.relative_to(self._result_dir).as_posix()
        except (OSError, ValueError):
            return
        if path.name == 'profile.json' and path.is_file():
            self._reviewed.add(rel)
            self._save_workflow_state()
        self.context.pop('pending_review_candidate', None)
        self.settings.remove('dynamic_ride_workflow_pending_review_candidate')

    def set_result_dir(self, directory):
        directory = Path(directory).resolve()
        if not (directory / 'report.json').is_file():
            return
        self._result_dir = directory
        self.settings.setValue('dynamic_ride_workflow_output', str(directory))
        dismissed = directory / 'analyzer_dismissed_profiles.json'
        try:
            self._dismissed = set(json.loads(dismissed.read_text(encoding='utf-8')))
        except (OSError, ValueError, TypeError):
            self._dismissed = set()
        self._load_workflow_state()
        self._consume_pending_review()
        self.populate_candidates()
        self._refresh_generation_details()
        self._update_details_toggle_text()

    def _choice_relative(self, path):
        if not self._result_dir or path is None:
            return ''
        try:
            return Path(path).resolve().relative_to(self._result_dir).as_posix()
        except ValueError:
            return ''

    def populate_candidates(self):
        prior = self.candidate.currentData()
        self.candidate.blockSignals(True)
        self.candidate.clear()
        self._candidate_choices = {}
        choices = []
        if self._result_dir:
            from .dynamic_review import profile_choice
            ride = str(self.ride.currentData())
            platform_filter = str(self.platform.currentData())
            for path in self._result_dir.glob('*/*/*/profile.json'):
                try:
                    choice = profile_choice(path)
                except (OSError, ValueError, KeyError):
                    continue
                if choice.park != ride:
                    continue
                if platform_filter != 'both' and choice.platform != platform_filter:
                    continue
                rel = path.relative_to(self._result_dir).as_posix()
                dismissed = rel in self._dismissed
                if dismissed and not self.show_dismissed.isChecked():
                    continue
                choices.append(choice)
        choices.sort(key=candidate_sort_key)
        dismissed_paths = {str((self._result_dir / rel).resolve()) for rel in self._dismissed} if self._result_dir else set()
        self._recommended = recommended_candidate_paths(choices, dismissed_paths)
        for choice in choices:
            path = str(choice.path)
            rel = self._choice_relative(choice.path)
            dismissed = rel in self._dismissed
            recommended = self._recommended.get(choice.platform) == path and not dismissed
            target = TARGET_LABELS.get(choice.target, choice.target)
            qualification = 'Qualified' if candidate_is_qualified(choice) else choice.qualification
            suffix = ' - Recommended' if recommended else ''
            if dismissed:
                suffix += ' - Dismissed'
            label = f'{PLATFORM_LABELS.get(choice.platform, choice.platform)} - {target} - {qualification}{suffix}'
            self.candidate.addItem(label, path)
            self._candidate_choices[path] = choice

        idx = self.candidate.findData(prior)
        if idx < 0:
            recommended_paths = [
                self._recommended.get('chair_v3'), self._recommended.get('platform_v2')
            ]
            for preferred in recommended_paths:
                idx = self.candidate.findData(preferred)
                if idx >= 0:
                    break
        if idx < 0 and self.candidate.count():
            idx = 0
        if idx >= 0:
            self.candidate.setCurrentIndex(idx)
        self.candidate.blockSignals(False)
        self.update_workflow_state()
        if self._result_dir and not self._generation_running():
            self._refresh_generation_details()

    def current_candidate(self):
        data = self.candidate.currentData()
        return Path(data) if data else None

    def _current_choice(self):
        data = self.candidate.currentData()
        return self._candidate_choices.get(str(data)) if data else None

    def _candidate_reviewed(self, path):
        return self._choice_relative(path) in self._reviewed

    def _candidate_prepared_path(self, path):
        rel = self._choice_relative(path)
        package = self._prepared.get(rel)
        return Path(package) if package and Path(package).is_file() else None

    def _selected_stage(self):
        process_running = self.process is not None and self.process.state() != QProcess.ProcessState.NotRunning
        candidate = self.current_candidate()
        return workflow_stage(
            has_study=self.current_study() is not None,
            generating=process_running,
            has_result=bool(self._result_dir and (self._result_dir / 'report.json').is_file()),
            has_candidate=candidate is not None,
            reviewed=bool(candidate and self._candidate_reviewed(candidate)),
            prepared=bool(candidate and self._candidate_prepared_path(candidate)),
        )

    def _set_progress(self, stage, current_text='', next_text=''):
        self.progress_strip.set_stage(stage, current_text=current_text, next_text=next_text)

    def update_workflow_state(self):
        stage = self._selected_stage()
        progress_current = 'Source'
        progress_next = 'Select or capture a source study'
        choice = self._current_choice()
        candidate = self.current_candidate()
        has_result = bool(self._result_dir and (self._result_dir / 'report.json').is_file())
        process_running = self.process is not None and self.process.state() != QProcess.ProcessState.NotRunning

        self.report_btn.setEnabled(has_result and (self._result_dir / 'report.html').is_file())
        self.report_btn.setVisible(has_result)
        self.regenerate_btn.setEnabled(self.current_study() is not None and not process_running)
        self.regenerate_btn.setVisible(has_result)
        self.dismiss_btn.setEnabled(candidate is not None and not process_running)
        self.dismiss_btn.setVisible(candidate is not None)
        self.review_again_btn.setEnabled(candidate is not None and not process_running)
        self.review_again_btn.setVisible(candidate is not None and stage in ('prepare', 'export'))
        self.show_dismissed.setEnabled(has_result and not process_running)
        self.show_dismissed.setVisible(has_result)
        self.candidate.setEnabled(self.candidate.count() > 0 and not process_running)
        self.study.setEnabled(not process_running)
        self.ride.setEnabled(not process_running)
        self.platform.setEnabled(not process_running)
        self.open_recording_btn.setEnabled(self.recordings.count() > 0 and not process_running)

        is_recommended = False
        if choice:
            rel = self._choice_relative(choice.path)
            dismissed = rel in self._dismissed
            recommended = self._recommended.get(choice.platform) == str(choice.path) and not dismissed
            is_recommended = recommended
            validation = DynamicProfile.load(choice.path).metadata.get('validation', {})
            details = [
                f'{PLATFORM_LABELS.get(choice.platform, choice.platform)} / '
                f'{TARGET_LABELS.get(choice.target, choice.target)}',
                'Qualified' if candidate_is_qualified(choice) else f'Qualification: {choice.qualification}',
            ]
            if recommended:
                details.append('Recommended for this platform')
            if validation.get('heldout_pass') is True:
                details.append('held-out pass')
            if validation.get('production_parity') is True:
                details.append('production parity pass')
            if choice.target == 'strict':
                details.append('strict muscle request envelope')
            elif choice.target == 'relaxed5':
                details.append('up to 5% requested-length allowance')
            self.candidate_detail.setText('  |  '.join(details))
            self.dismiss_btn.setText('Restore candidate' if dismissed else 'Dismiss candidate')
        else:
            self.candidate_detail.setText(
                'Generate profiles for the selected study, ride and platform selection to continue.'
            )
            self.dismiss_btn.setText('Dismiss candidate')

        prepared_path = self._candidate_prepared_path(candidate) if candidate else None
        current_rel = self._choice_relative(candidate) if candidate else ''
        current_dismissed = bool(current_rel and current_rel in self._dismissed)
        self.package_group.setVisible(bool(prepared_path))
        if prepared_path:
            self._prepared_package = prepared_path
            self.package_note.setText(
                f'{prepared_path.name}\nFolder: {prepared_path.parent}\n'
                'Transfer this ZIP to the target POC and import it from the Profile tab.'
            )

        if current_dismissed:
            self.stage_title.setText('Candidate is dismissed')
            self.stage_detail.setText(
                'This research candidate is hidden from the normal workflow. Restore it before reviewing or preparing it for POC.'
            )
            self.primary_btn.setText('Restore candidate')
            self.primary_btn.setEnabled(True)
            progress_current = 'Prepare' if has_result else 'Review'
            progress_next = 'Restore the selected candidate'
        elif stage == 'source':
            self.stage_title.setText('Select or capture a complete dynamic-source study')
            self.stage_detail.setText(
                'Choose the study and ride. Individual recording playback is optional; generation uses the complete study.'
            )
            self.primary_btn.setText('Capture new study...')
            self.primary_btn.setEnabled(True)
            progress_current = 'Source'
            progress_next = 'Select a study or capture a new one'
        elif stage == 'generating':
            self.stage_title.setText('Profile generation is running')
            self.stage_detail.setText(
                'Generation details are shown below. Source recordings are unchanged.'
            )
            self.primary_btn.setText('Cancel generation')
            self.primary_btn.setEnabled(True)
            progress_current = 'Generate'
            progress_next = 'Wait for generation to finish or cancel it'
        elif stage == 'generate':
            self.stage_title.setText('Ready to generate dynamic profiles')
            self.stage_detail.setText(
                f'Generate {RIDE_LABELS.get(self.ride.currentData(), self.ride.currentData())} profiles for '
                f'{self.platform.currentText()}. Analyzer will choose the reference and held-out runs automatically.'
            )
            self.primary_btn.setText('Generate dynamic profiles')
            self.primary_btn.setEnabled(self.current_study() is not None)
            progress_current = 'Generate'
            progress_next = 'Generate profiles for the selected study'
        elif stage == 'review':
            label = self.candidate.currentText()
            self.stage_title.setText(
                'Review the recommended generated profile' if is_recommended else 'Review the selected generated profile'
            )
            self.stage_detail.setText(
                f'Selected: {label}. Play the complete profile in Analyzer, inspect motion/synchronisation, '
                'then use Return to dynamic study to continue.'
            )
            self.primary_btn.setText('Review selected profile')
            self.primary_btn.setEnabled(candidate is not None)
            progress_current = 'Review'
            progress_next = 'Review the selected profile in Analyzer'
        elif stage == 'prepare':
            self.stage_title.setText('Profile reviewed - ready to prepare for POC')
            self.stage_detail.setText(
                'Analyzer will validate the recorded qualification evidence and build a POC import ZIP. '
                'Preparing a package does not make the ride preferred or physically commissioned.'
            )
            self.primary_btn.setText('Prepare for POC...')
            self.primary_btn.setEnabled(candidate is not None)
            progress_current = 'Prepare'
            progress_next = 'Prepare the selected candidate for POC'
        else:
            self.stage_title.setText('POC package ready')
            self.stage_detail.setText(
                'The selected candidate has a prepared import ZIP. Transfer it to the target POC, then use '
                'Profile > Import dynamic candidate ZIP...'
            )
            self.primary_btn.setText('Open package folder')
            self.primary_btn.setEnabled(bool(prepared_path))
            progress_current = 'Export'
            progress_next = 'Transfer the prepared ZIP to POC'

        self._set_progress(stage, current_text=progress_current, next_text=progress_next)
        self._update_details_toggle_text()

        if not has_result:
            self.candidate_status.setText('No generated profiles for the current ride/platform selection.')
        elif self.candidate.count() == 0:
            self.candidate_status.setText(
                'Generation results exist, but no candidate matches the current ride/platform selection.'
            )
        else:
            recommended_count = len(self._recommended)
            self.candidate_status.setText(
                f'{self.candidate.count()} candidate(s) available. '
                f'{recommended_count} platform recommendation(s). Strict is preferred when it qualifies; '
                'Relaxed 5% is suggested only when no qualifying Strict candidate is available.'
            )

    def advance_workflow(self):
        candidate = self.current_candidate()
        rel = self._choice_relative(candidate) if candidate else ''
        if rel and rel in self._dismissed:
            self.dismiss_profile()
            return
        stage = self._selected_stage()
        if stage == 'source':
            self.capture_study()
        elif stage == 'generating':
            self.cancel_generation()
        elif stage == 'generate':
            self.generate_profiles()
        elif stage == 'review':
            self.review_profile()
        elif stage == 'prepare':
            self.prepare_profile()
        elif stage == 'export':
            self.open_export_folder()

    def open_report(self):
        if self._result_dir:
            path = self._result_dir / 'report.html'
            if path.is_file():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def review_profile(self):
        path = self.current_candidate()
        if path is None:
            return
        self.action = 'profile'
        self.selected_path = path
        self.context = self.save_context()
        self.context['pending_review_candidate'] = str(path)
        self.settings.setValue('dynamic_ride_workflow_pending_review_candidate', str(path))
        self.accept()

    def dismiss_profile(self):
        path = self.current_candidate()
        if not path or not self._result_dir:
            return
        rel = path.relative_to(self._result_dir).as_posix()
        if rel in self._dismissed:
            self._dismissed.remove(rel)
        else:
            self._dismissed.add(rel)
        target = self._result_dir / 'analyzer_dismissed_profiles.json'
        target.write_text(json.dumps(sorted(self._dismissed), indent=2) + '\n', encoding='utf-8')
        self.populate_candidates()

    def prepare_profile(self):
        path = self.current_candidate()
        if path is None:
            return
        from sims.nolimits2.ride_config import PARKS
        profile = DynamicProfile.load(path)
        park = profile.identity['park']
        default = PARKS[park]['name'] + ' [Dynamic A]'
        from PyQt6.QtWidgets import QInputDialog
        label, ok = QInputDialog.getText(
            self, 'Prepare candidate for POC',
            'Unique POC variant name (candidate, not preferred):', text=default
        )
        if not ok:
            return
        try:
            package, rel, summary = prepare_bundle(
                path.parent, label, export_dir=ROOT / 'exports' / 'dynamic_candidates'
            )
        except Exception as exc:
            QMessageBox.warning(self, 'Cannot prepare candidate', str(exc))
            return
        candidate_rel = self._choice_relative(path)
        self._prepared[candidate_rel] = str(package)
        self._save_workflow_state()
        self._prepared_package = package
        QApplication.clipboard().setText(str(package.parent))
        self.append_log('Prepared: ' + str(package) + '\nFolder copied to clipboard: ' + str(package.parent))
        self.update_workflow_state()
        QMessageBox.information(
            self, 'Candidate prepared for POC',
            f'Candidate: {label}\nPlatform: {summary["platform"]}\n'
            f'Recordings: {summary["source_recordings"]} (including held-out validation)\n'
            f'Package: {package}\n\nFolder path copied to clipboard.\n'
            'Transfer this ZIP to POC, then use Profile > Import dynamic candidate ZIP. '
            'No working catalogue was overwritten on the Analyzer PC.'
        )

    def open_export_folder(self):
        path = self._candidate_prepared_path(self.current_candidate())
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))

    def copy_export_path(self):
        path = self._candidate_prepared_path(self.current_candidate())
        if path:
            QApplication.clipboard().setText(str(path.parent))
            self.stage_detail.setText('Package folder path copied to the clipboard: ' + str(path.parent))

    def reject(self):
        if self.process is not None and self.process.state() != QProcess.ProcessState.NotRunning:
            QMessageBox.warning(
                self, 'Generation active', 'Cancel or complete generation before closing the workspace.'
            )
            return
        self.context = self.save_context()
        super().reject()
