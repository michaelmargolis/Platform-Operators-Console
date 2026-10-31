"""MDX Motion Replay -- standalone UI prototype (synthetic data only).

Run: python mdx_motion_replay_ui_prototype.py
Requires: PyQt5 (already used by POC); no video, NL2, UDP or hardware access.

Design seams for later implementation:
  Recording.sample(index) -> FrameSample (replace synthetic data with CSV +
      shared production-equivalent motion processing).
  Panel registry in ReplayWindow.add_panel() (add future pyqtgraph strip charts,
      clearance and tension-feasibility docks without changing the window shell).
  QTimer is a display clock only; processing will be precomputed offline.
"""
from __future__ import annotations

import math
import sys
import time
from dataclasses import dataclass

from PyQt5.QtCore import Qt, QRectF, QSize, pyqtSignal, QTimer
from PyQt5.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QBrush
from PyQt5.QtWidgets import (
    QApplication, QComboBox, QDockWidget, QFrame, QHBoxLayout, QLabel,
    QMainWindow, QMenu, QPushButton, QSlider, QStyle, QToolButton,
    QVBoxLayout, QWidget,
)

FPS = 20
DURATION_S = 150
AXES = ("Surge", "Sway", "Heave", "Roll", "Pitch", "Yaw")
BG = "#101923"
PANEL = "#1a2633"
TRACK = "#304153"
TEXT = "#e8eef5"
MUTED = "#9dafc1"
ACCENT = "#50a7df"


@dataclass(frozen=True)
class FrameSample:
    seconds: float
    # These are synthetic values for UI evaluation, not NL2 or POC outputs.
    axes: tuple[float, ...]
    lengths_mm: tuple[float, ...]


@dataclass(frozen=True)
class RideEvent:
    seconds: float
    name: str
    color: str


class DemoRecording:
    def __init__(self):
        self.name = "Blackbird  /  UI DEMO"
        self.events = (
            RideEvent(4, "Dispatch", "#71c6a3"),
            RideEvent(8, "Depart", "#71c6a3"),
            RideEvent(62, "Inversion", "#f4ae62"),
            RideEvent(136, "Return", "#71c6a3"),
            RideEvent(140, "READY", "#71c6a3"),
        )
        self.frames = [self._make_frame(i / FPS) for i in range(DURATION_S * FPS + 1)]

    def _make_frame(self, t):
        a = (
            .25 * math.sin(t * .11),
            .18 * math.sin(t * .14 + .5),
            -.35 * math.sin(t * .085 + 1.4),
            .52 * math.sin(t * .18),
            .43 * math.sin(t * .10 + .6),
            .30 * math.sin(t * .06 + 2),
        )
        lengths = tuple(
            860 + 68 * math.sin(t * (.12 + i * .006) + i * .85)
            + 21 * math.sin(t * .38 + i * 1.2)
            for i in range(6)
        )
        return FrameSample(t, a, lengths)

    def sample(self, frame_index):
        return self.frames[max(0, min(frame_index, len(self.frames) - 1))]


def timestamp(seconds):
    minutes, remainder = divmod(float(seconds), 60)
    return f"{int(minutes):02d}:{remainder:05.2f}"


