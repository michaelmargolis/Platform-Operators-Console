"""Qt6 read-only gain and progress displays; all calculations are cached."""
from __future__ import annotations
import math
import numpy as np
from PyQt6.QtCore import Qt,QRectF,QPointF,pyqtSignal
from PyQt6.QtGui import QColor,QPainter,QPen,QPainterPath
from PyQt6.QtWidgets import QWidget,QVBoxLayout,QLabel,QTableWidget,QTableWidgetItem,QHeaderView,QPushButton,QScrollArea,QAbstractItemView
from .tuning import tuning_metrics

AXES=('Surge','Sway','Heave','Roll','Pitch','Yaw')
from .task_workspace_models import AXIS_COLORS
COLORS=AXIS_COLORS


class GainPlot(QWidget):
    seek_requested=pyqtSignal(float)
    def __init__(self,parent=None):
        super().__init__(parent);self.recording=None;self.current_s=0.;self.values=None;self.alternate=None
        self.setMinimumSize(300,180)
        self.setToolTip('Applied POST-WASHOUT multipliers. Dashed = other A/B side. Click to seek. '
                        '100% is unchanged processed input, not a platform angle.')

    def set_recording(self,recording,alternate=None):
        self.recording=recording;self.alternate=alternate
        self.values=(np.array([f.analysis['dynamic']['multipliers'] for f in recording.frames]) if recording else None)
        self.other=(np.array([f.analysis['dynamic']['multipliers'] for f in alternate.frames]) if alternate else None)
        self.update()

    def set_current(self,seconds):self.current_s=float(seconds);self.update()

    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing);p.fillRect(self.rect(),QColor('#101e2d'))
        if not self.recording:
            p.setPen(QColor('#c4d1dd'));p.drawText(self.rect(),Qt.AlignmentFlag.AlignCenter,'Open a dynamic profile to view gain curves');return
        left=74.;right=max(left+5.,self.width()-22.);top=36.;bottom=max(top+6.,self.height()-28.)
        h=(bottom-top)/6.;duration=max(.001,self.recording.duration)
        p.setPen(QColor('#dbe7ef'));p.drawText(12,20,'Applied multipliers 0-200% | solid = active, dashed = comparison')
        indices=np.linspace(0,len(self.recording.times)-1,min(len(self.recording.times),max(300,int(right-left)*2)),dtype=int)
        for axis in range(6):
            y=top+axis*h
            p.setPen(QPen(QColor('#354655'),1));p.drawLine(QPointF(left,y+h/2),QPointF(right,y+h/2))
            p.setPen(QColor(COLORS[axis]));p.drawText(QRectF(4,y,66,h),Qt.AlignmentFlag.AlignVCenter,AXES[axis])
            for values,color,dash in ((self.other,COLORS[axis],True),(self.values,COLORS[axis],False)):
                if values is None:continue
                path=QPainterPath()
                for k,i in enumerate(indices):
                    x=left+(right-left)*self.recording.times[i]/duration
                    yy=y+h-3-(h-6)*max(0.,min(2.,float(values[i,axis])))/2.
                    if k==0:path.moveTo(x,yy)
                    else:path.lineTo(x,yy)
                pen=QPen(QColor(color),1.2)
                if dash:pen.setStyle(Qt.PenStyle.DashLine)
                p.setPen(pen);p.drawPath(path)
        x=left+(right-left)*min(duration,max(0.,self.current_s))/duration
        p.setPen(QPen(QColor('#ffffff'),1));p.drawLine(QPointF(x,top),QPointF(x,bottom))
        p.setPen(QColor('#c4d1dd'));p.drawText(int(left),self.height()-8,'0 s')
        p.drawText(int(right-70),self.height()-8,f'{duration:.1f} s')

    def mousePressEvent(self,event):
        if self.recording and event.button()==Qt.MouseButton.LeftButton:
            left=74.;right=max(left+5.,self.width()-22.)
            seconds=max(0.,min(1.,(event.position().x()-left)/(right-left)))*self.recording.duration
            self.seek_requested.emit(seconds)
        super().mousePressEvent(event)


