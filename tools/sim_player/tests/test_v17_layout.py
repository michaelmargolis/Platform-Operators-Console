# test_v17_layout.py ver 1
"""Headless regression of the v17 main-window and right dock sizing."""
from __future__ import annotations
import ast
from pathlib import Path
from types import SimpleNamespace

BASE = Path(__file__).resolve().parents[1]
UI = (BASE / 'player_ui.py').read_text(encoding='utf-8')
APP = (BASE / 'app.py').read_text(encoding='utf-8')


def test_fixed_controls_and_expanding_video_are_direct_layout_children():
    assert 'self.video_stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)' in UI
    assert 'layout.addWidget(self.video_stack, 1)' in UI
    assert 'self.control_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)' in UI
    assert 'layout.addWidget(self.control_container, 0)' in UI
    assert 'self.control_container.setFixedHeight(height)' in UI
    assert 'VideoResizeSplitter' not in UI
    assert 'QSplitter#videoSplitter' not in UI
    assert 'self.video_splitter' not in APP


def test_saved_v16_spacer_is_restored_and_removed_before_first_show():
    restore = APP.index('self.restoreState(layout)')
    create = APP.index('legacy_space = QDockWidget(')
    remove = APP.index('self.removeDockWidget(legacy_space)')
    assert create < restore < remove
    assert 'sim_player_right_spacer_v16' in APP
    assert 'self.settings.remove("video_splitter_state")' in APP
    assert 'self.settings.setValue("video_splitter_state"' not in APP
    assert 'self.dock_space' not in UI


def _extract_compact_method():
    module = ast.parse(UI)
    mixin = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == 'PlayerUiMixin')
    method = next(n for n in mixin.body if isinstance(n, ast.FunctionDef)
                  and n.name == '_compact_right_docks')
    qt = SimpleNamespace(DockWidgetArea=SimpleNamespace(RightDockWidgetArea='right'),
                         Orientation=SimpleNamespace(Vertical='vertical'))
    namespace = {'Qt': qt}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])),
                 str(BASE / 'player_ui.py'), 'exec'), namespace)
    return namespace['_compact_right_docks']


class FakeDock:
    def __init__(self, y, height, *, visible=True, floating=False, area='right', x=0):
        self.y, self.h, self.x = y, height, x
        self.visible, self.floating, self.area = visible, floating, area
    def isVisible(self): return self.visible
    def isFloating(self): return self.floating
    def geometry(self): return SimpleNamespace(top=lambda: self.y, left=lambda: self.x)
    def widget(self): return SimpleNamespace(minimumHeight=lambda: self.h,
                                          minimumSizeHint=lambda: SimpleNamespace(height=lambda: self.h))
    def minimumHeight(self): return self.h
    def minimumSizeHint(self): return SimpleNamespace(height=lambda: self.h + 25)


class FakeMain:
    def __init__(self, panels):
        self.panels = dict(enumerate(panels))
        self.last_resize = None
    def dockWidgetArea(self, dock): return dock.area
    def resizeDocks(self, docks, sizes, orientation):
        self.last_resize = (docks, sizes, orientation)


def test_three_separate_right_panels_minimise_upper_two_and_fill_bottom():
    axis = FakeDock(50, 190)
    muscles = FakeDock(272, 190)
    tuning = FakeDock(500, 215)
    win = FakeMain([axis, muscles, tuning])
    _extract_compact_method()(win)
    docks, sizes, orientation = win.last_resize
    assert docks == [axis, muscles, tuning]
    assert sizes == [225, 225, 10000]
    assert orientation == 'vertical'


def test_tabbed_lower_group_is_one_row_and_fills_bottom():
    axis = FakeDock(50, 190)
    muscles = FakeDock(273, 190)
    tuning = FakeDock(274, 215)
    other_area = FakeDock(600, 100, area='left')
    hidden = FakeDock(800, 100, visible=False)
    floating = FakeDock(700, 190, floating=True)
    win = FakeMain([axis, muscles, tuning, other_area, hidden, floating])
    _extract_compact_method()(win)
    docks, sizes, _ = win.last_resize
    assert docks == [axis, muscles]
    assert sizes == [225, 10000]


def test_only_one_visible_dock_is_not_resized():
    win = FakeMain([FakeDock(10, 220), FakeDock(250, 200, visible=False)])
    _extract_compact_method()(win)
    assert win.last_resize is None


def test_default_layout_has_no_spacer_or_forced_window_resizing():
    assert 'self.tabifyDockWidget(muscle, tuning)' in UI
    assert 'self.dock_space' not in UI
    assert 'self.resize(self.width(), old_height + delta)' not in APP
    assert 'self._initialize_video_splitter' not in APP