class BarPanel(QWidget):
    """One painter-drawn bar group; no dependency on the future graph backend."""

    def __init__(self, names, signed, parent=None):
        super().__init__(parent)
        self.names = names
        self.signed = signed
        self.values = (0,) * len(names) if signed else (860,) * len(names)
        self.setMinimumSize(270, 245)

    def update_values(self, values):
        self.values = values
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        left, right = 86, 72
        bar_width = max(30, w - left - right - 18)
        top = 34
        row_height = max(30, min(48, (h - top - 31) / len(self.names)))
        p.setPen(QColor(MUTED))
        p.setFont(QFont("Segoe UI", 9))
        p.drawText(14, 10, w - 28, 16, Qt.AlignLeft, "NORMALIZED  -100% to +100%" if self.signed else "MUSCLE LENGTHS  750-1000 mm")
        for i, (name, value) in enumerate(zip(self.names, self.values)):
            y = top + i * row_height
            rect = QRectF(left, y + 5, bar_width, 15)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(TRACK))
            p.drawRoundedRect(rect, 4, 4)
            if self.signed:
                value = max(-1.0, min(1.0, value))
                centre = rect.center().x()
                half = rect.width() / 2
                x = centre + min(0, value) * half
                extent = max(1.0, abs(value) * half)
                fill = QRectF(x, rect.y(), extent, rect.height())
                color = "#eda96a" if i in (3, 4) else ACCENT
                p.setBrush(QColor(color))
                p.drawRoundedRect(fill, 3, 3)
                p.setPen(QPen(QColor("#dce4ec"), 1))
                p.drawLine(int(centre), int(rect.top() - 2), int(centre), int(rect.bottom() + 2))
                value_text = f"{value * 100:+.0f}%"
            else:
                frac = max(0, min(1, (value - 750) / 250))
                p.setBrush(QColor("#5fbdaf"))
                p.drawRoundedRect(QRectF(rect.x(), rect.y(), max(1, rect.width() * frac), rect.height()), 3, 3)
                value_text = f"{value:.0f} mm"
            p.setPen(QColor(TEXT))
            p.drawText(14, int(y), 64, 25, Qt.AlignVCenter | Qt.AlignLeft, name)
            p.drawText(int(left + bar_width + 7), int(y), right, 25, Qt.AlignVCenter | Qt.AlignLeft, value_text)
        p.setPen(QColor(MUTED))
        p.setFont(QFont("Segoe UI", 8))
        p.drawText(14, h - 21, w - 28, 16, Qt.AlignLeft, "SYNTHETIC DEMO DATA")
        p.end()


class VideoPlaceholder(QWidget):
    """A clearly labeled animated placeholder; no video backend at this stage."""

    def __init__(self):
        super().__init__()
        self.seconds = 0.0
        self.setMinimumSize(340, 215)

    def set_seconds(self, seconds):
        self.seconds = seconds
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#101e2d"))
        w, h = self.width(), self.height()
        path = QPainterPath()
        for j in range(121):
            x = 20 + (w - 40) * j / 120
            y = h * .66 - math.sin(j / 120 * 3.4 * math.pi) * h * .16 - math.sin(j / 120 * 7.2 * math.pi) * h * .035
            if j == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        p.setPen(QPen(QColor("#3d6b8c"), 7))
        p.drawPath(path)
        p.setPen(QPen(QColor("#88b5d0"), 2))
        p.drawPath(path)
        progress = (self.seconds % 23) / 23
        x = 20 + (w - 40) * progress
        j = progress * 120
        y = h * .66 - math.sin(j / 120 * 3.4 * math.pi) * h * .16 - math.sin(j / 120 * 7.2 * math.pi) * h * .035
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#f0af68"))
        p.drawRoundedRect(QRectF(x - 16, y - 15, 32, 15), 5, 5)
        p.setPen(QColor(TEXT))
        p.setFont(QFont("Segoe UI", 15, QFont.Bold))
        p.drawText(0, 16, w, 32, Qt.AlignHCenter, "VIDEO PREVIEW")
        p.setFont(QFont("Segoe UI", 10))
        p.setPen(QColor(MUTED))
        p.drawText(0, 50, w, 23, Qt.AlignHCenter, "Placeholder animation  -  no MP4 loaded")
        p.setPen(QColor(TEXT))
        p.drawText(14, h - 36, w - 28, 22, Qt.AlignRight, timestamp(self.seconds))
        p.end()


class ClickSlider(QSlider):
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            usable = max(1, self.width() - 24)
            fraction = max(0, min(1, (event.x() - 12) / usable))
            self.setValue(round(self.minimum() + fraction * (self.maximum() - self.minimum())))
            event.accept()
        super().mousePressEvent(event)


