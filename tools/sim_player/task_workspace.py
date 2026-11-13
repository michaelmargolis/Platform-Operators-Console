"""Task-oriented Qt UI for existing Sim Player replay and analysis results.

Does not implement kinematics, physics, timing or motion filtering. All tunable
values are passed to the current app/controller to re-run the shared POC
analysis. The original advanced docks remain available from View > Panels.
"""
from __future__ import annotations
from collections import OrderedDict

from PyQt6.QtCore import Qt, QRectF, QPointF, pyqtSignal, QTimer
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QComboBox, QToolButton, QFrame, QScrollArea, QStackedWidget, QTabWidget,
    QSlider, QDoubleSpinBox, QCheckBox, QLineEdit, QListWidget, QListWidgetItem,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QMenu, QSizePolicy
)
from .task_workspace_models import AXES, AXIS_COLORS, read_washout, plot_series, plot_unit, plot_guides, plot_styles
from .tuning import tuning_metrics, muscle_flags
from .angular_cues import MODES, MODE_LABELS
from .panels import ResponsiveBars
from .panel_state import candidate_label, comparison_pair, event_description
from .platform_selection import platform_metadata

PLOT_CHOICES = (
    ('all_axes', '6-DOF motion'),
    ('profile_gains', 'Dynamic gain multipliers'),
    ('source_vs_processed_surge', 'Surge: source vs processed'),
    ('source_vs_processed_sway', 'Sway: source vs processed'),
    ('all_muscles', 'Muscle lengths (all)'),
    ('surge', 'Surge'), ('sway', 'Sway'), ('heave', 'Heave'),
    ('roll', 'Roll'), ('pitch', 'Pitch'), ('yaw', 'Yaw'),
    *((f'm{i+1}', f'Muscle M{i+1}') for i in range(6)),
)


def plot_choices():
    return list(PLOT_CHOICES)


