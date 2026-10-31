# test_qt6_backend_contract.py ver 1
"""Backend API and separation tests; run even without PyQt6 installed."""
from __future__ import annotations

import ast
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app.py"
BACKEND = ROOT / "video_backend.py"


def test_player_is_pyqt6_only_and_production_not_migrated():
    for path in (ROOT / "app.py", BACKEND, ROOT / "recording.py", ROOT / "video_sync.py"):
        source = path.read_text(encoding="utf-8")
        assert not re.search(r"^\s*(from|import)\s+PyQt5", source, re.MULTILINE), path
    assert "from PyQt6.QtWidgets import" in APP.read_text(encoding="utf-8")
    assert "from PyQt6.QtMultimedia import" in BACKEND.read_text(encoding="utf-8")


def test_qt6_backend_has_single_first_frame_bootstrap_but_no_preview_pulses():
    text = BACKEND.read_text(encoding="utf-8")
    assert 'os.environ["QT_MEDIA_BACKEND"] = "ffmpeg"' in text
    tree = ast.parse(text)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Qt6VideoBackend")
    methods = {n.name: ast.get_source_segment(text, n) for n in cls.body if isinstance(n, ast.FunctionDef)}
    assert "self.player.play()" in methods["load"]  # Once when opening a new file
    assert "self.player.play()" in methods["play"]
    assert "self.player.play()" not in methods["seek"]
    assert "self.player.pause()" not in methods["seek"]
    assert "self.player.setPosition" in methods["seek"]
    assert "frame.startTime()" in methods["_on_frame"]
    assert "self.player.pause()" in methods["_finish_first_frame"]


def test_no_v3_decoder_preview_code_remains():
    text = APP.read_text(encoding="utf-8")
    for unwanted in ("_preview_paused_seek", "paused_preview_timer", "_restore_preview_audio", "video_player"):
        assert unwanted not in text
    assert "self.video_backend.seek(target_ms)" in text
    assert "QFileDialog.Option.DontUseNativeDialog" in text
    assert "QMainWindow.DockOption.AllowNestedDocks" in text
