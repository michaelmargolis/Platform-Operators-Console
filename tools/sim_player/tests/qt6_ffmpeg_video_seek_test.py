"""Standalone Windows video seek diagnostic for sim_player research.

Requires: pip install PyQt6
Run: python qt6_ffmpeg_video_seek_test.py "C:\\path\\to\\recording.mp4"

This does not import POC or alter any POC/sim_player file.
Uses Qt 6 FFmpeg backend and never plays video merely to refresh a paused seek.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

# Must be set before importing/initialising Qt Multimedia.
os.environ["QT_MEDIA_BACKEND"] = "ffmpeg"

from PyQt6.QtCore import QTimer, Qt, QUrl, QT_VERSION_STR, PYQT_VERSION_STR
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QFileDialog, QHBoxLayout, QLabel, QMainWindow,
    QPushButton, QSlider, QVBoxLayout, QWidget,
)
from PyQt6.QtMultimedia import QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget


class SeekTest(QMainWindow):
    def __init__(self, video_path: str):
        super().__init__()
        self.setWindowTitle("Qt 6 / FFmpeg — paused video seek test")
        self.resize(1100, 730)
        self.video_path = video_path
        self.first_frame = True
        self.last_seek_at = None
        self.last_seek_ms = None
        self.last_pts_ms = None
        self.pending_drag_ms = None
        self.slider_dragging = False

        root = QWidget()
        column = QVBoxLayout(root)
        self.setCentralWidget(root)
        self.screen = QVideoWidget()
        column.addWidget(self.screen, 1)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 1)
        self.slider.sliderPressed.connect(self._drag_start)
        self.slider.sliderMoved.connect(self._drag_move)
        self.slider.sliderReleased.connect(self._drag_finish)
        column.addWidget(self.slider)

        buttons = QHBoxLayout()
        column.addLayout(buttons)
        self.play_btn = QPushButton("Play")
        self.play_btn.clicked.connect(self.toggle_play)
        buttons.addWidget(self.play_btn)
        for seconds in (0, 10, 30, 60):
            b = QPushButton(f"Seek {seconds}s")
            b.clicked.connect(lambda _, t=seconds: self.seek_ms(t * 1000))
            buttons.addWidget(b)
        self.drag_updates = QCheckBox("Update video while dragging (up to 8 seeks/s)")
        self.drag_updates.setChecked(True)
        buttons.addWidget(self.drag_updates)

        self.info = QLabel("Loading video...")
        self.info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        column.addWidget(self.info)
        self.status = QLabel(f"Qt {QT_VERSION_STR} | PyQt {PYQT_VERSION_STR} | QT_MEDIA_BACKEND=ffmpeg")
        column.addWidget(self.status)

        self.player = QMediaPlayer(self)
        self.player.setVideoOutput(self.screen)
        self.player.errorOccurred.connect(self.on_error)
        self.player.durationChanged.connect(self.on_duration)
        self.player.positionChanged.connect(self.on_position)
        self.player.playbackStateChanged.connect(self.on_state)
        self.player.seekableChanged.connect(self.on_seekable)
        # QVideoSink reports decoded frames, not merely a changed media position.
        self.screen.videoSink().videoFrameChanged.connect(self.on_video_frame)

        self.drag_timer = QTimer(self)
        self.drag_timer.setInterval(125)
        self.drag_timer.timeout.connect(self._flush_drag)

        self.player.setSource(QUrl.fromLocalFile(self.video_path))
        # Start decoding only once at load; freeze after the first actual frame.
        # No play()/pause() pulses are used on subsequent seeks.
        self.player.play()

    def on_error(self, error, message):
        self.status.setText(f"PLAYER ERROR: {error}: {message}")

    def on_duration(self, duration_ms):
        self.slider.setMaximum(max(1, int(duration_ms)))

    def on_seekable(self, seekable):
        self.status.setText(self.status.text() + f" | seekable={seekable}")

    def on_position(self, pos_ms):
        if not self.slider_dragging:
            self.slider.blockSignals(True)
            self.slider.setValue(int(pos_ms))
            self.slider.blockSignals(False)
        self.update_info()

    def on_state(self, state):
        self.play_btn.setText("Pause" if state == QMediaPlayer.PlaybackState.PlayingState else "Play")

    def on_video_frame(self, frame):
        if not frame.isValid():
            return
        pts_us = frame.startTime()
        self.last_pts_ms = pts_us / 1000.0 if pts_us >= 0 else None
        if self.first_frame:
            self.first_frame = False
            QTimer.singleShot(0, self.player.pause)
        if self.last_seek_at is not None:
            latency = 1000 * (time.perf_counter() - self.last_seek_at)
            difference = (self.last_pts_ms - self.last_seek_ms) if self.last_pts_ms is not None else float("nan")
            print(f"frame: PTS={self.last_pts_ms}ms seek={self.last_seek_ms}ms "
                  f"error={difference:+.0f}ms elapsed={latency:.0f}ms", flush=True)
            # Mark settled only when frame is within 150ms of requested position.
            if abs(difference) <= 150:
                self.last_seek_at = None
        self.update_info()

    def update_info(self):
        position_ms = self.player.position()
        desired_ms = self.last_seek_ms
        pts = "unknown" if self.last_pts_ms is None else f"{self.last_pts_ms / 1000:.3f}s"
        target = "none" if desired_ms is None else f"{desired_ms / 1000:.3f}s"
        self.info.setText(
            f"Position: {position_ms / 1000:.3f}s  |  Last decoded frame PTS: {pts}  |  "
            f"Last seek target: {target}  |  "
            f"State: {self.player.playbackState().name}"
        )

    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def seek_ms(self, target):
        target = min(max(0, int(target)), self.player.duration())
        self.last_seek_ms = target
        self.last_seek_at = time.perf_counter()
        self.player.setPosition(target)
        self.update_info()

    def _drag_start(self):
        self.slider_dragging = True
        self.pending_drag_ms = self.slider.value()
        if self.drag_updates.isChecked():
            self.drag_timer.start()

    def _drag_move(self, value):
        self.pending_drag_ms = int(value)

    def _flush_drag(self):
        if self.pending_drag_ms is not None and self.drag_updates.isChecked():
            value = self.pending_drag_ms
            self.pending_drag_ms = None
            self.seek_ms(value)

    def _drag_finish(self):
        self.drag_timer.stop()
        self.slider_dragging = False
        self.pending_drag_ms = None
        self.seek_ms(self.slider.value())

    def closeEvent(self, event):
        self.drag_timer.stop()
        self.player.stop()
        super().closeEvent(event)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", nargs="?", help="Recorded MP4/MKV file")
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    path = args.video
    if not path:
        path, _ = QFileDialog.getOpenFileName(None, "Open recorded video", "", "Video (*.mp4 *.mkv *.mov *.avi)")
    if not path:
        return 0
    window = SeekTest(os.path.abspath(path))
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