class PlotCanvas(QWidget):
    """One zoom-free, selectable chart, drawn over video or full width.

    Data is cached at recording/plot changes; repaint only moves playhead.
    Input values retain their source units and are never smoothed or clipped.
    """
    seek_requested = pyqtSignal(float)

    def __init__(self, *, overlay=False, parent=None):
        super().__init__(parent)
        self.overlay_mode = bool(overlay)
        self.recording = None
        self.comparison = None
        self._comparison_series = ()
        self.plot_key = 'all_axes'
        self.current_s = 0.0
        self._series_cache = OrderedDict()
        self._series = ()
        self._bounds = (-1., 1.)
        self.setMinimumHeight(160 if overlay else 220)
        if self.overlay_mode:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        else:
            self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

    def set_recording(self, recording):
        if recording is self.recording:
            return
        self.recording = recording
        self._series_cache.clear()
        self._recompute()

    def set_comparison(self, recording):
        if recording is self.comparison:
            return
        self.comparison = recording
        self._recompute()

    def set_plot_key(self, key):
        if key == self.plot_key:
            return
        self.plot_key = str(key)
        self._recompute()

    def set_current_time(self, seconds):
        current = max(0., float(seconds))
        if abs(current - self.current_s) >= 0.001:
            self.current_s = current
            self.update()

    def _recompute(self):
        self._series = plot_series(self.recording, self.plot_key)
        # Source-vs-processed already uses dashed/solid for its two stages.
        # A/B overlay is reserved for the other plot families to avoid ambiguity.
        self._comparison_series = (() if self.plot_key.startswith('source_vs') else
                                   plot_series(self.comparison, self.plot_key))
        if not self._series:
            self._bounds = (-1., 1.)
        else:
            values_all = self._series + self._comparison_series
            minimum = min(min(values) for _, values in values_all if values)
            maximum = max(max(values) for _, values in values_all if values)
            guides = plot_guides(self.recording, self.plot_key)
            if guides:
                minimum = min(minimum, *(v for v, _ in guides))
                maximum = max(maximum, *(v for v, _ in guides))
            if maximum <= minimum:
                minimum -= 1.
                maximum += 1.
            pad = max((maximum - minimum) * .09, .0001)
            self._bounds = (minimum-pad, maximum+pad)
        self.update()

    def _legend_layout(self):
        """Wrap keys instead of clipping long source-stage descriptions."""
        metrics = self.fontMetrics()
        available = max(120., self.width()-86.)
        x = 0.; row = 0; entries = []
        for name, color, dashed in plot_styles(self.plot_key, self._series):
            label = metrics.elidedText(name, Qt.TextElideMode.ElideRight, max(40, int(available-35)))
            width = min(available, metrics.horizontalAdvance(label)+43.)
            if x and x+width > available:
                row += 1; x = 0.
            entries.append((x, row, label, color, dashed))
            x += width
        return entries, (row+1)*19.

    def _graph_rect(self):
        w, h = self.width(), self.height()
        _, legend_height = self._legend_layout()
        if self.overlay_mode:
            height = max(155., min(h * .37, 265.))
            top = max(legend_height+8., h-height+legend_height)
            return QRectF(60., top, max(12., w-78.), max(12., h-top-32.))
        top = 15.+legend_height
        return QRectF(65., top, max(12., w-86.), max(12., h-top-34.))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        graph = self._graph_rect()
        legend, legend_height = self._legend_layout()
        if self.overlay_mode:
            painter.fillRect(graph.adjusted(-52., -legend_height-7., 16., 26.), QColor(25,34,46,181))
        else:
            painter.fillRect(self.rect(), QColor('#0f1c2b'))
            painter.fillRect(graph.adjusted(-52., -legend_height-7., 16., 26.), QColor('#152536'))
        painter.setPen(QPen(QColor('#466071'), .8))
        for n in range(6):
            y = graph.top() + n * graph.height()/5.
            painter.drawLine(QPointF(graph.left(), y), QPointF(graph.right(), y))
        for n in range(9):
            x = graph.left() + n * graph.width()/8.
            painter.drawLine(QPointF(x, graph.top()), QPointF(x, graph.bottom()))

        if self.recording is None or not self._series:
            painter.setPen(QColor('#bdcddb'))
            painter.drawText(graph, Qt.AlignmentFlag.AlignCenter, 'No data for this plot - choose a compatible recording or plot')
            return

        minimum, maximum = self._bounds
        axis_range = max(maximum-minimum, 1.e-9)
        times = self.recording.times
        t0 = times[0]
        duration = max(times[-1]-t0, 1.e-9)
        for other, series, source in ((True, self._comparison_series, self.comparison),
                                      (False, self._series, self.recording)):
            if source is None:
                continue
            source_times = source.times
            source_stride = max(1, len(source_times)//max(1, int(graph.width()*2.)))
            source_indices = list(range(0, len(source_times), source_stride))
            if source_indices and source_indices[-1] != len(source_times)-1:
                source_indices.append(len(source_times)-1)
            for channel, (name, values) in enumerate(series):
                line = QPainterPath()
                for n, i in enumerate(source_indices):
                    x = graph.left() + (source_times[i]-t0)/duration*graph.width()
                    y = graph.bottom() - (values[i]-minimum)/axis_range*graph.height()
                    if n == 0:
                        line.moveTo(x,y)
                    else:
                        line.lineTo(x,y)
                _label, color, source_dash = plot_styles(self.plot_key, series)[channel]
                pen = QPen(QColor(color), 1.3 if other else (1.9 if self.overlay_mode else 2.2))
                if other or source_dash:
                    pen.setStyle(Qt.PenStyle.DashLine)
                painter.setPen(pen)
                painter.drawPath(line)
        scale, unit = plot_unit(self.plot_key)
        for level, label in plot_guides(self.recording, self.plot_key):
            y = graph.bottom() - (level-minimum)/axis_range*graph.height()
            painter.setPen(QPen(QColor('#9cabb7'), .9, Qt.PenStyle.DotLine))
            painter.drawLine(QPointF(graph.left(), y), QPointF(graph.right(), y))
            painter.drawText(QRectF(graph.right()-90.,y-15.,88.,14.),
                             Qt.AlignmentFlag.AlignRight, f'{label} {level*scale:g}{unit}')

        cx = graph.left() + max(0., min(1., (self.current_s-t0)/duration))*graph.width()
        painter.setPen(QPen(QColor('#f1f4f5'),1.4))
        painter.drawLine(QPointF(cx, graph.top()), QPointF(cx, graph.bottom()))
        painter.setPen(QColor('#d9e5f0'))
        for x, row, name, color, dashed in legend:
            lx = graph.left()+x
            ly = graph.top()-legend_height-3.+row*19.
            pen = QPen(QColor(color), 2.)
            if dashed: pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawLine(QPointF(lx,ly+8.),QPointF(lx+22.,ly+8.))
            painter.setPen(QColor(color))
            painter.drawText(QRectF(lx+28.,ly,graph.width()-x-28.,18.),
                             Qt.AlignmentFlag.AlignLeft, name)
        painter.setPen(QColor('#d9e5f0'))
        painter.drawText(QRectF(3.,graph.top()-18.,52.,16.),Qt.AlignmentFlag.AlignRight,unit)
        painter.setPen(QColor('#c2d0dc'))
        painter.drawText(QRectF(3., graph.top(), 55., 16.), Qt.AlignmentFlag.AlignRight, f'{maximum*scale:.1f}')
        painter.drawText(QRectF(3., graph.bottom()-15., 55., 16.), Qt.AlignmentFlag.AlignRight, f'{minimum*scale:.1f}')
        painter.drawText(QRectF(graph.left(),graph.bottom()+4.,graph.width(),20.),
                         Qt.AlignmentFlag.AlignLeft, f'{t0:.2f}s | Playhead {self.current_s:.2f}s' + (' | solid: active, dashed: other' if self._comparison_series else ''))
        painter.drawText(QRectF(graph.left(),graph.bottom()+4.,graph.width(),20.),
                         Qt.AlignmentFlag.AlignRight, f'{self.recording.duration:.1f} s')

    def mousePressEvent(self, event):
        if (self.recording is not None and
                event.button() == Qt.MouseButton.LeftButton):
            graph = self._graph_rect()
            if graph.contains(event.position()) and self.recording.times:
                fraction = max(0., min(1., (event.position().x()-graph.left())/graph.width()))
                seconds = self.recording.times[0] + fraction * (
                    self.recording.times[-1]-self.recording.times[0])
                self.seek_requested.emit(seconds)
                event.accept()
                return
        super().mousePressEvent(event)


class Section(QFrame):
    """Collapsible inspector panel with its title attached to the frame."""
    def __init__(self, title, *, open=True, parent=None):
        super().__init__(parent)
        self.setObjectName('inspectorPanel')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.toggle = QToolButton()
        self.toggle.setText(title)
        self.toggle.setObjectName('inspectorSection')
        self.toggle.setCheckable(True)
        self.toggle.setChecked(open)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if open else Qt.ArrowType.RightArrow)
        layout.addWidget(self.toggle)
        self.body = QWidget()
        self.body.setObjectName('inspectorCardBody')
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(9, 9, 9, 9)
        self.body_layout.setSpacing(6)
        layout.addWidget(self.body)
        self.body.setVisible(open)
        self.toggle.toggled.connect(self._toggle)

    def _toggle(self, checked):
        self.body.setVisible(checked)
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)


