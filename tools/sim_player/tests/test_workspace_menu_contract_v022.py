"""Execute the actual menu-builder with minimal non-Qt doubles.

These test action wiring and state, NOT Qt painting, sizing or event dispatch.
They complement the optional real-Qt tests, rather than substituting for them.
"""
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pytest

PLAYER = Path(__file__).resolve().parents[1]


class Signal:
    def __init__(self):
        self.callbacks = []
    def connect(self, callback):
        self.callbacks.append(callback)
    def emit(self, *args):
        for cb in self.callbacks:
            cb(*args)


class Action:
    def __init__(self, text, parent=None):
        self.text = text
        self.triggered, self.toggled = Signal(), Signal()
        self.checked, self.enabled, self.blocked = False, True, False
    def setCheckable(self, value):
        self.checkable = value
    def setChecked(self, value):
        previous, self.checked = self.checked, value
        if previous != value and not self.blocked:
            self.toggled.emit(value)
    def isChecked(self):
        return self.checked
    def blockSignals(self, value):
        previous, self.blocked = self.blocked, value
        return previous
    def setEnabled(self, value):
        self.enabled = value
    def setShortcut(self, value):
        self.shortcut = value
    def setShortcutContext(self, value):
        self.shortcut_context = value
    def setIcon(self, value):
        self.icon = value
    def setObjectName(self, value):
        self.object_name = value
    def setToolTip(self, value):
        self.tooltip = value


class Menu:
    def __init__(self, parent=None, title=''):
        self.title, self.items = title, []
    def addAction(self, action, callback=None):
        if isinstance(action, str):
            action = Action(action)
        if callback is not None:
            action.triggered.connect(callback)
        self.items.append(action)
        return action
    def addMenu(self, title):
        menu = Menu(title=title)
        self.items.append(menu)
        return menu
    def addSeparator(self):
        self.items.append(None)


class KeySequence:
    StandardKey = SimpleNamespace(Open='CTRL+O')
    def __init__(self, key):
        self.key = key


def actual_method(name, extra=None):
    tree = ast.parse((PLAYER / 'player_ui.py').read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'PlayerUiMixin')
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)
    module = ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[]))
    ns = dict(QAction=Action, QMenu=Menu, QKeySequence=KeySequence,
              Qt=SimpleNamespace(ShortcutContext=SimpleNamespace(WindowShortcut='window')),
              QStyle=SimpleNamespace(StandardPixmap=SimpleNamespace(SP_MessageBoxInformation='info')))
    ns.update(extra or {})
    exec(compile(module, str(PLAYER / 'player_ui.py'), 'exec'), ns)
    return ns[name]


def menu_host():
    host = SimpleNamespace()
    host.menubar = Menu()
    host.menuBar = lambda: host.menubar
    host.style = lambda: SimpleNamespace(standardIcon=lambda key: key)
    host.workspace_help_button = Mock()
    host.panel_button = Mock()
    host.advanced_widget = Mock()
    names = ('pick_capture', 'pick_review_candidates', 'pick_dynamic_profiles',
             'open_review_session', 'open_dynamic_review', 'save_review_session',
             'save_dynamic_review', 'save_variant_entry', 'show_settings', 'close',
             '_display_current', 'restore_default_layout', 'show_all_panels',
             'show_workspace_help', 'about', 'manage_canonical_video', '_canonical_toggled')
    for name in names:
        setattr(host, name, Mock())
    host._canonical_enabled = True
    host._toggle_advanced_controls = lambda v: actual_method('_toggle_advanced_controls')(host, v)
    host.panels = {name: SimpleNamespace(toggleViewAction=lambda n=name: Action(n))
                   for name in ('axes', 'muscles', 'inspector', 'anomalies', 'tuning', 'dynamic_profile', 'video_sync')}
    actual_method('_create_menus')(host)
    return host


def actions(menu):
    return {item.text: item for item in menu.items if isinstance(item, Action)}


def test_top_menu_order_matches_requested_layout():
    host = menu_host()
    assert [m.title for m in host.menubar.items] == ['File', 'Open', 'View', 'Advanced', 'Twin', 'Help']


def test_menu_icon_and_f1_share_one_help_action():
    host = menu_host()
    assert host.workspace_help_action in host.menubar.items[-1].items
    assert host.workspace_help_action.shortcut.key == 'F1'
    host.workspace_help_button.setDefaultAction.assert_called_once_with(host.workspace_help_action)
    host.workspace_help_action.triggered.emit()
    host.show_workspace_help.assert_called_once_with()
    host.about.assert_not_called()


@pytest.mark.parametrize('title,handler', [
    ('Open capture...', 'pick_capture'),
    ('Review optimiser candidates...', 'pick_review_candidates'),
    ('Review dynamic motion profiles...', 'pick_dynamic_profiles'),
    ('Open saved candidate review...', 'open_review_session'),
    ('Open saved dynamic review...', 'open_dynamic_review'),
    ('Ride reference video...', 'manage_canonical_video'),
])
def test_all_open_workflows_remain_available(title, handler):
    host = menu_host()
    actions(host.open_menu)[title].triggered.emit()
    getattr(host, handler).assert_called_once_with()


def test_advanced_controls_toggle_has_no_duplicate_toolbar_button():
    host = menu_host()
    host.advanced_controls_action.setChecked(True)
    host.advanced_widget.setVisible.assert_called_with(True)
    host._toggle_advanced_controls(False)
    assert not host.advanced_controls_action.isChecked()
    host.advanced_widget.setVisible.assert_called_with(False)
    assert not hasattr(host, 'advanced_button')


def test_twin_menu_uses_existing_settings_and_does_not_enable_output():
    host = menu_host()
    assert not host.twin_state_action.enabled
    assert not host.twin_destination_action.enabled
    host.show_settings.assert_not_called()
    actions(host.twin_menu)['Visualizer settings...'].triggered.emit()
    host.show_settings.assert_called_once_with()


def test_save_workflows_retain_initial_enabled_guards():
    host = menu_host()
    for name in ('save_review_session_action', 'save_dynamic_action', 'save_variant_action'):
        action = getattr(host, name)
        assert action in host.menubar.items[0].items
        assert not action.enabled


def test_source_has_no_top_open_or_advanced_buttons_and_keeps_single_inspector():
    source = (PLAYER / 'player_ui.py').read_text()
    assert 'self.open_menu_button' not in source
    assert 'self.advanced_button' not in source
    assert "self.workspace_help_button" in source
    assert "key != 'inspector'" in source
    # The production layout's overlay is unchanged, not a split video view.
    assert 'QStackedLayout.StackingMode.StackAll' in source


def test_help_open_method_changes_no_playback_or_analysis_state():
    tree = ast.parse((PLAYER / 'player_ui.py').read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'PlayerUiMixin')
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'show_workspace_help')
    attributes = {n.attr for n in ast.walk(method) if isinstance(n, ast.Attribute)}
    assert not attributes.intersection({'seek_seconds', '_set_playing', '_request_reanalysis',
                                        'current_s', 'loop_range', 'recording', 'twin_output'})
    assert {'show', 'raise_', 'activateWindow'} <= attributes
    assert 'exec' not in attributes
