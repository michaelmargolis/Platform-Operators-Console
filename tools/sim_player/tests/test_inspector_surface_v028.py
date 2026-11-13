"""Contracts for Task Workspace v0.2.8 inspector surface cohesion."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _source(name):
    return (ROOT / name).read_text(encoding='utf-8')


def test_task_inspector_uses_named_surface_container():
    src = _source('task_workspace.py')
    assert "body = QFrame()" in src
    assert "body.setObjectName('inspectorSurface')" in src


def test_stylesheet_defines_subtle_inspector_surface():
    ui = _source('player_ui.py')
    assert 'QFrame#inspectorSurface' in ui
    assert 'border-radius: 7px' in ui
    assert 'background: #132536' in ui
    assert 'border: 1px solid #294156' in ui
