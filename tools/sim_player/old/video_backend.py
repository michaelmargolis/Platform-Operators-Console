"""Qt 6 / FFmpeg video backend for sim_player, isolated from telemetry.

This module is imported by sim_player only. The operational POC continues
using PyQt5 in its own process. Never import PyQt5 in this package.

Qt 6 can decode/display a sought frame while paused; do not issue play/pause
pulses after seeks. We start decoding once when a recording is opened, solely
to render its first frame, then pause on the first *decoded* frame (as in the
standalone Windows test). All later positioning uses setPosition() while
paused or playing normally.
"""
from __future__ import annotations

import os
from pathlib import Path

# Set before Qt Multimedia is imported or any Qt media object is constructed.
# Qt 6's FFmpeg backend was validated on the user's Windows installation.
os.environ["QT_MEDIA_BACKEND"] = "ffmpeg"

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget


class Qt6VideoBackend(QObject):
    """Small interface to Qt 6 video: load, play, pause, seek, position.

    `frame_presented` is based on decoded frame PTS (not the media clock), for
    later measurement of video/telemetry sync and seeking precision.
    """

    error = pyqtSignal(str)
    frame_presented = pyqtSignal(float)  # video presentation time in seconds

    def __init__(self, widget: QVideoWidget, parent: QObject | None = None):
        super().__init__(parent)
        self.widget = widget
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.6)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(widget)
        self.player.errorOccurred.connect(self._on_error)
        self.widget.videoSink().videoFrameChanged.connect(self._on_frame)
        self.last_frame_pts_s: float | None = None
        self._generation = 0
        self._starting = False
        self._first_frame_target_ms = 0
        self._want_play = False

    def load(self, path: str | Path, initial_ms: int = 0) -> None:
        """Load a video and display its first useful frame while paused.

        First-frame bootstrap is necessary on some Qt platforms before a
        paused seek has a video frame to update. It runs once per file, and
        never on slider seeks or pause/resume.
        """
        self.unload()
        self._starting = True
        self._first_frame_target_ms = max(0, int(initial_ms))
        self._want_play = False
        self.player.setSource(QUrl.fromLocalFile(str(Path(path).resolve())))
        self.player.play()

    def unload(self) -> None:
        self._generation += 1
        self._starting = False
        self._first_frame_target_ms = 0
        self._want_play = False
        self.last_frame_pts_s = None
        self.player.stop()
        self.player.setSource(QUrl())

    def seek(self, target_ms: int) -> None:
        target = max(0, int(target_ms))
        # Remember the latest user request, including a seek between first
        # decoded frame and its deferred bootstrap-completion callback.
        self._first_frame_target_ms = target
        if not self._starting:
            self.player.setPosition(target)

    def position(self) -> int:
        return int(self._first_frame_target_ms if self._starting else self.player.position())

    def play(self) -> None:
        self._want_play = True
        if not self._starting:
            self.player.play()

    def pause(self) -> None:
        self._want_play = False
        if not self._starting:
            self.player.pause()

    def set_speed(self, factor: float) -> None:
        self.player.setPlaybackRate(float(factor))

    def _on_error(self, _code, message: str) -> None:
        self.error.emit(str(message) or self.player.errorString())

    def _on_frame(self, frame) -> None:
        if not frame.isValid():
            return
        pts_us = frame.startTime()
        self.last_frame_pts_s = pts_us / 1_000_000.0 if pts_us >= 0 else None
        if self.last_frame_pts_s is not None:
            self.frame_presented.emit(self.last_frame_pts_s)
        if self._starting:
            generation = self._generation
            self._starting = False
            # Qt media operations may not be reentrant in a sink callback.
            QTimer.singleShot(0, lambda: self._finish_first_frame(generation))

    def _finish_first_frame(self, generation: int) -> None:
        if generation != self._generation:
            return  # User opened a different recording in the meantime.
        if not self._want_play:
            self.player.pause()
        target = self._first_frame_target_ms
        if target > 0:
            # Qt 6 FFmpeg decodes the sought frame even when paused.
            self.player.setPosition(target)