class EventTrack(QWidget):
    seek_requested = pyqtSignal(float)

    def __init__(self, events, duration):
        super().__init__()
        self.events, self.duration = events, duration
        self.seconds = 0
        self.setMinimumHeight(58)
        self.setMouseTracking(True)

    def set_seconds(self, seconds):
        self.seconds = seconds
        self.update()

    def x_for_time(self, seconds):
        return 24 + (self.width() - 48) * seconds / self.duration

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        nearest = min(self.events, key=lambda mark: abs(self.x_for_time(mark.seconds) - event.x()))
        if abs(self.x_for_time(nearest.seconds) - event.x()) <= 13:
            self.seek_requested.emit(nearest.seconds)
        else:
            self.seek_requested.emit(max(0, min(self.duration, (event.x() - 24) / max(1, self.width() - 48) * self.duration)))

    def mouseMoveEvent(self, event):
        nearest = min(self.events, key=lambda mark: abs(self.x_for_time(mark.seconds) - event.x()))
        self.setToolTip(f"{nearest.name} — {timestamp(nearest.seconds)}" if abs(self.x_for_time(nearest.seconds) - event.x()) <= 14 else "Click to seek")

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        p.setPen(QPen(QColor("#3e5062"), 2))
        p.drawLine(24, 21, w - 24, 21)
        p.setFont(QFont("Segoe UI", 8))
        for mark in self.events:
            x = self.x_for_time(mark.seconds)
            p.setPen(QPen(QColor(mark.color), 2))
            p.drawLine(int(x), 12, int(x), 30)
            p.setBrush(QColor(mark.color))
            p.drawEllipse(QRectF(x - 3, 18, 6, 6))
            p.setPen(QColor(MUTED))
            p.drawText(int(x - 38), 33, 76, 20, Qt.AlignHCenter, mark.name)
        p.setPen(QPen(QColor(TEXT), 2))
        x = self.x_for_time(self.seconds)
        p.drawLine(int(x), 4, int(x), 31)
        p.end()


class ReplayWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.recording = DemoRecording()
        self.index = 0
        self.playing = False
        self.play_speed = 1.0
        self.play_from_time = 0.0
        self.play_from_clock = 0.0
        self.was_playing_before_drag = False
        self.panels = {}
        self.setWindowTitle("MDX Motion Replay — UI Prototype")
        self.resize(1250, 760)
        self.setDockOptions(QMainWindow.AllowNestedDocks | QMainWindow.AllowTabbedDocks | QMainWindow.AnimatedDocks)
        self._build_central()
        self._build_panels()
        self._build_menu()
        self._update_frame(0)
        self.clock = QTimer(self)
        self.clock.setInterval(33)
        self.clock.timeout.connect(self._tick)
        self.clock.start()
        self.statusBar().showMessage("UI PROTOTYPE  •  20 Hz generated data  •  no video, NoLimits2, SHS, UDP or Festo connections")

    def _build_central(self):
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(9)
        row = QHBoxLayout()
        title = QLabel("BLACKBIRD  /  SYNTHETIC RIDE")
        title.setObjectName("sectionTitle")
        row.addWidget(title)
        row.addStretch(1)
        badge = QLabel("DEMO DATA")
        badge.setObjectName("demoBadge")
        row.addWidget(badge)
        layout.addLayout(row)

        self.video = VideoPlaceholder()
        layout.addWidget(self.video, 1)
        heading = QHBoxLayout()
        heading.addWidget(QLabel("RIDE EVENTS  —  click a marker to seek"))
        heading.addStretch(1)
        self.time_label = QLabel()
        self.time_label.setObjectName("timeLabel")
        heading.addWidget(self.time_label)
        layout.addLayout(heading)
        self.event_track = EventTrack(self.recording.events, DURATION_S)
        self.event_track.seek_requested.connect(self.seek_seconds)
        layout.addWidget(self.event_track)
        self.slider = ClickSlider(Qt.Horizontal)
        self.slider.setRange(0, len(self.recording.frames) - 1)
        self.slider.valueChanged.connect(self._slider_changed)
        self.slider.sliderPressed.connect(self._drag_started)
        self.slider.sliderReleased.connect(self._drag_finished)
        layout.addWidget(self.slider)
        controls = QHBoxLayout()
        for caption, function, tip in (
            ("−5s", lambda: self.seek_seconds(self.current_seconds() - 5), "Back 5 seconds"),
            ("◀", lambda: self.seek_frame(self.index - 1), "Back one telemetry frame"),
        ):
            b = QPushButton(caption)
            b.setToolTip(tip)
            b.clicked.connect(function)
            controls.addWidget(b)
        self.play_button = QPushButton("▶  Play")
        self.play_button.setObjectName("playButton")
        self.play_button.clicked.connect(self.toggle_play)
        controls.addWidget(self.play_button)
        for caption, function, tip in (
            ("▶", lambda: self.seek_frame(self.index + 1), "Forward one telemetry frame"),
            ("+5s", lambda: self.seek_seconds(self.current_seconds() + 5), "Forward 5 seconds"),
        ):
            b = QPushButton(caption)
            b.setToolTip(tip)
            b.clicked.connect(function)
            controls.addWidget(b)
        controls.addSpacing(15)
        controls.addWidget(QLabel("Speed"))
        self.speed = QComboBox()
        for value in (.25, .5, 1, 2):
            self.speed.addItem(f"{value:g}×", value)
        self.speed.setCurrentIndex(2)
        self.speed.currentIndexChanged.connect(self._speed_changed)
        controls.addWidget(self.speed)
        controls.addStretch(1)
        self.panels_button = QToolButton()
        self.panels_button.setText("Panels ▾")
        self.panels_button.setPopupMode(QToolButton.InstantPopup)
        controls.addWidget(self.panels_button)
        layout.addLayout(controls)
        central.setMinimumWidth(400)
        self.setCentralWidget(central)

    def add_panel(self, key, title, widget, area):
        """Future charts/tension feasibility can register here as new docks."""
        dock = QDockWidget(title, self)
        dock.setObjectName(f"dock_{key}")
        dock.setAllowedAreas(Qt.AllDockWidgetAreas)
        dock.setFeatures(QDockWidget.DockWidgetClosable | QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        dock.setWidget(widget)
        self.addDockWidget(area, dock)
        self.panels[key] = dock
        return dock

    def _build_panels(self):
        self.axis_bars = BarPanel(AXES, signed=True)
        self.length_bars = BarPanel(tuple(f"M{i + 1}" for i in range(6)), signed=False)
        a = self.add_panel("axes", "Transform  ·  6DOF", self.axis_bars, Qt.RightDockWidgetArea)
        b = self.add_panel("muscles", "Muscle Lengths  ·  mm", self.length_bars, Qt.RightDockWidgetArea)
        self.splitDockWidget(a, b, Qt.Vertical)

    def _build_menu(self):
        file_menu = self.menuBar().addMenu("File")
        reset = file_menu.addAction("Restart demo")
        reset.triggered.connect(lambda: self.seek_seconds(0))
        file_menu.addSeparator()
        file_menu.addAction("Exit", self.close)
        view = self.menuBar().addMenu("View")
        for dock in self.panels.values():
            view.addAction(dock.toggleViewAction())
        view.addSeparator()
        show_all = view.addAction("Show all panels")
        show_all.triggered.connect(lambda: [dock.show() for dock in self.panels.values()])
        restore = view.addAction("Restore default layout")
        restore.triggered.connect(self.restore_layout)
        self.panels_button.setMenu(view)

    def restore_layout(self):
        for dock in self.panels.values():
            dock.setFloating(False)
            dock.show()
            self.addDockWidget(Qt.RightDockWidgetArea, dock)
        self.splitDockWidget(self.panels["axes"], self.panels["muscles"], Qt.Vertical)
        self.resizeDocks(list(self.panels.values()), [320, 320], Qt.Vertical)

    def current_seconds(self):
        return self.index / FPS

    def _update_frame(self, index):
        self.index = max(0, min(index, len(self.recording.frames) - 1))
        sample = self.recording.sample(self.index)
        self.axis_bars.update_values(sample.axes)
        self.length_bars.update_values(sample.lengths_mm)
        self.video.set_seconds(sample.seconds)
        self.event_track.set_seconds(sample.seconds)
        self.time_label.setText(f"{timestamp(sample.seconds)}   /   {timestamp(DURATION_S)}")
        self.slider.blockSignals(True)
        self.slider.setValue(self.index)
        self.slider.blockSignals(False)

    def seek_frame(self, index):
        self._update_frame(index)
        if self.playing:
            self.play_from_time = self.current_seconds()
            self.play_from_clock = time.monotonic()

    def seek_seconds(self, seconds):
        self.seek_frame(round(max(0, min(DURATION_S, seconds)) * FPS))

    def _slider_changed(self, index):
        self.seek_frame(index)

    def _drag_started(self):
        self.was_playing_before_drag = self.playing
        if self.playing:
            self._set_playing(False)

    def _drag_finished(self):
        if self.was_playing_before_drag:
            self._set_playing(True)
        self.was_playing_before_drag = False

    def _set_playing(self, active):
        if active and self.index >= len(self.recording.frames) - 1:
            self._update_frame(0)
        self.playing = active
        self.play_from_time = self.current_seconds()
        self.play_from_clock = time.monotonic()
        self.play_button.setText("❚❚  Pause" if active else "▶  Play")

    def toggle_play(self):
        self._set_playing(not self.playing)

    def _speed_changed(self, index):
        at = self.current_seconds()
        self.play_speed = float(self.speed.itemData(index))
        self.play_from_time = at
        self.play_from_clock = time.monotonic()

    def _tick(self):
        if not self.playing or self.slider.isSliderDown():
            return
        elapsed = (time.monotonic() - self.play_from_clock) * self.play_speed
        target = self.play_from_time + elapsed
        if target >= DURATION_S:
            self._update_frame(len(self.recording.frames) - 1)
            self._set_playing(False)
            return
        self._update_frame(round(target * FPS))


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(f"""
        QMainWindow, QWidget {{ background: {BG}; color: {TEXT}; font: 10pt 'Segoe UI'; }}
        QDockWidget {{ background: {PANEL}; border: 1px solid #344556; }}
        QDockWidget::title {{ background: #26384a; padding: 8px 10px; text-align: left; }}
        QDockWidget > QWidget {{ background: {PANEL}; }}
        QMenuBar, QMenu, QStatusBar {{ background: #1c2a38; color: {TEXT}; }}
        QMenu::item:selected {{ background: #315776; }}
        QPushButton, QToolButton, QComboBox {{ background: #283b4e; color: {TEXT};
            border: 1px solid #4a6378; border-radius: 5px; padding: 6px 12px; }}
        QPushButton:hover, QToolButton:hover {{ background: #36566f; }}
        QPushButton#playButton {{ background: #23688d; font-weight: bold; min-width: 100px; }}
        QLabel#sectionTitle {{ font-size: 13pt; font-weight: bold; }}
        QLabel#timeLabel {{ color: #d2e9f7; font-weight: bold; }}
        QLabel#demoBadge {{ background: #4d3f27; color: #ffdfa7; border-radius: 5px; padding: 5px 8px; }}
        QSlider::groove:horizontal {{ height: 6px; border-radius: 3px; background: #405367; }}
        QSlider::handle:horizontal {{ background: {ACCENT}; width: 16px; margin: -6px 0;
            border-radius: 8px; }}
        QStatusBar {{ color: {MUTED}; }}
    """)
    window = ReplayWindow()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
