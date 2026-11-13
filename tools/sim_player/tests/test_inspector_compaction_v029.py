"""Contracts for Task Workspace v0.2.9 inspector compaction."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _source(name):
    return (ROOT / name).read_text(encoding='utf-8')


def test_internal_duplicate_inspector_header_is_removed():
    src = _source('task_workspace.py')
    assert "self.title = QLabel('Inspector')" not in src
    assert 'self.expand_button' not in src
    assert 'self.collapse_button' not in src


def test_dock_title_carries_active_workflow():
    app = _source('app.py')
    ui = _source('player_ui.py')
    assert "inspector_dock.setWindowTitle(f'Inspector : {label}')" in app
    assert '"Inspector : Review"' in ui


def test_context_is_one_line_until_real_details_exist():
    src = _source('task_workspace.py')
    assert 'class CompactContextSection' in src
    assert "self.summary = QLabel('No capture loaded')" in src
    assert "self.context_section.set_context('No capture loaded', expandable=False)" in src
    assert 'self.context_section.set_context(compact, detail, expandable=True)' in src


def test_expand_collapse_actions_remain_available_without_a_header_row():
    src = _source('task_workspace.py')
    assert "menu.addAction('Expand all sections'" in src
    assert "menu.addAction('Collapse all sections'" in src
