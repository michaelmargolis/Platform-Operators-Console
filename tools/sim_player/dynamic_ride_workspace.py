# dynamic_ride_workspace.py ver 14
"""Three-stage dynamic-ride workflow: Source -> Generate -> Review & Export.

All expensive profile generation runs in a managed QProcess; no simulator,
Festo or SHS connection is opened by this dialog. The normal Analyzer playback
window remains the review surface and can return to this workspace afterwards.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
import sys

from PyQt6.QtCore import QFileSystemWatcher, QProcess, QTimer, QUrl, Qt
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFrame,
    QGroupBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QMessageBox, QPlainTextEdit, QPushButton, QSizePolicy, QStackedWidget,
    QHeaderView, QLineEdit, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .dynamic_review import ROOT
from .dynamic_export_inventory import discover_exported_candidates, short_candidate_label
from .dynamic_ride_studies import (
    available_rides, find_sessions, platform_processing_display, rated_recordings,
    ride_label, ride_processing_display, session_processing_details,
    session_status_display,
)
from .dynamic_source import DEFAULT_OUTPUT_ROOT as DEFAULT_SOURCE_ROOT
from .dynamic_workflow_state import (
    candidate_is_qualified, candidate_sort_key, recommended_candidate_paths,
    workflow_stage,
)
from tools.nl2_dynamic_profiles.package import prepare_bundle
from tools.nl2_sync.profile import DynamicProfile

PLATFORM_LABELS = {'chair_v3': 'Chair V3', 'platform_v2': 'Ground-Loading Platform'}
TARGET_LABELS = {'strict': 'Strict', 'relaxed5': 'Relaxed 5%'}


class WorkflowProgressStrip(QWidget):
    """Passive three-stage wizard header; deliberately not button-like."""

    _ORDER = ['source', 'generate', 'review']
    _TITLES = ('1 Source', '2 Generate', '3 Review & Export')

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(3)
        self._segments = []
        for title in self._TITLES:
            frame = QFrame()
            frame.setObjectName('workflowSegment')
            frame.setMinimumHeight(40)
            frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            inner = QVBoxLayout(frame)
            inner.setContentsMargins(10, 6, 10, 6)
            label = QLabel(title)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            inner.addWidget(label)
            row.addWidget(frame, 1)
            self._segments.append((frame, label, title))
        outer.addLayout(row)
        # Kept as non-visible compatibility fields for older code/tests that
        # call set_stage(current_text=..., next_text=...). The wizard pages now
        # make the next action self-evident and do not repeat it below the strip.
        self.current_label = QLabel()
        self.next_label = QLabel()

    def set_stage(self, stage, *, current_text='', next_text=''):
        current = {
            'generating': 'generate', 'prepare': 'review', 'export': 'review'
        }.get(stage, stage)
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
        self.current_label.setText(current_text)
        self.next_label.setText(next_text)


class DynamicRideWorkspace(QDialog):
    """Three-stage wizard for generating, reviewing and exporting dynamic rides."""

    def __init__(self, settings, parent=None, *, context=None):
        super().__init__(parent)
        self.setWindowTitle('Dynamic rides - prepare for POC')
        self.resize(1360, 790)
        self.settings = settings
        self.context = dict(context or {})
        self.action = None
        self.selected_path = None
        self.process = None
        self.output_directory = None
        self._stdout = ''
        self._generation_output = []
        self._generation_failed = False
        self._force_source_page = False
        self.sessions = ()
        self._source_root = DEFAULT_SOURCE_ROOT.resolve()
        self._result_dir = None
        self._dismissed = set()
        self._reviewed = set()
        self._prepared = {}
        self._candidate_choices = {}
        self._all_candidate_choices = {}
        self._recommended = {}
        self._prepared_package = None
        self._export_name_overrides = {}
        self._session_processing = {}
        self._preferred_ride = str(
            self.context.get('ride') or settings.value('dynamic_ride_workflow_ride', '') or ''
        )

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        self.setStyleSheet(
            'QComboBox, QLineEdit {'
            '  background:#182838; color:#e8eef4; padding:5px 30px 5px 8px;'
            '  border:1px solid #687d90; border-radius:4px;'
            '}'
            'QLineEdit {padding-right:8px;}'
            'QComboBox:hover, QLineEdit:hover {border-color:#8ea7bc;}'
            'QComboBox::drop-down {'
            '  subcontrol-origin:padding; subcontrol-position:top right; width:26px;'
            '  background:#132231; border-left:1px solid #687d90;'
            '}'
            'QPushButton {'
            '  background:#263746; color:#dbe6ee; padding:6px 14px; font-weight:500;'
            '  border:1px solid #5d7487; border-radius:4px; min-height:18px;'
            '}'
            'QPushButton:hover {background:#31495c; border-color:#7892a6;}'
            'QPushButton:pressed {background:#20313f; border-color:#526a7d;}'
            'QPushButton:checked {background:#36556b; border-color:#7892a6;}'
            'QPushButton:disabled {'
            '  background:#1a2733; color:#72808c; border-color:#354553;'
            '}'
            'QPushButton#primaryActionButton {'
            '  background:#147fc8; border:2px solid #62b8ec;'
            '  padding:8px 18px; font-weight:700;'
            '}'
            'QPushButton#primaryActionButton:hover {background:#1d91dc; border-color:#8bd0f7;}'
            'QPushButton#primaryActionButton:pressed {background:#0f69a8;}'
            'QPushButton#primaryActionButton:disabled {'
            '  background:#1b3345; color:#71899a; border-color:#38556a;'
            '}'
            'QFrame#decisionFocusFrame {'
            '  background:#102234; border:1px solid #2d8fd0; border-radius:6px;'
            '}'
            'QFrame#decisionFocusFrame QLabel {background:transparent;}'
        )

        intro = QLabel(
            'Create a POC-ready dynamic ride in three steps. Analyzer selects the '
            'reference and held-out validation runs automatically; engineering '
            'alternatives remain available without cluttering the normal path.'
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.progress_strip = WorkflowProgressStrip(self)
        layout.addWidget(self.progress_strip)
        self.content_row = QHBoxLayout()
        self.content_row.setSpacing(10)
        self.pages = QStackedWidget(self)
        self.content_row.addWidget(self.pages, 1)
        layout.addLayout(self.content_row, 1)

        # ------------------------------------------------------------------
        # 1. SOURCE
        # ------------------------------------------------------------------
        source_page = QWidget()
        source_page_layout = QVBoxLayout(source_page)
        source_group = QGroupBox('Source')
        source_layout = QVBoxLayout(source_group)

        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel('Folder'))
        self.source_folder_label = QLabel(str(self._source_root))
        self.source_folder_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.source_folder_label.setStyleSheet(
            'background:#132231; color:#d8e4ed; border:1px solid #526779; '
            'border-radius:4px; padding:6px 8px;'
        )
        folder_row.addWidget(self.source_folder_label, 1)
        source_layout.addLayout(folder_row)

        decision_frame = QFrame()
        decision_frame.setObjectName('decisionFocusFrame')
        decision_layout = QVBoxLayout(decision_frame)
        decision_layout.setContentsMargins(12, 10, 12, 10)
        decision_layout.setSpacing(10)

        session_row = QHBoxLayout()
        session_row.addWidget(QLabel('Session'))
        self.session = QComboBox()
        self.session.setMinimumContentsLength(52)
        self.session.setToolTip(
            'Sessions found directly in the folder above. Hover a session to see rides and processing state.')
        self.session.view().setMouseTracking(True)
        session_row.addWidget(self.session, 1)
        capture_btn = QPushButton('Capture new session...')
        capture_btn.setMaximumWidth(180)
        capture_btn.clicked.connect(self.capture_session)
        session_row.addWidget(capture_btn)
        decision_layout.addLayout(session_row)

        ride_row = QHBoxLayout()
        ride_row.addWidget(QLabel('Ride'))
        self.ride = QComboBox()
        self.ride.setToolTip('Only rides with primary recordings in the selected session are listed.')
        ride_row.addWidget(self.ride, 1)
        decision_layout.addLayout(ride_row)

        platform_group = QGroupBox('Generate for platform(s)')
        platform_layout = QVBoxLayout(platform_group)
        self.platform_chair = QCheckBox('Chair V3')
        self.platform_ground = QCheckBox('Ground-Loading Platform')
        self.platform_status = {
            'chair_v3': QLabel('Not generated'),
            'platform_v2': QLabel('Not generated'),
        }
        for checkbox, key in ((self.platform_chair, 'chair_v3'),
                              (self.platform_ground, 'platform_v2')):
            row = QHBoxLayout()
            row.addWidget(checkbox)
            row.addStretch(1)
            status = self.platform_status[key]
            status.setStyleSheet('color:#aebdca;')
            row.addWidget(status)
            platform_layout.addLayout(row)
        decision_layout.addWidget(platform_group)
        source_layout.addWidget(decision_frame)

        self.session_note = QLabel()
        self.session_note.setWordWrap(True)
        source_layout.addWidget(self.session_note)

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
        self.recordings.setMaximumHeight(150)
        recording_panel_layout.addWidget(self.recordings)
        recording_row = QHBoxLayout()
        recording_hint = QLabel(
            'Playback only. Ratings help inspection; they do not choose the optimiser reference.')
        recording_hint.setWordWrap(True)
        recording_row.addWidget(recording_hint, 1)
        self.open_recording_btn = QPushButton('Play selected recording')
        self.open_recording_btn.setMaximumWidth(210)
        self.open_recording_btn.clicked.connect(self.open_recording)
        recording_row.addWidget(self.open_recording_btn)
        recording_panel_layout.addLayout(recording_row)
        self.recordings_panel.setVisible(False)
        source_layout.addWidget(self.recordings_panel)
        source_page_layout.addWidget(source_group)
        source_page_layout.addStretch(1)

        source_action = QHBoxLayout()
        self.source_generate_new_btn = QPushButton('Generate new profiles →')
        self.source_generate_new_btn.setMinimumHeight(36)
        self.source_generate_new_btn.clicked.connect(self.regenerate_profiles)
        self.source_generate_new_btn.setVisible(False)
        source_action.addStretch(1)
        source_action.addWidget(self.source_generate_new_btn)
        source_action.addSpacing(12)
        self.source_next_btn = QPushButton('Generate profiles →')
        self.source_next_btn.setObjectName('primaryActionButton')
        self.source_next_btn.setMinimumHeight(42)
        self.source_next_btn.setMinimumWidth(250)
        self.source_next_btn.clicked.connect(self._source_next)
        source_action.addWidget(self.source_next_btn)
        source_action.addStretch(1)
        source_page_layout.addLayout(source_action)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        self.source_close_btn = QPushButton('Close / return to Analyzer')
        self.source_close_btn.setMinimumHeight(36)
        self.source_close_btn.setMinimumWidth(280)
        self.source_close_btn.clicked.connect(self.reject)
        close_row.addWidget(self.source_close_btn)
        close_row.addStretch(1)
        source_page_layout.addSpacing(8)
        source_page_layout.addLayout(close_row)
        self.pages.addWidget(source_page)

        # ------------------------------------------------------------------
        # 2. GENERATE
        # ------------------------------------------------------------------
        generation_page = QWidget()
        generation_layout = QVBoxLayout(generation_page)
        self.generation_title = QLabel('Generate dynamic profiles')
        self.generation_title.setStyleSheet('font-size:16px; font-weight:700;')
        generation_layout.addWidget(self.generation_title)
        self.generation_context = QLabel()
        self.generation_context.setWordWrap(True)
        generation_layout.addWidget(self.generation_context)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2500)
        self.log.setPlaceholderText('Generation progress and optimiser output')
        generation_layout.addWidget(self.log, 1)
        generation_buttons = QHBoxLayout()
        self.generation_back_btn = QPushButton('← Back to source')
        self.generation_back_btn.clicked.connect(self._go_source)
        generation_buttons.addWidget(self.generation_back_btn)
        self.generation_previous_btn = QPushButton('Review previous result')
        self.generation_previous_btn.clicked.connect(self._go_review)
        self.generation_previous_btn.setVisible(False)
        generation_buttons.addWidget(self.generation_previous_btn)
        generation_buttons.addStretch(1)
        self.retry_generation_btn = QPushButton('Try again')
        self.retry_generation_btn.clicked.connect(self.generate_profiles)
        self.retry_generation_btn.setVisible(False)
        generation_buttons.addWidget(self.retry_generation_btn)
        self.abort_generation_btn = QPushButton('Abort generation')
        self.abort_generation_btn.clicked.connect(self.cancel_generation)
        generation_buttons.addWidget(self.abort_generation_btn)
        generation_layout.addLayout(generation_buttons)
        self.pages.addWidget(generation_page)

        # Compatibility control retained but not shown: generation output is
        # always visible on the dedicated Generate page.
        self.details_toggle = QPushButton()
        self.details_toggle.setCheckable(True)

        # ------------------------------------------------------------------
        # 3. REVIEW & EXPORT
        # ------------------------------------------------------------------
        review_page = QWidget()
        review_layout = QVBoxLayout(review_page)
        review_intro = QLabel(
            'Review the recommended profile for each selected platform. Returning '
            'from playback marks that exact profile reviewed; its export name then '
            'becomes editable and Export for POC is enabled.')
        review_intro.setWordWrap(True)
        review_layout.addWidget(review_intro)

        self.review_platform = {}
        for platform_key, platform_label in PLATFORM_LABELS.items():
            group = QGroupBox(platform_label)
            gl = QVBoxLayout(group)
            status = QLabel()
            status.setWordWrap(True)
            gl.addWidget(status)
            candidate_row = QHBoxLayout()
            candidate_row.addWidget(QLabel('Profile'))
            selector = QComboBox()
            selector.setMinimumContentsLength(44)
            candidate_row.addWidget(selector, 1)
            review_btn = QPushButton('Review profile')
            candidate_row.addWidget(review_btn)
            dismiss_btn = QPushButton('Dismiss')
            dismiss_btn.setToolTip('Hide/restore this research candidate; generated evidence is preserved.')
            candidate_row.addWidget(dismiss_btn)
            gl.addLayout(candidate_row)
            export_row = QHBoxLayout()
            export_row.addWidget(QLabel('POC profile name'))
            name = QLineEdit()
            name.setPlaceholderText('Review profile before export')
            export_row.addWidget(name, 1)
            export_btn = QPushButton('Export for POC')
            export_row.addWidget(export_btn)
            open_btn = QPushButton('Open export folder')
            open_btn.setVisible(False)
            export_row.addWidget(open_btn)
            gl.addLayout(export_row)
            export_note = QLabel()
            export_note.setWordWrap(True)
            gl.addWidget(export_note)
            review_layout.addWidget(group)
            self.review_platform[platform_key] = {
                'group': group, 'status': status, 'selector': selector,
                'review': review_btn, 'dismiss': dismiss_btn, 'name': name,
                'export': export_btn, 'open': open_btn, 'note': export_note,
            }
            selector.currentIndexChanged.connect(
                lambda _index, key=platform_key: self._review_candidate_changed(key))
            review_btn.clicked.connect(
                lambda _checked=False, key=platform_key: self._review_profile_for_platform(key))
            dismiss_btn.clicked.connect(
                lambda _checked=False, key=platform_key: self._dismiss_profile_for_platform(key))
            name.textEdited.connect(
                lambda text, key=platform_key: self._export_name_edited(key, text))
            export_btn.clicked.connect(
                lambda _checked=False, key=platform_key: self._export_profile_for_platform(key))
            open_btn.clicked.connect(
                lambda _checked=False, key=platform_key: self._open_platform_export_folder(key))

        engineering = QHBoxLayout()
        self.report_btn = QPushButton('View generation report')
        self.report_btn.clicked.connect(self.open_report)
        engineering.addWidget(self.report_btn)
        self.regenerate_btn = QPushButton('Regenerate profiles...')
        self.regenerate_btn.clicked.connect(self.regenerate_profiles)
        engineering.addWidget(self.regenerate_btn)
        self.show_dismissed = QCheckBox('Show dismissed')
        self.show_dismissed.toggled.connect(self.populate_candidates)
        engineering.addWidget(self.show_dismissed)
        engineering.addStretch(1)
        self.process_another_btn = QPushButton('Process another ride')
        self.process_another_btn.clicked.connect(self._go_source)
        engineering.addWidget(self.process_another_btn)
        review_layout.addLayout(engineering)
        review_layout.addStretch(1)
        self.pages.addWidget(review_page)

        # Hidden compatibility selectors/state labels used by the established
        # candidate/review persistence backend. The wizard surfaces one selector
        # per physical platform instead of one global candidate dropdown.
        self.candidate = QComboBox()
        self.candidate_status = QLabel()
        self.candidate_detail = QLabel()
        self.dismiss_btn = QPushButton()
        self.review_again_btn = QPushButton()
        self.primary_btn = QPushButton()
        self.stage_title = QLabel()
        self.stage_detail = QLabel()
        self.package_group = QWidget()
        self.package_group.setVisible(False)
        self.package_note = QLabel()
        self.open_export_btn = QPushButton()
        self.copy_export_btn = QPushButton()

        # ------------------------------------------------------------------
        # Persistent exported-candidate inventory
        # ------------------------------------------------------------------
        export_group = QGroupBox('Exported POC candidates')
        export_group.setMinimumWidth(360)
        export_group.setMaximumWidth(440)
        export_layout = QVBoxLayout(export_group)
        export_path_row = QHBoxLayout()
        self.export_folder_label = QLabel('exports/dynamic_candidates')
        self.export_folder_label.setStyleSheet('color:#aebdca;')
        self.export_folder_label.setToolTip(str(ROOT / 'exports' / 'dynamic_candidates'))
        export_path_row.addWidget(self.export_folder_label, 1)
        self.export_refresh_btn = QPushButton('Refresh')
        self.export_refresh_btn.setMaximumWidth(90)
        self.export_refresh_btn.clicked.connect(self.refresh_exported_candidates)
        export_path_row.addWidget(self.export_refresh_btn)
        export_layout.addLayout(export_path_row)

        self.export_watcher = QFileSystemWatcher(self)
        self.export_watcher.directoryChanged.connect(self._export_folder_changed)

        self.export_tree = QTreeWidget()
        self.export_tree.setColumnCount(2)
        self.export_tree.setHeaderLabels(['Ride', 'Candidate'])
        self.export_tree.setRootIsDecorated(True)
        self.export_tree.setUniformRowHeights(True)
        self.export_tree.setMouseTracking(True)
        self.export_tree.setToolTip(
            'POC-ready ZIPs currently in the Analyzer export folder. '
            'Selecting an item does not change the Source session or ride. '
            'Double-click a candidate to open its folder.')
        header = self.export_tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.export_tree.itemSelectionChanged.connect(self._export_selection_changed)
        self.export_tree.itemDoubleClicked.connect(self._export_item_double_clicked)
        export_layout.addWidget(self.export_tree, 1)

        self.export_summary = QLabel()
        self.export_summary.setWordWrap(True)
        self.export_summary.setStyleSheet('color:#aebdca;')
        export_layout.addWidget(self.export_summary)

        export_actions = QHBoxLayout()
        self.export_open_btn = QPushButton('Open selected folder')
        self.export_open_btn.setEnabled(False)
        self.export_open_btn.clicked.connect(self._open_selected_export_folder)
        export_actions.addWidget(self.export_open_btn)
        export_actions.addStretch(1)
        export_layout.addLayout(export_actions)

        self.content_row.addWidget(export_group)

        self.session.currentIndexChanged.connect(self._session_selection_changed)
        self.ride.currentIndexChanged.connect(self._ride_selection_changed)
        self.platform_chair.toggled.connect(self._platform_selection_changed)
        self.platform_ground.toggled.connect(self._platform_selection_changed)
        self.candidate.currentIndexChanged.connect(self.update_workflow_state)

        platform_value = self.context.get('platform') or settings.value(
            'dynamic_ride_workflow_platform', 'both')
        self._set_platform_selection(str(platform_value))
        self.populate_sessions()
        output = self.context.get('output') or settings.value('dynamic_ride_workflow_output', '')
        if output:
            if self.set_result_dir(Path(str(output))):
                self._force_source_page = False
        self.refresh_exported_candidates()
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
        # The three-stage wizard has a dedicated Generate page; progress/details stay visible.
        self.log.setVisible(True)
        if not self._generation_running():
            self._refresh_generation_details()

    def _set_details_visible(self, visible):
        # Compatibility no-op: generation output is always visible on page 2.
        self.log.setVisible(True)

    def _generation_running(self):
        return self.process is not None and self.process.state() != QProcess.ProcessState.NotRunning

    def _generation_log_path(self):
        return self._result_dir / 'analyzer_generation_output.txt' if self._result_dir else None

    def _update_details_toggle_text(self):
        # Retained for callers from the pre-wizard implementation.
        return

    def _generation_summary_text(self):
        if not self._result_dir:
            return 'No completed generation result is currently selected.'
        lines = ['Generated profile set: ' + str(self._result_dir)]
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
                'Detailed optimiser output was not retained for this older generated profile set.',
                'Use View generation report for the full qualification results.',
            ])
        return '\n'.join(lines)

    def _refresh_generation_details(self):
        if self._generation_running():
            return
        self.log.setPlainText(self._generation_summary_text())

    def _selected_platforms(self):
        selected = []
        if self.platform_chair.isChecked():
            selected.append('chair_v3')
        if self.platform_ground.isChecked():
            selected.append('platform_v2')
        return tuple(selected)

    def _platform_selection_key(self):
        selected = self._selected_platforms()
        if len(selected) == 2:
            return 'both'
        return selected[0] if selected else ''

    def _platform_selection_text(self):
        selected = self._selected_platforms()
        if len(selected) == 2:
            return 'Chair V3 and Ground-Loading Platform'
        if len(selected) == 1:
            return PLATFORM_LABELS[selected[0]]
        return 'no platform'

    def _set_platform_selection(self, value):
        value = str(value or 'both')
        self.platform_chair.blockSignals(True)
        self.platform_ground.blockSignals(True)
        self.platform_chair.setChecked(value in ('both', 'chair_v3'))
        self.platform_ground.setChecked(value in ('both', 'platform_v2'))
        self.platform_chair.blockSignals(False)
        self.platform_ground.blockSignals(False)

    def _selected_platforms_have_profiles(self):
        selected = set(self._selected_platforms())
        if not selected:
            return False
        available = {choice.platform for choice in self._candidate_choices.values()}
        return selected.issubset(available)

    def _source_next(self):
        if self._selected_platforms_have_profiles():
            self._force_source_page = False
            self._go_review()
            return
        self.generate_profiles()

    def _go_source(self):
        if self._generation_running():
            return
        self._force_source_page = True
        self.pages.setCurrentIndex(0)
        self._set_progress('source')
        self.update_workflow_state()

    def _go_review(self):
        if not self._candidate_choices:
            return
        self._force_source_page = False
        self.pages.setCurrentIndex(2)
        self._set_progress('review')
        self._refresh_review_panels()

    def populate_sessions(self, directory=None, preferred_session=None):
        """Populate sessions from exactly one parent folder.

        A fresh workspace always starts at captures/dynamic_source. Choosing a
        different folder changes only this open workspace; it is not persisted
        as the next startup root.
        """
        root = Path(directory or self._source_root).expanduser().resolve()
        self._source_root = root
        self.source_folder_label.setText(str(root))
        self.source_folder_label.setToolTip(str(root))
        self.sessions = find_sessions(root)

        desired = str(preferred_session or self.context.get('session') or
                      self.settings.value('dynamic_ride_workflow_session', ''))
        self.session.blockSignals(True)
        self.session.clear()
        generated_root = ROOT / 'studies/nl2/dynamic_profiles'
        for row in self.sessions:
            description, tooltip = session_status_display(row, generated_root)
            self.session.addItem(description, str(row.path))
            index = self.session.count() - 1
            self.session.setItemData(index, tooltip, Qt.ItemDataRole.ToolTipRole)
        index = self.session.findData(desired)
        if index < 0 and self.session.count():
            index = 0
        if index >= 0:
            self.session.setCurrentIndex(index)
        self.session.blockSignals(False)
        self._session_selection_changed()

    def populate_rides(self, preferred=None):
        """Populate rides and expose retained profile work without changing it."""
        session = self.current_session()
        prior = str(preferred or self.ride.currentData() or self._preferred_ride or '')
        rides = available_rides(session)
        generated_root = ROOT / 'studies/nl2/dynamic_profiles'
        self._session_processing = (
            session_processing_details(session, generated_root) if session is not None else {})
        self.ride.blockSignals(True)
        self.ride.clear()
        for key in rides:
            description, tooltip = ride_processing_display(
                key, self._session_processing.get(key, {}))
            self.ride.addItem(description, key)
            row = self.ride.count() - 1
            self.ride.setItemData(row, tooltip, Qt.ItemDataRole.ToolTipRole)
        index = self.ride.findData(prior)
        if index < 0 and self.ride.count():
            index = 0
        if index >= 0:
            self.ride.setCurrentIndex(index)
            self._preferred_ride = str(self.ride.currentData())
        self.ride.blockSignals(False)
        self.ride.setEnabled(bool(rides) and not self._generation_running())
        return rides


    def capture_session(self):
        from .dynamic_source_dialog import DynamicSourceCaptureDialog
        initial_ride = str(self.ride.currentData() or self._preferred_ride or 'blackbird')
        dialog = DynamicSourceCaptureDialog(initial_ride, self)
        dialog.exec()
        preferred_session = str(dialog.session_dir) if dialog.session_dir is not None else None
        self.populate_sessions(self._source_root, preferred_session=preferred_session)
        if dialog.open_recording_path is not None:
            from .dynamic_source_rating import _find_session_dir
            source = Path(dialog.open_recording_path)
            session = _find_session_dir(source)
            if session is not None:
                index = self.session.findData(str(session))
                if index >= 0:
                    self.session.setCurrentIndex(index)
            self.action = 'recording'
            self.selected_path = source
            self.context = self.save_context()
            self.accept()

    def current_session(self):
        index = self.session.currentIndex()
        return self.sessions[index] if 0 <= index < len(self.sessions) else None

    def _session_selection_changed(self, *_args):
        self.populate_rides(self._preferred_ride)
        self._source_context_changed()

    def _ride_selection_changed(self, *_args):
        if self.ride.currentData():
            self._preferred_ride = str(self.ride.currentData())
        self._source_context_changed()
        self._update_exported_ride_highlight()

    def _platform_selection_changed(self, *_args):
        self._force_source_page = True
        self._generation_failed = False
        if self._result_dir is not None and not self._result_matches_current_selection(self._result_dir):
            self._clear_result_selection()
        self._restore_saved_result_if_matching()
        self.populate_candidates()
        self.update_workflow_state()

    def _source_context_changed(self):
        self._force_source_page = True
        self._generation_failed = False
        self._detach_mismatched_result()
        self._restore_saved_result_if_matching()
        self.populate_recordings()
        self.populate_candidates()
        self.update_workflow_state()

    def _result_platforms(self, directory):
        platforms = set()
        run_dir = Path(directory)
        if not (run_dir / 'report.json').is_file():
            return platforms
        for profile_path in run_dir.glob('*/*/*/profile.json'):
            try:
                payload = json.loads(profile_path.read_text(encoding='utf-8-sig'))
                platform = str(payload.get('identity', {}).get('platform', '')).casefold()
            except (OSError, ValueError, TypeError):
                continue
            if platform in PLATFORM_LABELS:
                platforms.add(platform)
        return platforms

    def _result_matches_current_selection(self, directory):
        if not self._result_matches_current_source(directory):
            return False
        selected = set(self._selected_platforms())
        return not selected or selected.issubset(self._result_platforms(directory))

    def _clear_result_selection(self):
        self._result_dir = None
        self._dismissed = set()
        self._reviewed = set()
        self._prepared = {}
        self._candidate_choices = {}
        self._all_candidate_choices = {}
        self._recommended = {}
        self._prepared_package = None
        self.package_group.setVisible(False)
        self.candidate.blockSignals(True)
        self.candidate.clear()
        self.candidate.blockSignals(False)
        self._refresh_generation_details()
        self._update_details_toggle_text()

    def _restore_saved_result_if_matching(self):
        if self._result_dir is not None:
            return
        value = self.context.get('output') or self.settings.value('dynamic_ride_workflow_output', '')
        if value:
            directory = Path(str(value))
            if self._result_matches_current_selection(directory) and self.set_result_dir(directory):
                return

        # The wizard is also a resume surface. If the globally remembered last
        # result belongs to another session/ride, find the newest completed
        # result for the current source instead of offering an unnecessary
        # regeneration. Incomplete runs are intentionally not adopted.
        root = ROOT / 'studies/nl2/dynamic_profiles'
        try:
            reports = sorted(
                root.rglob('report.json'),
                key=lambda path: path.stat().st_mtime, reverse=True)
        except OSError:
            reports = []
        for report in reports:
            directory = report.parent
            if self._result_matches_current_selection(directory) and self.set_result_dir(directory):
                return

    def _result_matches_current_source(self, directory):
        session = self.current_session()
        ride = self.ride.currentData()
        if session is None or not ride:
            return False
        settings_path = Path(directory) / 'run_settings.json'
        try:
            payload = json.loads(settings_path.read_text(encoding='utf-8'))
        except (OSError, ValueError, TypeError):
            return False
        inputs = payload.get('inputs', ())
        if isinstance(inputs, str):
            inputs = (inputs,)
        if not isinstance(inputs, (list, tuple)):
            return False
        source_match = False
        for value in inputs:
            candidate = Path(str(value)).expanduser()
            try:
                source_match = candidate.resolve() == session.path.resolve()
            except OSError:
                source_match = False
            if not source_match and candidate.name == session.path.name:
                source_match = True
            if source_match:
                break
        rides = payload.get('rides', '')
        if isinstance(rides, str):
            rides = {part.strip().casefold() for part in rides.split(',') if part.strip()}
        elif isinstance(rides, (list, tuple, set)):
            rides = {str(part).strip().casefold() for part in rides if str(part).strip()}
        else:
            rides = set()
        return source_match and str(ride).casefold() in rides

    def _detach_mismatched_result(self):
        if self._result_dir is None or self._result_matches_current_source(self._result_dir):
            return
        self._clear_result_selection()

    def _generation_source_rows(self):
        session = self.current_session()
        ride = self.ride.currentData()
        if session is None or not ride:
            return ()
        return rated_recordings(session, ride)

    def _generation_ready(self):
        rows = self._generation_source_rows()
        # Avoid is a hard/basic capture fault. Other ratings remain eligible
        # for the optimiser to make the final compatibility decision.
        return sum(hint.rating != 'Avoid' for _, hint in rows) >= 2

    def populate_recordings(self):
        st = self.current_session()
        ride = self.ride.currentData()
        self.recordings.clear()
        if st and ride:
            rows = rated_recordings(st, ride)
            for path, hint in rows:
                item = QListWidgetItem(f'{hint.rating:10s}  {path.name}')
                item.setData(Qt.ItemDataRole.UserRole, str(path))
                item.setToolTip(hint.tooltip())
                self.recordings.addItem(item)
            usable = sum(hint.rating != 'Avoid' for _, hint in rows)
            note = (
                f'{len(rows)} primary recording(s) for {ride_label(ride)}; {usable} not rated Avoid. '
                "Generation uses this ride's compatible completed runs and selects reference, fit and held-out runs automatically."
            )
            if usable < 2:
                note += ' At least two non-Avoid recordings are required before generation can start.'
            self.session_note.setText(note)
            self.settings.setValue('dynamic_ride_workflow_session', str(st.path))
        elif st:
            self.session_note.setText(
                'This session contains no Analyzer-visible primary ride recordings. Choose another session or capture a new session.'
            )
        else:
            self.session_note.setText(
                'No dynamic-source session with completed recordings was found in this folder. Capture a new session or choose another folder.'
            )
        self.open_recording_btn.setEnabled(self.recordings.count() > 0 and not self._generation_running())
        if self.recordings.count():
            self.recordings.setCurrentRow(0)
        self.source_toggle.setText(
            'Hide source recordings' if self.source_toggle.isChecked() else
            f'Source recordings (optional) - {self.recordings.count()} available'
        )

    def save_context(self):
        st = self.current_session()
        data = {
            'session': str(st.path) if st else '',
            'ride': str(self.ride.currentData() or ''),
            'platform': str(self._platform_selection_key() or ''),
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
        session = self.current_session()
        ride = self.ride.currentData()
        platform_selection = self._platform_selection_key()
        if not session or not ride:
            QMessageBox.warning(self, 'No ride data', 'Select a session containing ride recordings first.')
            return
        if not platform_selection:
            QMessageBox.warning(self, 'No platform selected', 'Select at least one target platform.')
            return
        if not self._generation_ready():
            QMessageBox.warning(
                self, 'Insufficient recordings',
                'At least two recordings not rated Avoid are required for this ride.'
            )
            return
        if self.process is not None and self.process.state() != QProcess.ProcessState.NotRunning:
            return
        self.save_context()
        self._force_source_page = False
        self._generation_failed = False
        self.output_directory = None
        self._stdout = ''
        self._generation_output = []
        self.log.clear()
        self.pages.setCurrentIndex(1)
        self._set_progress('generate')
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
                '-m', 'tools.sim_player.dynamic_optimizer', str(session.path),
                '--rides', str(ride),
                '--platforms', platform_selection,
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
                'Generate a new profile set for the current session/ride/platform selection?\n\n'
                'The existing generated profile set and reviews will be preserved.'
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
            self._generation_failed = False
            self.set_result_dir(self.output_directory)
            self.populate_rides(self.ride.currentData())
            self._force_source_page = False
            self.pages.setCurrentIndex(2)
        else:
            self._generation_failed = True
            self.append_log(
                f'Generation ended with exit code {code}; incomplete results cannot be exported.'
            )
            self.pages.setCurrentIndex(1)
        self.update_workflow_state()

    def cancel_generation(self):
        if self.process is not None and self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()
            self._generation_failed = True
            self.append_log('Generation cancelled; incomplete results cannot be exported.')
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
            return False
        if not self._result_matches_current_source(directory):
            return False
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
        return True

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
        self._all_candidate_choices = {}
        choices = []
        if self._result_dir:
            from .dynamic_review import profile_choice
            ride = str(self.ride.currentData())
            platform_filter = str(self._platform_selection_key())
            for path in self._result_dir.glob('*/*/*/profile.json'):
                try:
                    choice = profile_choice(path)
                except (OSError, ValueError, KeyError):
                    continue
                if choice.park != ride:
                    continue
                path_text = str(choice.path)
                self._all_candidate_choices[path_text] = choice
                if platform_filter not in ('', 'both') and choice.platform != platform_filter:
                    continue
                if platform_filter == '':
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
        source_ready = self.current_session() is not None and self.ride.currentData() is not None
        return workflow_stage(
            has_study=source_ready,
            generating=process_running,
            has_result=bool(self._result_dir and (self._result_dir / 'report.json').is_file()),
            has_candidate=candidate is not None,
            reviewed=bool(candidate and self._candidate_reviewed(candidate)),
            prepared=bool(candidate and self._candidate_prepared_path(candidate)),
        )

    def _set_progress(self, stage, current_text='', next_text=''):
        self.progress_strip.set_stage(stage, current_text=current_text, next_text=next_text)

    def _select_hidden_candidate(self, path):
        if path is None:
            return False
        index = self.candidate.findData(str(Path(path)))
        if index < 0:
            return False
        self.candidate.blockSignals(True)
        self.candidate.setCurrentIndex(index)
        self.candidate.blockSignals(False)
        return True

    def _platform_panel_candidate(self, platform_key):
        panel = self.review_platform[platform_key]
        value = panel['selector'].currentData()
        return Path(value) if value else None

    def _platform_workflow_status(self, platform_key):
        ride = self.ride.currentData()
        entry = self._session_processing.get(str(ride), {}).get(platform_key) if ride else None
        text, tooltip = platform_processing_display(entry)
        self.platform_status[platform_key].setToolTip(tooltip)
        return text

    def _refresh_review_panels(self):
        selected = set(self._selected_platforms())
        for platform_key, panel in self.review_platform.items():
            panel['group'].setVisible(platform_key in selected)
            prior = panel['selector'].currentData()
            rows = [choice for choice in self._all_candidate_choices.values()
                    if choice.platform == platform_key]
            rows.sort(key=candidate_sort_key)
            dismissed_paths = {
                str((self._result_dir / rel).resolve()) for rel in self._dismissed
            } if self._result_dir else set()
            recommended = recommended_candidate_paths(rows, dismissed_paths).get(platform_key)

            panel['selector'].blockSignals(True)
            panel['selector'].clear()
            for choice in rows:
                rel = self._choice_relative(choice.path)
                dismissed = rel in self._dismissed
                if dismissed and not self.show_dismissed.isChecked():
                    continue
                target = TARGET_LABELS.get(choice.target, choice.target)
                qualification = 'Qualified' if candidate_is_qualified(choice) else str(choice.qualification)
                suffix = []
                if str(choice.path) == recommended and not dismissed:
                    suffix.append('Recommended')
                if dismissed:
                    suffix.append('Dismissed')
                text = f'{target} - {qualification}'
                if suffix:
                    text += ' - ' + ', '.join(suffix)
                panel['selector'].addItem(text, str(choice.path))
            index = panel['selector'].findData(prior)
            if index < 0 and recommended:
                index = panel['selector'].findData(recommended)
            if index < 0 and panel['selector'].count():
                index = 0
            if index >= 0:
                panel['selector'].setCurrentIndex(index)
            panel['selector'].blockSignals(False)
            self._update_review_platform_panel(platform_key)

    def _review_candidate_changed(self, platform_key):
        self._update_review_platform_panel(platform_key)

    def _export_package_label(self, package):
        if not package or not Path(package).is_file():
            return ''
        try:
            from zipfile import ZipFile
            with ZipFile(package) as archive:
                payload = json.loads(archive.read('dynamic_candidate.json').decode('utf-8-sig'))
            return str(payload.get('candidate', {}).get('label', ''))
        except Exception:
            return ''

    def _export_candidate_tooltip(self, row):
        ride_name = ride_label(row.park)
        short_label = short_candidate_label(row.label, ride_name)
        target = TARGET_LABELS.get(row.target, row.target or 'Qualification target not recorded')
        title = f'{target} ({short_label})' if short_label else target
        timestamp = datetime.fromtimestamp(row.modified_time).strftime('%d %b %Y %H:%M')
        lines = [
            title, '',
            f'Ride: {ride_name}',
            f'Platform: {PLATFORM_LABELS.get(row.platform, row.platform)}',
            f'POC profile: {row.label}',
            f'Package timestamp: {timestamp}',
        ]
        if row.source_recordings is not None:
            lines.append(f'Qualification recordings: {row.source_recordings}')
        if row.operational_status:
            lines.append('Package status: ' + row.operational_status.replace('_', ' '))
        lines.extend([f'Package: {row.package.name}', str(row.package)])
        return '\n'.join(lines)

    def refresh_exported_candidates(self):
        folder = ROOT / 'exports' / 'dynamic_candidates'
        if folder.is_dir() and hasattr(self, 'export_watcher'):
            if str(folder) not in self.export_watcher.directories():
                self.export_watcher.addPath(str(folder))
        rows, invalid = discover_exported_candidates(folder)
        self.export_tree.clear()
        markers = {'chair_v3': '●', 'platform_v2': '◆'}
        platform_order = ('chair_v3', 'platform_v2')
        other_platforms = sorted(
            {row.platform for row in rows if row.platform not in platform_order}, key=str.casefold)
        for platform_key in platform_order + tuple(other_platforms):
            platform_rows = [row for row in rows if row.platform == platform_key]
            if not platform_rows and platform_key not in platform_order:
                continue
            marker = markers.get(platform_key, '•')
            group = QTreeWidgetItem([
                f'{marker} {PLATFORM_LABELS.get(platform_key, platform_key)}', ''
            ])
            group.setFlags(group.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            font = group.font(0)
            font.setBold(True)
            group.setFont(0, font)
            self.export_tree.addTopLevelItem(group)
            group.setFirstColumnSpanned(True)
            group.setExpanded(True)

            platform_rows.sort(
                key=lambda row: (ride_label(row.park).casefold(), row.label.casefold(), row.package.name.casefold()))
            for row in platform_rows:
                ride_name = ride_label(row.park)
                candidate_name = short_candidate_label(row.label, ride_name)
                item = QTreeWidgetItem([ride_name, candidate_name])
                item.setData(0, Qt.ItemDataRole.UserRole, str(row.package))
                item.setData(1, Qt.ItemDataRole.UserRole, row.park)
                tooltip = self._export_candidate_tooltip(row)
                item.setToolTip(0, tooltip)
                item.setToolTip(1, tooltip)
                group.addChild(item)

        count = len(rows)
        if count:
            text = f'{count} exported candidate package' + ('' if count == 1 else 's')
        else:
            text = 'No exported candidate packages found.'
        if invalid:
            text += f'  {invalid} invalid/non-candidate ZIP' + ('' if invalid == 1 else 's') + ' ignored.'
        self.export_summary.setText(text)
        self._update_exported_ride_highlight()
        self._export_selection_changed()

    def _export_folder_changed(self, _path):
        # Windows can signal before a copied/generated ZIP has closed.  Delay
        # the rescan slightly and coalesce filesystem notifications.
        QTimer.singleShot(200, self.refresh_exported_candidates)

    def _update_exported_ride_highlight(self):
        if not hasattr(self, 'export_tree'):
            return
        current_ride = str(self.ride.currentData() or '')
        for top_index in range(self.export_tree.topLevelItemCount()):
            group = self.export_tree.topLevelItem(top_index)
            for child_index in range(group.childCount()):
                item = group.child(child_index)
                match = bool(current_ride and item.data(1, Qt.ItemDataRole.UserRole) == current_ride)
                for column in range(2):
                    font = item.font(column)
                    font.setBold(match)
                    item.setFont(column, font)

    def _selected_export_package(self):
        item = self.export_tree.currentItem() if hasattr(self, 'export_tree') else None
        if item is None:
            return None
        value = item.data(0, Qt.ItemDataRole.UserRole)
        return Path(str(value)) if value else None

    def _export_selection_changed(self):
        package = self._selected_export_package()
        self.export_open_btn.setEnabled(bool(package and package.is_file()))

    def _export_item_double_clicked(self, item, _column):
        value = item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None
        if value:
            package = Path(str(value))
            if package.is_file():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(package.parent)))

    def _open_selected_export_folder(self):
        package = self._selected_export_package()
        if package and package.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(package.parent)))

    def _existing_export_labels(self, platform_key, park):
        labels = set()
        folder = ROOT / 'exports' / 'dynamic_candidates'
        if not folder.is_dir():
            return labels
        from zipfile import ZipFile, BadZipFile
        for package in folder.glob('*.zip'):
            try:
                with ZipFile(package) as archive:
                    payload = json.loads(archive.read('dynamic_candidate.json').decode('utf-8-sig'))
                if payload.get('platform') == platform_key and payload.get('park') == park:
                    label = payload.get('candidate', {}).get('label')
                    if isinstance(label, str) and label:
                        labels.add(label)
            except (OSError, BadZipFile, KeyError, ValueError, UnicodeError):
                continue
        return labels

    def _default_export_label(self, choice):
        from sims.nolimits2.ride_config import PARKS
        park = str(choice.park)
        base = PARKS.get(park, {}).get('name', ride_label(park))
        existing = self._existing_export_labels(choice.platform, park)
        for suffix in list('ABCDEFGHIJKLMNOPQRSTUVWXYZ') + [str(i) for i in range(2, 100)]:
            candidate = f'{base} [Dynamic {suffix}]'
            if candidate not in existing:
                return candidate
        return f'{base} [Dynamic]'

    def _validate_export_name(self, choice, label, prepared_path=None):
        label = str(label).strip()
        if not label:
            return False, 'Enter a profile name before export.'
        from sims.nolimits2 import ride_config
        try:
            ride_config.validate_variant_label_format(label, choice.park)
        except Exception as exc:
            return False, str(exc)
        existing = self._existing_export_labels(choice.platform, choice.park)
        prepared_label = self._export_package_label(prepared_path)
        if label in existing and label != prepared_label:
            return False, 'A package with this platform/ride profile name has already been exported.'
        return True, ''

    def _update_review_platform_panel(self, platform_key):
        panel = self.review_platform[platform_key]
        path = self._platform_panel_candidate(platform_key)
        choice = self._all_candidate_choices.get(str(path)) if path else None
        if choice is None:
            panel['status'].setText('No generated profile is available for this platform.')
            panel['review'].setEnabled(False)
            panel['dismiss'].setEnabled(False)
            panel['name'].setEnabled(False)
            panel['export'].setEnabled(False)
            panel['open'].setVisible(False)
            panel['note'].clear()
            return

        rel = self._choice_relative(path)
        dismissed = rel in self._dismissed
        reviewed = self._candidate_reviewed(path)
        prepared = self._candidate_prepared_path(path)
        recommended = self._recommended.get(platform_key) == str(path) and not dismissed
        bits = [TARGET_LABELS.get(choice.target, choice.target)]
        bits.append('Qualified' if candidate_is_qualified(choice) else f'Not qualified: {choice.qualification}')
        if recommended:
            bits.append('Recommended')
        if reviewed:
            bits.append('Reviewed')
        if prepared:
            bits.append('Exported')
        if dismissed:
            bits.append('Dismissed')
        panel['status'].setText('  |  '.join(bits))
        panel['review'].setEnabled(not dismissed)
        panel['dismiss'].setEnabled(True)
        panel['dismiss'].setText('Restore' if dismissed else 'Dismiss')

        path_key = str(path)
        if prepared:
            label = self._export_package_label(prepared)
            if label:
                self._export_name_overrides[path_key] = label
        elif path_key not in self._export_name_overrides:
            self._export_name_overrides[path_key] = self._default_export_label(choice)
        desired = self._export_name_overrides.get(path_key, '')
        if panel['name'].text() != desired:
            panel['name'].blockSignals(True)
            panel['name'].setText(desired)
            panel['name'].blockSignals(False)

        panel['name'].setEnabled(reviewed and not dismissed and prepared is None)
        valid_name, reason = self._validate_export_name(choice, desired, prepared)
        can_export = (reviewed and not dismissed and prepared is None and
                      candidate_is_qualified(choice) and valid_name)
        panel['export'].setEnabled(can_export)
        panel['export'].setText('Export for POC' if prepared is None else 'Exported')
        panel['open'].setVisible(bool(prepared))
        panel['open'].setEnabled(bool(prepared))
        if prepared:
            panel['note'].setText(
                f'Exported: {prepared.name}\nFolder: {prepared.parent}')
        elif not reviewed:
            panel['note'].setText('Review this profile before export.')
        elif dismissed:
            panel['note'].setText('Restore this candidate before export.')
        elif not candidate_is_qualified(choice):
            panel['note'].setText('This candidate is not qualified and cannot be exported for POC.')
        elif not valid_name:
            panel['note'].setText(reason)
        else:
            panel['note'].setText(
                'Ready to export. Export validates the qualification evidence and builds the immutable POC ZIP.')

    def _review_profile_for_platform(self, platform_key):
        path = self._platform_panel_candidate(platform_key)
        if path is None or not self._select_hidden_candidate(path):
            return
        self.review_profile()

    def _dismiss_profile_for_platform(self, platform_key):
        path = self._platform_panel_candidate(platform_key)
        if path is None or not self._select_hidden_candidate(path):
            return
        self.dismiss_profile()

    def _export_name_edited(self, platform_key, text):
        path = self._platform_panel_candidate(platform_key)
        if path is not None:
            self._export_name_overrides[str(path)] = str(text)
        self._update_review_platform_panel(platform_key)

    def _export_profile_for_platform(self, platform_key):
        path = self._platform_panel_candidate(platform_key)
        if path is None:
            return
        choice = self._all_candidate_choices.get(str(path))
        if choice is None:
            return
        rel = self._choice_relative(path)
        if rel in self._dismissed:
            QMessageBox.warning(self, 'Candidate dismissed', 'Restore this candidate before export.')
            return
        if not self._candidate_reviewed(path):
            QMessageBox.warning(self, 'Review required', 'Review this exact profile before exporting it for POC.')
            return
        if not candidate_is_qualified(choice):
            QMessageBox.warning(self, 'Candidate not qualified', 'Only a qualified profile can be exported for POC.')
            return
        label = self.review_platform[platform_key]['name'].text().strip()
        valid, reason = self._validate_export_name(choice, label)
        if not valid:
            QMessageBox.warning(self, 'Cannot export candidate', reason)
            return
        try:
            package, _rel, summary = prepare_bundle(
                path.parent, label, export_dir=ROOT / 'exports' / 'dynamic_candidates')
        except Exception as exc:
            QMessageBox.warning(self, 'Cannot export candidate', str(exc))
            return
        self._prepared[rel] = str(package)
        self._save_workflow_state()
        self._prepared_package = package
        self._export_name_overrides[str(path)] = label
        QApplication.clipboard().setText(str(package.parent))
        self.append_log('Exported for POC: ' + str(package))
        self.populate_rides(self.ride.currentData())
        self.refresh_exported_candidates()
        self.update_workflow_state()
        QMessageBox.information(
            self, 'Dynamic profile exported for POC',
            f'Candidate: {label}\nPlatform: {summary["platform"]}\n'
            f'Recordings: {summary["source_recordings"]} (including held-out validation)\n'
            f'Package: {package}\n\nFolder path copied to clipboard. '
            'The candidate remains uncommissioned until it is imported and tested on the target POC.')

    def _open_platform_export_folder(self, platform_key):
        path = self._platform_panel_candidate(platform_key)
        package = self._candidate_prepared_path(path) if path else None
        if package:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(package.parent)))

    def update_workflow_state(self):
        has_result = bool(self._result_dir and (self._result_dir / 'report.json').is_file())
        process_running = self._generation_running()
        selected = self._selected_platforms()

        self.session.setEnabled(not process_running)
        self.ride.setEnabled(self.ride.count() > 0 and not process_running)
        self.platform_chair.setEnabled(not process_running)
        self.platform_ground.setEnabled(not process_running)
        self.open_recording_btn.setEnabled(self.recordings.count() > 0 and not process_running)
        self.report_btn.setEnabled(has_result and (self._result_dir / 'report.html').is_file())
        self.report_btn.setVisible(has_result)
        self.regenerate_btn.setEnabled(bool(selected) and self._generation_ready() and not process_running)
        self.regenerate_btn.setVisible(has_result)
        self.show_dismissed.setEnabled(has_result and not process_running)
        self.show_dismissed.setVisible(has_result)

        for platform_key in PLATFORM_LABELS:
            self.platform_status[platform_key].setText(self._platform_workflow_status(platform_key))

        source_ready = (
            self.current_session() is not None and self.ride.currentData() is not None and
            bool(selected) and self._generation_ready()
        )
        existing_ready = self._selected_platforms_have_profiles()
        self.source_next_btn.setText(
            'Use existing profile(s) →' if existing_ready else 'Generate profiles →')
        self.source_next_btn.setEnabled(existing_ready or source_ready)
        self.source_generate_new_btn.setVisible(existing_ready)
        self.source_generate_new_btn.setEnabled(existing_ready and source_ready and not process_running)

        ride = self.ride.currentData()
        session = self.current_session()
        self.generation_context.setText(
            (f'{ride_label(ride)}\nSession: {session.path.name if session else "--"}\n'
             f'Platform(s): {self._platform_selection_text()}') if ride else
            'Choose a source session, ride and platform first.')
        self.abort_generation_btn.setVisible(process_running)
        self.abort_generation_btn.setEnabled(process_running)
        self.generation_back_btn.setEnabled(not process_running)
        self.retry_generation_btn.setVisible(self._generation_failed and not process_running)
        self.retry_generation_btn.setEnabled(self._generation_failed and not process_running and source_ready)
        self.generation_previous_btn.setVisible(self._generation_failed and has_result and not process_running)
        self.generation_previous_btn.setEnabled(self._generation_failed and has_result and not process_running)
        if process_running:
            self.generation_title.setText('Generating dynamic profiles...')
        elif self._generation_failed:
            self.generation_title.setText('Generation did not complete')
        elif has_result:
            self.generation_title.setText('Generation complete')
        else:
            self.generation_title.setText('Generate dynamic profiles')

        self._refresh_review_panels()

        if process_running or self._generation_failed:
            self.pages.setCurrentIndex(1)
            self._set_progress('generate')
        elif has_result and self._candidate_choices and not self._force_source_page:
            self.pages.setCurrentIndex(2)
            self._set_progress('review')
        else:
            self.pages.setCurrentIndex(0)
            self._set_progress('source')

    def advance_workflow(self):
        candidate = self.current_candidate()
        rel = self._choice_relative(candidate) if candidate else ''
        if rel and rel in self._dismissed:
            self.dismiss_profile()
            return
        stage = self._selected_stage()
        if stage == 'source':
            self.capture_session()
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
        """Compatibility entry point: export the currently selected platform profile."""
        choice = self._current_choice()
        if choice is None:
            return
        self._export_profile_for_platform(choice.platform)

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
