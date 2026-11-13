"""Contracts for v0.2.10 inspector title/frame attachment styling."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def _source(name):
    return (ROOT / name).read_text(encoding="utf-8")

def test_collapsible_sections_are_their_own_frames():
    src = _source("task_workspace.py")
    assert "class Section(QFrame):" in src
    assert "class CompactContextSection(QFrame):" in src
    assert src.count("self.setObjectName('inspectorPanel')") >= 2
    assert "layout.setContentsMargins(0, 0, 0, 0)" in src
    assert "layout.setSpacing(0)" in src

def test_section_button_and_body_share_one_visual_frame():
    ui = _source("player_ui.py")
    assert "QFrame#inspectorPanel" in ui
    assert "QToolButton#inspectorSection" in ui
    assert "border-right: 1px solid #426078" in ui
    assert "border-bottom: 1px solid #426078" in ui
    assert "QWidget#inspectorCardBody {{ background: transparent; border: none; }}" in ui

def test_inspector_dock_title_uses_colon_separator():
    ui = _source("player_ui.py")
    app = _source("app.py")
    assert '"Inspector : Review"' in ui
    assert "setWindowTitle(f'Inspector : {label}')" in app
    assert "Inspector / Review" not in ui