class CompactContextSection(QFrame):
    """One-line ride/candidate summary, visually attached to its frame."""
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setObjectName('inspectorPanel')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(7)
        self.toggle = QToolButton()
        self.toggle.setText(title)
        self.toggle.setObjectName('inspectorSection')
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setCheckable(False)
        self.toggle.setArrowType(Qt.ArrowType.NoArrow)
        header.addWidget(self.toggle)

        self.summary = QLabel('No capture loaded')
        self.summary.setObjectName('inspectorContextSummary')
        self.summary.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.summary.setMinimumWidth(0)
        self.summary.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        header.addWidget(self.summary, 1)
        layout.addLayout(header)

        self.body = QWidget()
        self.body.setObjectName('inspectorCardBody')
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(9, 7, 9, 7)
        self.body_layout.setSpacing(4)
        self.detail = QLabel('')
        self.detail.setWordWrap(True)
        self.detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.body_layout.addWidget(self.detail)
        layout.addWidget(self.body)
        self.body.hide()
        self.toggle.clicked.connect(self._toggle)

    def set_context(self, summary, detail='', *, expandable=False):
        self.summary.setText(summary)
        self.summary.setToolTip(summary)
        self.detail.setText(detail)
        self.toggle.blockSignals(True)
        if expandable:
            self.toggle.setCheckable(True)
            self.toggle.setChecked(False)
            self.toggle.setArrowType(Qt.ArrowType.RightArrow)
        else:
            self.toggle.setChecked(False)
            self.toggle.setCheckable(False)
            self.toggle.setArrowType(Qt.ArrowType.NoArrow)
        self.toggle.blockSignals(False)
        self.body.hide()

    def _toggle(self, checked=False):
        if not self.toggle.isCheckable():
            return
        checked = self.toggle.isChecked()
        self.body.setVisible(checked)
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)