class DynamicProfilePanel(QScrollArea):
    seek_requested=pyqtSignal(float)
    def __init__(self,parent=None):
        super().__init__(parent);self.setWidgetResizable(True);self.recording=None
        body=QWidget();layout=QVBoxLayout(body);layout.setContentsMargins(8,8,8,8)
        self.title=QLabel('No dynamic profile loaded');self.title.setWordWrap(True);layout.addWidget(self.title)
        self.status=QLabel();self.status.setWordWrap(True);layout.addWidget(self.status)
        self.table=QTableWidget(6,3);self.table.setHorizontalHeaderLabels(['DOF','Applied','Profile curve'])
        self.table.verticalHeader().hide();self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumHeight(185);self.table.setMaximumHeight(220)
        for i,a in enumerate(AXES):
            for j,value in enumerate((a,'-','-')):self.table.setItem(i,j,QTableWidgetItem(value))
        layout.addWidget(self.table)
        self.summary=QLabel();self.summary.setWordWrap(True);self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse);layout.addWidget(self.summary)
        self.jump=QPushButton('Inspect largest muscle-command step');self.jump.clicked.connect(self.jump_peak);layout.addWidget(self.jump)
        self.warning=QLabel('Research playback only. Feasible lengths do not establish actuator response, comfort or live safety.')
        self.warning.setWordWrap(True);layout.addWidget(self.warning);layout.addStretch(1);self.setWidget(body)

    def set_recording(self,recording,alternate=None):
        self.recording=recording
        if recording is None:
            self.title.setText('No dynamic profile loaded');self.summary.clear();return
        d=recording.dynamic_profile;metrics=tuning_metrics(recording)
        self.title.setText(d['label']+'\n'+d['source']+' | read-only')
        self.title.setToolTip(recording.diagnostics)
        text=(f'Requested excess: {metrics.worst_requested_excess_mm:.2f} mm / {metrics.requested_oob_frames} ticks\n'
              f'Workspace: {metrics.workspace_frames} ticks; final limits: {metrics.final_clipped_frames} ticks\n'
              f'Largest output step: {metrics.peak_step_mm:.2f} mm per tick\n')
        if alternate is not None:
            other=tuning_metrics(alternate)
            text+=f'Other side: excess {other.worst_requested_excess_mm:.2f} mm; largest step {other.peak_step_mm:.2f} mm\n'
            vals=lambda rec:np.asarray([f.processed_axes for f in rec.frames if f.analysis['dynamic']['ride_time_s']>=0])
            a=np.percentile(np.abs(np.clip(vals(recording),-1,1)),95,axis=0)
            b=np.percentile(np.abs(np.clip(vals(alternate),-1,1)),95,axis=0)
            text+='Primary request p95 (active / other):\n'+'; '.join(f'{AXES[i]} {100*a[i]:.1f}/{100*b[i]:.1f}%' for i in (3,4,2))
        text+='\nMetrics include pre-roll except primary p95. Compare with original generator metrics on their stated scope.'
        self.summary.setText(text)

    def show_frame(self,frame):
        if frame is None or 'dynamic' not in frame.analysis:return
        d=frame.analysis['dynamic'];lag=d['profile_lag_s'];res=d['position_residual_m']
        text=f'Ride {d["ride_time_s"]:.2f} s | reference lookup {d["phase_s"]:.2f} s\nSync: {d["status"]}'
        if lag is not None:text+=f' | lookup lag {1000*lag:+.1f} ms'
        else:text+=' | lookup lag not recorded'
        if res is not None:text+=f'\nPosition residual {res*1000:.1f} mm'
        if d['fallback_active']:text+='\nFallback/blend is active'
        self.status.setText(text)
        for i in range(6):
            self.table.item(i,1).setText(f'{d["multipliers"][i]*100:.1f}%')
            self.table.item(i,2).setText(f'{d["profile_multipliers"][i]*100:.1f}%')

    def jump_peak(self):
        if self.recording is None:return
        frames=self.recording.frames
        if len(frames)<2:return
        i=max(range(1,len(frames)),key=lambda j:max(abs(a-b) for a,b in zip(frames[j].muscle_lengths_mm,frames[j-1].muscle_lengths_mm)))
        self.seek_requested.emit(frames[i].seconds)
