"""Contracts for Task Workspace v0.2.7 plot palette and workflow discoverability."""
from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]


def _source(name):
    return (ROOT / name).read_text(encoding='utf-8')


def _axis_colors():
    tree = ast.parse(_source('task_workspace_models.py'))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == 'AXIS_COLORS':
                    return tuple(ast.literal_eval(node.value))
    raise AssertionError('AXIS_COLORS not found')


def test_pitch_is_bright_red_and_yaw_is_yellow_in_shared_palette():
    colors = _axis_colors()
    assert colors[4].lower() == '#ff4d4d'
    assert colors[5].lower() == '#ffe34f'


def test_plot_families_share_axis_palette():
    models = _source('task_workspace_models.py')
    dynamic = _source('dynamic_panel.py')
    workspace = _source('task_workspace.py')
    assert 'AXIS_COLORS[AXES.index(name)]' in models
    assert 'COLORS=AXIS_COLORS' in dynamic
    assert 'plot_styles' in workspace


def test_workflow_group_has_subtle_container_and_specific_hover_help():
    ui = _source('player_ui.py')
    assert "setObjectName('workflowModeGroup')" in ui
    assert "QFrame#workflowModeGroup" in ui
    assert "QLabel#workflowGroupLabel" in ui
    assert "'review': 'Review: inspect motion" in ui
    assert "'tune': 'Tune: experiment offline" in ui
    assert "'validate': 'Validate: check calculated" in ui


def test_workflow_right_click_is_local_and_help_only():
    ui = _source('player_ui.py')
    assert 'Qt.ContextMenuPolicy.CustomContextMenu' in ui
    assert "menu.addAction(f'Help about {title}')" in ui
    assert 'self.show_workflow_help(m)' in ui
    block = ui[ui.index('def _show_workflow_context_menu'):ui.index('def show_workflow_help')]
    assert 'toggleViewAction' not in block
    assert 'Restore task workspace' not in block


def test_quick_help_has_purpose_and_process_for_all_three_workflows():
    content = _source('workspace_help_content.py')
    assert 'WORKFLOW_QUICK_HELP' in content
    for key in ('review', 'tune', 'validate'):
        assert f"'{key}': {{" in content
    assert "'purpose':" in content
    assert "'steps': (" in content
    dialog = _source('workspace_help.py')
    assert 'class WorkflowHelpDialog' in dialog
    assert "<h3>What to do</h3><ol>" in dialog