class TaskInspector(QScrollArea):
    """Single context-sensitive inspector for Review, Tune, Validate.

    Readings are real captured data. The inspector emits user actions to the
    controller rather than running physics, filters or any hardware I/O.
    """
    seek_requested = pyqtSignal(float)
    gain_commit_requested = pyqtSignal(object)
    neutral_commit_requested = pyqtSignal(float)
    cue_commit_requested = pyqtSignal(str)
    washout_commit_requested = pyqtSignal(object)
    open_advanced_requested = pyqtSignal(str)
    save_notes_requested = pyqtSignal()
    examine_requested = pyqtSignal(object)
    baseline_requested = pyqtSignal()
    restore_baseline_requested = pyqtSignal()
    save_experiment_requested = pyqtSignal()
    load_experiment_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.recording = None
        self.workflow = 'review'
        self.current_s = 0.
        self.anomalies = ()
        self._notes = []
        self._session_key = None
        self._syncing = False
        self._busy = False
        self._active_slot = 'A'
        self._last_sent_gains = (100.,) * 6
        self._last_sent_neutral = 0.
        self._gain_debounce = QTimer(self)
        self._gain_debounce.setSingleShot(True)
        self._gain_debounce.setInterval(450)
        self._gain_debounce.timeout.connect(self._commit_gains)
        body = QFrame()
        body.setObjectName('inspectorSurface')
        layout = QVBoxLayout(body)
        layout.setContentsMargins(9,9,9,9)
        layout.setSpacing(8)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_section_menu)
        self.pages = QStackedWidget()
        self.review_page = self._make_review_page()
        self.tune_page = self._make_tune_page()
        self.validate_page = self._make_validate_page()
        for page in (self.review_page, self.tune_page, self.validate_page):
            self.pages.addWidget(page)
        layout.addWidget(self.pages, 1)
        self.setWidget(body)
        self.setMinimumWidth(330)

    def _page(self):
        page = QWidget()
        vertical = QVBoxLayout(page)
        vertical.setContentsMargins(0,0,0,0)
        vertical.setSpacing(6)
        return page, vertical

    def _make_review_page(self):
        page, layout = self._page()
        self.context_section = CompactContextSection('Ride and candidate')
        layout.addWidget(self.context_section)

        self.motion_section = Section('Current motion',open=True)
        self.motion_tabs = QTabWidget()
        self.axis_readout = ResponsiveBars(True)
        self.motion_tabs.addTab(self.axis_readout, 'Bars')
        self.motion_section.body_layout.addWidget(self.motion_tabs)
        self.motion_table = QTableWidget(6,4)
        self.motion_table.setHorizontalHeaderLabels(['DOF','Input','Gain','Output'])
        self._setup_table(self.motion_table,195)
        for row, name in enumerate(AXES):
            for col, value in enumerate((name,'—','—','—')):
                self.motion_table.setItem(row,col,QTableWidgetItem(value))
        self.motion_tabs.addTab(self.motion_table, 'Values')
        self.motion_note = QLabel('Cue input and processed output include the configured filtering; '
                                 'the relative gain alone does not describe that transformation.')
        self.motion_note.setWordWrap(True)
        self.motion_section.body_layout.addWidget(self.motion_note)
        layout.addWidget(self.motion_section)

        self.muscle_section = Section('Muscle lengths', open=True)
        # Use exactly the pre-refactor renderer, not a second progress-bar implementation.
        self.muscle_readout = ResponsiveBars(False)
        self.muscle_readout.setMinimumHeight(225)
        self.muscle_section.body_layout.addWidget(self.muscle_readout)
        self.muscle_hint=QLabel('Bars = permitted lengths. Blue/gold lines = minimum/maximum '
            'requests; red lines = out-of-range extrema. Hover for exact values. '
            'Grey guides show 50 mm intervals and the valid limits.')
        self.muscle_hint.setWordWrap(True)
        self.muscle_section.body_layout.addWidget(self.muscle_hint)
        layout.addWidget(self.muscle_section)

        self.event_section = Section('Events and limits',open=False)
        self.event_summary = QLabel('No events')
        self.event_summary.setWordWrap(True)
        self.event_section.body_layout.addWidget(self.event_summary)
        self.event_list = QListWidget()
        self.event_list.setMinimumHeight(105)
        self.event_list.setMaximumHeight(180)
        self.event_list.itemDoubleClicked.connect(self._event_activated)
        self.event_list.currentItemChanged.connect(self._event_selected)
        self.event_section.body_layout.addWidget(self.event_list)
        self.event_detail = QLabel('Select an event to see its details.')
        self.event_detail.setWordWrap(True)
        self.event_detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.event_section.body_layout.addWidget(self.event_detail)
        self.examine_button = QPushButton('Examine selected anomaly (loop)')
        self.examine_button.clicked.connect(self._examine_selected)
        self.examine_button.setEnabled(False)
        self.event_section.body_layout.addWidget(self.examine_button)
        self.event_section.body_layout.addWidget(QLabel('Double-click to seek; Examine uses the existing A/B loop.'))
        layout.addWidget(self.event_section)

        self.compare_section = Section('Candidate A/B',open=False)
        self.compare_label = QLabel('Open two candidates to compare their motion at the current position.')
        self.compare_label.setWordWrap(True)
        self.compare_section.body_layout.addWidget(self.compare_label)
        self.compare_table = QTableWidget(6,3)
        self.compare_table.setHorizontalHeaderLabels(['DOF', 'A', 'B'])
        self._setup_table(self.compare_table,184)
        for i,name in enumerate(AXES):
            for j,value in enumerate((name,'—','—')):
                self.compare_table.setItem(i,j,QTableWidgetItem(value))
        self.compare_section.body_layout.addWidget(self.compare_table)
        layout.addWidget(self.compare_section)

        self.notes_section = Section('Trial notes',open=False)
        row = QHBoxLayout()
        self.note_edit = QLineEdit()
        self.note_edit.setPlaceholderText('Observation at playhead…')
        row.addWidget(self.note_edit,1)
        add = QPushButton('Add')
        add.clicked.connect(self._add_note)
        row.addWidget(add)
        self.notes_section.body_layout.addLayout(row)
        self.note_list = QListWidget()
        self.note_list.setMinimumHeight(88)
        self.note_list.itemDoubleClicked.connect(self._note_activated)
        self.notes_section.body_layout.addWidget(self.note_list)
        export = QPushButton('Save notes as JSON…')
        export.clicked.connect(self.save_notes_requested)
        self.notes_section.body_layout.addWidget(export)
        layout.addWidget(self.notes_section)
        layout.addStretch(1)
        return page

    def _make_tune_page(self):
        page, layout = self._page()
        notice = QLabel('Experimental offline settings. Changes reanalyse the original capture using the POC processing code. Not validated for physical use.')
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.gains_section = Section('Motion gains',open=True)
        grid=QGridLayout()
        self.gain_sliders=[]
        self.gain_labels=[]
        for i,name in enumerate(AXES):
            grid.addWidget(QLabel(name),i,0)
            slider=QSlider(Qt.Orientation.Horizontal)
            slider.setRange(0,200)
            slider.setSingleStep(5)
            slider.setPageStep(10)
            slider.setValue(100)
            slider.sliderReleased.connect(self._commit_gains)
            value=QLabel('100%')
            value.setMinimumWidth(48)
            value.setAlignment(Qt.AlignmentFlag.AlignRight)
            slider.valueChanged.connect(lambda v, j=i: self._gain_value_changed(j, v))
            self.gain_sliders.append(slider)
            self.gain_labels.append(value)
            grid.addWidget(slider,i,1)
            grid.addWidget(value,i,2)
        self.gains_section.body_layout.addLayout(grid)
        gain_row=QHBoxLayout()
        reset=QPushButton('Reset to 100%')
        reset.clicked.connect(self._reset_gains)
        self.reset_gain_button = reset
        gain_row.addWidget(reset)
        advanced=QPushButton('Advanced gains…')
        advanced.clicked.connect(lambda: self.open_advanced_requested.emit('tuning'))
        gain_row.addWidget(advanced)
        self.gains_section.body_layout.addLayout(gain_row)
        baseline_row = QHBoxLayout()
        self.baseline_button = QPushButton('Set baseline')
        self.baseline_button.clicked.connect(self.baseline_requested)
        baseline_row.addWidget(self.baseline_button)
        self.restore_button = QPushButton('Restore baseline')
        self.restore_button.clicked.connect(self.restore_baseline_requested)
        baseline_row.addWidget(self.restore_button)
        self.gains_section.body_layout.addLayout(baseline_row)
        self.baseline_label = QLabel('Baseline: not set')
        self.baseline_label.setWordWrap(True)
        self.gains_section.body_layout.addWidget(self.baseline_label)
        persistence = QHBoxLayout()
        self.save_experiment_button = QPushButton('Save experiment...')
        self.save_experiment_button.clicked.connect(self.save_experiment_requested)
        self.load_experiment_button = QPushButton('Load experiment...')
        self.load_experiment_button.clicked.connect(self.load_experiment_requested)
        persistence.addWidget(self.save_experiment_button)
        persistence.addWidget(self.load_experiment_button)
        self.gains_section.body_layout.addLayout(persistence)
        layout.addWidget(self.gains_section)

        self.neutral_section = Section('Neutral and angular cue',open=False)
        self.neutral_label=QLabel('Ride neutral offset')
        self.neutral_section.body_layout.addWidget(self.neutral_label)
        self.neutral_box = QDoubleSpinBox()
        self.neutral_box.setRange(-100,100)
        self.neutral_box.setSingleStep(1.)
        self.neutral_box.setDecimals(1)
        self.neutral_box.setSuffix(' mm')
        self.neutral_box.editingFinished.connect(self._commit_neutral)
        self.neutral_box.valueChanged.connect(self._neutral_numeric_changed)
        self.neutral_section.body_layout.addWidget(self.neutral_box)
        self.neutral_reset_button = QPushButton('Reset neutral offset')
        self.neutral_reset_button.clicked.connect(self._reset_neutral)
        self.neutral_section.body_layout.addWidget(self.neutral_reset_button)
        self.neutral_summary = QLabel('Platform neutral: unavailable')
        self.neutral_summary.setWordWrap(True)
        self.neutral_section.body_layout.addWidget(self.neutral_summary)
        cue_row=QHBoxLayout()
        cue_row.addWidget(QLabel('Angular cue'))
        self.cue_box=QComboBox()
        for mode in MODES:
            self.cue_box.addItem(MODE_LABELS[mode],mode)
        self.cue_box.currentIndexChanged.connect(self._cue_changed)
        cue_row.addWidget(self.cue_box,1)
        self.neutral_section.body_layout.addLayout(cue_row)
        layout.addWidget(self.neutral_section)

        self.washout_section = Section('Translational washout',open=False)
        self.washout_summary = QLabel('Load a recording to inspect washout configuration.')
        self.washout_summary.setWordWrap(True)
        self.washout_section.body_layout.addWidget(self.washout_summary)
        self.washout_controls = QWidget()
        wgrid=QGridLayout(self.washout_controls)
        wgrid.setContentsMargins(0,0,0,0)
        wgrid.addWidget(QLabel('Axis'),0,0)
        wgrid.addWidget(QLabel('On'),0,1)
        wgrid.addWidget(QLabel('Tau (seconds)'),0,2)
        self.washout_enabled=[]
        self.washout_tau=[]
        for i,name in enumerate(('Surge','Sway')):
            wgrid.addWidget(QLabel(name),i+1,0)
            enabled=QCheckBox()
            enabled.setChecked(True)
            wgrid.addWidget(enabled,i+1,1)
            tau=QDoubleSpinBox()
            tau.setRange(.01,60.)
            tau.setDecimals(2)
            tau.setSingleStep(.2)
            tau.setSuffix(' s')
            tau.setValue(1.2)
            wgrid.addWidget(tau,i+1,2)
            self.washout_enabled.append(enabled)
            self.washout_tau.append(tau)
        self.washout_section.body_layout.addWidget(self.washout_controls)
        apply_btn=QPushButton('Reanalyse with these settings')
        apply_btn.clicked.connect(self._commit_washout)
        self.washout_section.body_layout.addWidget(apply_btn)
        self.washout_apply_button=apply_btn
        self.washout_warning=QLabel('Original capture required. Existing dynamic-profile qualification does not apply if washout changes.')
        self.washout_warning.setWordWrap(True)
        self.washout_section.body_layout.addWidget(self.washout_warning)
        layout.addWidget(self.washout_section)
        layout.addStretch(1)
        return page

    def _make_validate_page(self):
        page, layout = self._page()
        self.validation_section=Section('Kinematic feasibility',open=True)
        self.validation_label=QLabel('Open a candidate to see calculated limits.')
        self.validation_label.setWordWrap(True)
        self.validation_section.body_layout.addWidget(self.validation_label)
        largest=QPushButton('Inspect largest muscle step')
        largest.clicked.connect(self._seek_largest_step)
        self.validation_section.body_layout.addWidget(largest)
        layout.addWidget(self.validation_section)
        self.sync_section=Section('Synchronisation and provenance',open=True)
        self.sync_label=QLabel('No capture selected')
        self.sync_label.setWordWrap(True)
        self.sync_section.body_layout.addWidget(self.sync_label)
        self.reference_video_label = QLabel()
        self.reference_video_label.setWordWrap(True)
        self.reference_video_label.hide()
        self.sync_section.body_layout.addWidget(self.reference_video_label)
        self.dynamic_status = QLabel('')
        self.dynamic_status.setWordWrap(True)
        self.sync_section.body_layout.addWidget(self.dynamic_status)
        self.dynamic_table = QTableWidget(6, 3)
        self.dynamic_table.setHorizontalHeaderLabels(['DOF', 'Applied', 'Profile curve'])
        self._setup_table(self.dynamic_table, 184)
        for i, name in enumerate(AXES):
            for j, value in enumerate((name, '-', '-')):
                self.dynamic_table.setItem(i, j, QTableWidgetItem(value))
        self.sync_section.body_layout.addWidget(self.dynamic_table)
        self.dynamic_table.hide()
        layout.addWidget(self.sync_section)
        adv=QPushButton('Open full diagnostics…')
        adv.clicked.connect(lambda: self.open_advanced_requested.emit('anomalies'))
        layout.addWidget(adv)
        layout.addStretch(1)
        return page

    @staticmethod
    def _setup_table(table,height):
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.setShowGrid(False)
        table.verticalHeader().hide()
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.setMaximumHeight(height)
        table.setMinimumHeight(height-8)

    def _set_expanded(self, open):
        page = self.pages.currentWidget()
        for section in page.findChildren(Section):
            section.toggle.setChecked(open)
        for section in page.findChildren(CompactContextSection):
            if section.toggle.isCheckable():
                section.toggle.setChecked(open)
                section._toggle(open)

    def _show_section_menu(self, pos):
        menu = QMenu(self)
        menu.addAction('Expand all sections', lambda: self._set_expanded(True))
        menu.addAction('Collapse all sections', lambda: self._set_expanded(False))
        menu.exec(self.mapToGlobal(pos))

    def set_workspace(self, name):
        mapping={'review':(0,'Review'), 'tune':(1,'Tune'), 'validate':(2,'Validate')}
        index,label=mapping.get(name,mapping['review'])
        self.workflow=name if name in mapping else 'review'
        self.pages.setCurrentIndex(index)
        self._update_readonly()

    def _update_readonly(self):
        dynamic = self.recording is not None and self.recording.dynamic_profile is not None
        busy = self.recording is None or dynamic or self._busy
        for slider in self.gain_sliders:
            slider.setEnabled(not busy)
        named = bool(getattr(self.recording, 'variant_snapshot', None))
        self.neutral_box.setEnabled(not busy and named)
        self.neutral_reset_button.setEnabled(not busy and named)
        for control in (self.reset_gain_button, self.baseline_button, self.restore_button,
                        self.save_experiment_button, self.load_experiment_button):
            control.setEnabled(not busy)
        self.cue_box.setEnabled(not busy)
        washout = read_washout(getattr(self.recording,'global_settings_snapshot',None))
        editable = not busy and washout['kind'] == 'classical'
        self.washout_controls.setEnabled(editable)
        self.washout_apply_button.setEnabled(editable)
        if dynamic:
            self.washout_warning.setText('Dynamic-profile review is read-only. New washout values require a raw-capture reconstruction and new qualification.')
        elif washout['kind'] != 'classical':
            self.washout_warning.setText('Only classical washout time constants are editable here. Other filter types remain available in the POC configuration.')
        else:
            self.washout_warning.setText('Offline experiment only. Reprocesses the entire raw capture; does not update POC or qualify an existing profile.')

    def set_recording(self, recording, anomalies=(), *, comparison=None, active_slot='A'):
        self._active_slot = active_slot
        changed = recording is not self.recording
        session_key = (str(recording.csv_path), recording.platform_key) if recording is not None else None
        if session_key != self._session_key:
            self._notes.clear()
            self.note_list.clear()
            self._session_key = session_key
        self.recording = recording
        self.anomalies = tuple(anomalies)
        if changed:
            self.clear_readouts()
            self._refresh_context()
            self._refresh_tuning()
            self._refresh_validation()
        self._refresh_events()
        self._set_comparison(comparison)
        self._update_readonly()
        self.set_current_time(self.current_s, comparison=comparison)

    def clear_readouts(self):
        self.axis_readout.update_values((0.,) * 6)
        self.muscle_readout.clear_extrema()
        self.muscle_readout.update_values((0.,) * 6)
        for table in (self.motion_table, self.compare_table, self.dynamic_table):
            for row in range(6):
                for col in range(1, table.columnCount()):
                    table.item(row, col).setText('-')
        self.dynamic_status.clear()
        self.dynamic_table.hide()
        self.axis_readout.setEnabled(self.recording is not None)
        self.muscle_readout.setEnabled(self.recording is not None)

    def set_muscle_state(self, state):
        state.apply(self.muscle_readout)

    def set_transform_state(self, context, values):
        self.axis_readout.axis_context = context
        self.axis_readout.update_values(values)

    def set_feedback(self, text):
        # The original detailed metrics are prepared by the same controller.
        self.validation_label.setText(text + '\nOffline results are not actuator/comfort validation.')

    def set_baseline_text(self, text):
        self.baseline_label.setText(text)

    def set_busy(self, busy):
        was_busy = self._busy
        self._busy = bool(busy)
        if self._busy:
            self._gain_debounce.stop()
        elif was_busy:
            # Restore accepted values after a failed/cancelled worker as well.
            self._refresh_tuning()
        self._update_readonly()

    def _refresh_context(self):
        rec=self.recording
        if rec is None:
            self.context_section.set_context('No capture loaded', expandable=False)
            self.validation_label.setText('No candidate loaded')
            self.sync_label.setText('No capture selected')
            return
        wash=read_washout(rec.global_settings_snapshot)
        if wash['kind']=='classical':
            summary=(f"Surge {'ON' if wash['surge_enabled'] else 'OFF'}, τ={wash['surge_tau']:g} s; "
                     f"Sway {'ON' if wash['sway_enabled'] else 'OFF'}, τ={wash['sway_tau']:g} s")
        else:
            summary=f"Washout: {wash['kind']}"
        dynamic=rec.dynamic_profile
        label=dynamic.get('label','Dynamic') if isinstance(dynamic,dict) else rec.variant_label or 'Static baseline'
        compact = f'{rec.ride} · {label}'
        platform_name = platform_metadata(rec.platform_key)['platform_name']
        detail = f'Platform: {platform_name}\nCandidate: {label}\n{summary}'
        self.context_section.set_context(compact, detail, expandable=True)

    def _refresh_tuning(self):
        rec=self.recording
        self._syncing=True
        self._gain_debounce.stop()
        try:
            gains=rec.gain_percent if rec is not None else (100,)*6
            for slider,label,gain in zip(self.gain_sliders,self.gain_labels,gains):
                slider.setValue(round(gain))
                label.setText(f'{gain:g}%')
            offset=float((rec.variant_snapshot or {}).get('neutral_height_delta_mm',0.)) if rec else 0.
            self._last_sent_gains = tuple(float(g) for g in gains)
            self._last_sent_neutral = offset
            self.neutral_box.setValue(offset)
            self._update_neutral_summary()
            if rec:
                index=self.cue_box.findData(rec.cue_mode)
                if index>=0:
                    self.cue_box.setCurrentIndex(index)
            wash=read_washout(rec.global_settings_snapshot if rec else None)
            self.washout_summary.setText('Classical washout · settings are inherited from this recording' if wash['kind']=='classical'
                                         else f"Washout type: {wash['kind']}")
            for i,axis in enumerate(('surge','sway')):
                self.washout_enabled[i].setChecked(wash[axis+'_enabled'])
                self.washout_tau[i].setValue(wash[axis+'_tau'] or 1.2)
        finally:
            self._syncing=False

    def _refresh_events(self):
        rec=self.recording
        selected = self.event_list.currentItem()
        previous = selected.data(Qt.ItemDataRole.UserRole) if selected else None
        self.event_list.clear()
        self.event_summary.setText(f'{len(self.anomalies)} kinematic anomaly intervals'
                                   + (f' | {len(rec.events)} recorded ride events' if rec else ''))
        self.event_detail.setText('Select an event to see its details.')
        self.examine_button.setEnabled(False)
        if rec is None:
            return
        events=[(e.seconds, 'Ride', e) for e in rec.events]
        events.extend((e.seconds, 'Limit', e) for e in self.anomalies)
        for seconds, kind, event in sorted(events, key=lambda e:e[0]):
            item=QListWidgetItem(f'{seconds:6.2f}s   {kind}: {event.name}')
            item.setData(Qt.ItemDataRole.UserRole, event)
            item.setToolTip(event_description(event))
            self.event_list.addItem(item)
            if event is previous:
                self.event_list.setCurrentItem(item)

    def _refresh_validation(self):
        rec=self.recording
        if rec is None:
            return
        metrics=tuning_metrics(rec)
        self.validation_label.setText(
            f'Request beyond working range: {metrics.requested_oob_frames} samples\n'
            f'Worst requested excess: {metrics.worst_requested_excess_mm:.2f} mm\n'
            f'Workspace limited: {metrics.workspace_frames} samples\n'
            f'Final output clipped: {metrics.final_clipped_frames} samples\n'
            f'Max output step: {metrics.peak_step_mm:.2f} mm/sample\n'
            'Offline kinematic results are not actuator/comfort validation.'
        )
        dynamic=rec.dynamic_profile
        source_diff=dynamic.get('processing_source_mismatches',()) if isinstance(dynamic,dict) else ()
        self.sync_label.setText(
            f'Capture-linked video: {rec.video_path.name if rec.video_path else "none"}\n'
            f'Sync: {rec.sync_source}\n'
            f'Profile processing: {"source difference (review only)" if source_diff else "see capture provenance"}\n'
            f'Duration: {rec.duration:.2f} s'
        )

    def _set_comparison(self, comparison):
        if comparison is None or self.recording is None:
            self.compare_label.setText('Select an A/B review to inspect both candidates at the same playhead.')
            self.compare_table.setEnabled(False)
            for row in range(6):
                self.compare_table.item(row, 1).setText('-')
                self.compare_table.item(row, 2).setText('-')
        else:
            a, b = comparison_pair(self.recording, comparison, self._active_slot)
            self.compare_label.setText(f'A: {candidate_label(a)}\nB: {candidate_label(b)}'
                                       f'\nActive: {self._active_slot}')
            self.compare_table.setEnabled(True)
        self._comparison=comparison

    def set_current_time(self, seconds, *,comparison=None):
        self.current_s=float(seconds)
        rec=self.recording
        if rec is None:
            return
        frame=rec.at(self.current_s)
        multipliers=(frame.analysis.get('dynamic') or {}).get('multipliers')
        for i in range(6):
            original=float(frame.input_axes[i])
            post=float(frame.processed_axes[i])
            gain=float(multipliers[i]) if multipliers is not None else float(rec.gain_percent[i])/100.
            items=self.motion_table
            items.item(i,1).setText(f'{original*100:+.1f}%')
            items.item(i,2).setText(f'{gain:.2f}×')
            items.item(i,3).setText(f'{post*100:+.1f}%')
        dynamic = frame.analysis.get('dynamic') if rec.dynamic_profile else None
        self.motion_table.setHorizontalHeaderLabels(
            ['DOF', 'Base filtered', 'Applied', 'Request'] if dynamic else
            ['DOF', 'Cue input', 'Rel. gain', 'Processed'])
        self.motion_note.setText(
            'Applied multiplier follows washout; the request may still be workspace limited.'
            if dynamic else 'Cue input is before relative gains, POC gains and washout; '
            'processed output includes those stages. All motion values are normalised percentages.')
        other=comparison if comparison is not None else getattr(self,'_comparison',None)
        if other is not None:
            a,b=comparison_pair(rec,other,self._active_slot)
            af=a.at(min(self.current_s,a.duration))
            bf=b.at(min(self.current_s,b.duration))
            for i in range(6):
                self.compare_table.item(i,1).setText(f'{float(af.processed_axes[i])*100:+.1f}%')
                self.compare_table.item(i,2).setText(f'{float(bf.processed_axes[i])*100:+.1f}%')
        self.dynamic_table.setVisible(bool(dynamic))
        if dynamic:
            lag=dynamic.get('profile_lag_s')
            residual=dynamic.get('position_residual_m')
            self.dynamic_status.setText(
                f'Ride {dynamic.get("ride_time_s", 0.):.2f}s | lookup {dynamic.get("phase_s", 0.):.2f}s\n'
                f'Sync: {dynamic.get("status", "unknown")}\n'
                + (f'Lookup lag: {lag*1000:+.1f} ms\n' if lag is not None else 'Lookup lag: not recorded\n')
                + (f'Position residual: {residual*1000:.1f} mm\n' if residual is not None else 'Position residual: not recorded\n')
                + ('Fallback/blend active' if dynamic.get('fallback_active') else 'Fallback inactive'))
            applied=dynamic.get('multipliers')
            curve=dynamic.get('profile_multipliers')
            for i in range(6):
                self.dynamic_table.item(i,1).setText(f'{applied[i]*100:.1f}%' if applied else '-')
                self.dynamic_table.item(i,2).setText(f'{curve[i]*100:.1f}%' if curve else '-')
        else:
            self.dynamic_status.clear()

    def _event_activated(self,item):
        event=item.data(Qt.ItemDataRole.UserRole)
        if event is not None:
            self.seek_requested.emit(float(event.seconds))

    def _event_selected(self, item, previous=None):
        event = item.data(Qt.ItemDataRole.UserRole) if item else None
        self.event_detail.setText(event_description(event))
        self.examine_button.setEnabled(event is not None and hasattr(event, 'start_s'))

    def _examine_selected(self):
        item = self.event_list.currentItem()
        event = item.data(Qt.ItemDataRole.UserRole) if item else None
        if event is not None and hasattr(event, 'start_s'):
            self.examine_requested.emit(event)

    def _note_activated(self,item):
        seconds=item.data(Qt.ItemDataRole.UserRole)
        if seconds is not None:
            self.seek_requested.emit(float(seconds))

    def _add_note(self):
        message=self.note_edit.text().strip()
        if not message or self.recording is None:
            return
        sec=self.current_s
        self._notes.append({'seconds':round(sec,3),'text':message})
        item=QListWidgetItem(f'{sec:.2f}s  {message}')
        item.setData(Qt.ItemDataRole.UserRole,sec)
        self.note_list.addItem(item)
        self.note_edit.clear()

    def notes(self):
        return tuple(dict(item) for item in self._notes)

    def _gain_value_changed(self, index, value):
        self.gain_labels[index].setText(f'{value}%')
        if self._syncing or self._busy:
            return
        if any(slider.isSliderDown() for slider in self.gain_sliders):
            self._gain_debounce.stop()
        else:
            # Keyboard/wheel/PageUp/PageDown do not emit sliderReleased.
            self._gain_debounce.start()

    def _commit_gains(self):
        self._gain_debounce.stop()
        if self._syncing or self._busy or self.recording is None or self.recording.dynamic_profile:
            return
        gains = tuple(float(v.value()) for v in self.gain_sliders)
        if gains != self._last_sent_gains:
            self._last_sent_gains = gains
            self.gain_commit_requested.emit(gains)

    def _reset_gains(self):
        if self._busy or self.recording is None or self.recording.dynamic_profile:
            return
        self._syncing=True
        for slider in self.gain_sliders:
            slider.setValue(100)
        self._syncing=False
        self._commit_gains()

    def _update_neutral_summary(self):
        neutral = getattr(self.recording, 'platform_neutral_height_mm', None)
        offset = self.neutral_box.value()
        self.neutral_summary.setText('Platform neutral: unavailable' if neutral is None else
            f'Platform neutral: {neutral:.1f} mm | adjustment: {offset:+g} mm | '
            f'effective: {neutral+offset:.1f} mm')

    def _neutral_numeric_changed(self, value):
        self._update_neutral_summary()

    def _commit_neutral(self):
        rec = self.recording
        if self._syncing or self._busy or rec is None or rec.dynamic_profile or not rec.variant_snapshot:
            return
        value = self.neutral_box.value()
        if value != self._last_sent_neutral:
            self._last_sent_neutral = value
            self.neutral_commit_requested.emit(value)

    def _reset_neutral(self):
        if not self.neutral_box.isEnabled():
            return
        self.neutral_box.setValue(0.)
        self._commit_neutral()

    def _cue_changed(self,*_args):
        if not self._syncing and not self._busy and self.recording is not None and not self.recording.dynamic_profile:
            mode=self.cue_box.currentData()
            if mode is not None:
                self.cue_commit_requested.emit(mode)

    def _commit_washout(self):
        rec=self.recording
        if self._busy or rec is None or rec.dynamic_profile:
            return
        values=dict(
            surge_enabled=self.washout_enabled[0].isChecked(),
            sway_enabled=self.washout_enabled[1].isChecked(),
            surge_tau=self.washout_tau[0].value(),
            sway_tau=self.washout_tau[1].value(),
        )
        self.washout_commit_requested.emit(values)

    def _seek_largest_step(self):
        rec=self.recording
        if rec is None or len(rec.frames)<2:
            return
        frames=rec.frames
        idx=max(range(1,len(frames)),key=lambda i:max(abs(a-b)
            for a,b in zip(frames[i].muscle_lengths_mm,frames[i-1].muscle_lengths_mm)))
        self.seek_requested.emit(float(frames[idx].seconds))
