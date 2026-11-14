# dynamic_browser.py ver 1
"""Select any dynamic profiles for a common park/platform/capture."""
from __future__ import annotations
from pathlib import Path
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,QLineEdit,
    QPushButton,QComboBox,QTableWidget,QTableWidgetItem,QHeaderView,QDialogButtonBox,
    QFileDialog,QMessageBox,QCheckBox,QAbstractItemView)
from sims.nolimits2 import ride_config
from .dynamic_review import ROOT, ReviewSpec, discover_profiles, DynamicProfile


class DynamicProfileBrowserDialog(QDialog):
    def __init__(self, directory, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Review dynamic motion profiles')
        self.resize(970,640)
        self.specs=None;self.choices=[];self.directory=Path(directory)
        layout=QVBoxLayout(self)
        help_label=QLabel('Select a platform and park, then any profile for A. Compare with another profile, '
                         'its static 100% baseline, its conservative fallback, or use A alone. Original files stay unchanged.')
        help_label.setWordWrap(True);layout.addWidget(help_label)
        row=QHBoxLayout();self.root_edit=QLineEdit(str(directory));row.addWidget(self.root_edit,1)
        browse=QPushButton('Results folder...');browse.clicked.connect(self.browse);row.addWidget(browse)
        refresh=QPushButton('Refresh');refresh.clicked.connect(self.refresh);row.addWidget(refresh);layout.addLayout(row)
        filters=QHBoxLayout();self.platform=QComboBox();self.park=QComboBox()
        filters.addWidget(QLabel('Platform'));filters.addWidget(self.platform,1)
        filters.addWidget(QLabel('Park'));filters.addWidget(self.park,1);layout.addLayout(filters)
        self.table=QTableWidget(0,6);self.table.setHorizontalHeaderLabels(['Candidate / run','Target','Cue','Neutral mm','Captures','Source assessment'])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch)
        for i in range(1,6):self.table.horizontalHeader().setSectionResizeMode(i,QHeaderView.ResizeMode.ResizeToContents)
        self.table.setToolTip('Every available candidate is listed; Strict and 5% are not separate modes.')
        layout.addWidget(self.table,1)
        self.a=QComboBox();self.b=QComboBox();self.capture=QComboBox()
        for combo in (self.a,self.b,self.capture):
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(30)
        form=QFormLayout();form.addRow('A',self.a);form.addRow('B',self.b);form.addRow('Common recording',self.capture);layout.addLayout(form)
        self.raw_check=QCheckBox('Recalculate from original timing session (optional; otherwise review saved study)')
        layout.addWidget(self.raw_check)
        rawrow=QHBoxLayout();self.raw_root=QLineEdit(str(ROOT/'captures'/'nl2_timing_study'))
        rawrow.addWidget(self.raw_root,1);self.raw_browse=QPushButton('Session folder...');rawrow.addWidget(self.raw_browse)
        self.raw_browse.clicked.connect(self.browse_raw);layout.addLayout(rawrow)
        self.raw_check.toggled.connect(lambda b:(self.raw_root.setEnabled(b),self.raw_browse.setEnabled(b)))
        self.raw_root.setEnabled(False);self.raw_browse.setEnabled(False)
        self.note=QLabel();self.note.setWordWrap(True);layout.addWidget(self.note)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Open|QDialogButtonBox.StandardButton.Cancel)
        self.open_button=buttons.button(QDialogButtonBox.StandardButton.Open);self.open_button.setText('Open for review')
        buttons.accepted.connect(self.open_selected);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
        self.platform.currentIndexChanged.connect(self.fill_parks);self.park.currentIndexChanged.connect(self.fill_choices)
        self.a.currentIndexChanged.connect(self.fill_captures);self.b.currentIndexChanged.connect(self.fill_captures)
        self.table.cellDoubleClicked.connect(lambda row,col:self.a.setCurrentIndex(row))
        self.refresh()

    def browse(self):
        path=QFileDialog.getExistingDirectory(self,'Dynamic profile results root',self.root_edit.text())
        if path:self.root_edit.setText(path);self.refresh()

    def browse_raw(self):
        path=QFileDialog.getExistingDirectory(self,'Timing session or parent folder',self.raw_root.text())
        if path:self.raw_root.setText(path)

    def refresh(self):
        try:
            self.directory=Path(self.root_edit.text()).expanduser().resolve()
            self.choices,errors=discover_profiles(self.directory)
        except (ValueError,OSError) as exc:
            self.choices=[];errors=[str(exc)]
        self.platform.blockSignals(True);self.platform.clear()
        for key in sorted({c.platform for c in self.choices}):
            self.platform.addItem({'chair_v3':'Chair V3','platform_v2':'Ground-Loading Platform'}.get(key,key),key)
        self.platform.blockSignals(False)
        self.note.setText(f'{len(self.choices)} profiles found.' + (' Invalid entries: '+str(len(errors)) if errors else ''))
        self.note.setToolTip('\n'.join(errors))
        self.fill_parks()

    def fill_parks(self,*args):
        self.park.blockSignals(True);self.park.clear()
        for park in sorted({c.park for c in self.choices
                            if c.platform==self.platform.currentData()
                            and c.park not in ride_config.ANALYZER_HIDDEN_PARKS}):
            self.park.addItem(park,park)
        self.park.blockSignals(False);self.fill_choices()

    def fill_choices(self,*args):
        choices=[c for c in self.choices if c.platform==self.platform.currentData() and c.park==self.park.currentData()]
        self.table.setRowCount(len(choices));self.a.blockSignals(True);self.b.blockSignals(True)
        self.a.clear();self.b.clear()
        self.b.addItem('Static 100% baseline of A (same cue/midpoint)',('unity',None))
        self.b.addItem('Static fallback of A (same cue/midpoint)',('fallback',None))
        self.b.addItem('No B - review A only',('none',None))
        for i,c in enumerate(choices):
            values=[c.name+' / '+c.run,c.target,c.cue,f'{c.neutral_mm:+g}',str(len(c.captures)),c.qualification]
            for j,text in enumerate(values):
                item=QTableWidgetItem(text);item.setToolTip(str(c.path));self.table.setItem(i,j,item)
            self.a.addItem(c.description+' / '+c.run,c)
            self.b.addItem(c.description+' / '+c.run,('profile',c))
        self.a.blockSignals(False);self.b.blockSignals(False);self.fill_captures()

    def fill_captures(self,*args):
        prior=self.capture.currentData();self.capture.clear()
        a=self.a.currentData();selection=self.b.currentData()
        if a is None or selection is None:
            self.open_button.setEnabled(False);return
        kind,b=selection;names=list(a.captures)
        if kind=='profile':names=[n for n in names if n in b.captures]
        for n in names:
            label=n+(' [reference]' if n==a.reference_trial else '')
            self.capture.addItem(label,n)
        index=self.capture.findData(prior)
        if index>=0:self.capture.setCurrentIndex(index)
        self.open_button.setEnabled(bool(names))

    def open_selected(self):
        a=self.a.currentData();kind,b=self.b.currentData();name=self.capture.currentData()
        if not a or not name:return
        raw=None
        try:
            if self.raw_check.isChecked():
                folder=Path(self.raw_root.text()).expanduser().resolve()
                if not folder.is_dir():raise ValueError('Extracted timing session folder not found: '+str(folder))
                matches=list(folder.rglob(name+'_trial.json'))
                if len(matches)!=1:
                    raise ValueError(f'Expected one {name}_trial.json, found {len(matches)}. Choose a narrower extracted session folder.')
                raw=matches[0]
            specs=[ReviewSpec(a.path,name,'dynamic',raw)]
            if kind in ('unity','fallback'):specs.append(ReviewSpec(a.path,name,kind,raw))
            elif kind=='profile':specs.append(ReviewSpec(b.path,name,'dynamic',raw))
            self.specs=tuple(specs);self.accept()
        except (ValueError,OSError) as exc:
            QMessageBox.warning(self,'Cannot open profile selection',str(exc))
