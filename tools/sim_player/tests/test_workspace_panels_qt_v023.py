"""Real Qt widget/parity checks. Skip rather than simulate Qt if unavailable."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from dataclasses import replace
import pytest
pytest.importorskip('PyQt6')
from PyQt6.QtWidgets import QApplication
from PyQt6.QtTest import QTest, QSignalSpy
from PyQt6.QtCore import Qt
from tools.sim_player.task_workspace import TaskInspector
from tools.sim_player.panels import ResponsiveBars
from tools.sim_player.panel_state import muscle_bar_state
from tools.sim_player.muscle_extrema import MuscleExtrema,plot_bounds
from test_panel_parity_v023 import recording

@pytest.fixture(scope='module')
def application():
    app=QApplication.instance() or QApplication([])
    yield app

@pytest.fixture
def inspector(application):
    widget=TaskInspector();widget.resize(440,900)
    widget.set_recording(recording());widget.show();application.processEvents()
    yield widget
    widget.close();widget.deleteLater();application.processEvents()


def test_inspector_uses_original_widget_and_matches_advanced_rendering(inspector,application):
    r=inspector.recording;h=MuscleExtrema();h.advance(r.frames,0);h.advance(r.frames,2)
    state=muscle_bar_state(r,r.frames[2],extrema=h.extrema(),bounds=plot_bounds(r))
    legacy=ResponsiveBars(False)
    try:
        assert isinstance(inspector.muscle_readout,ResponsiveBars)
        for bar in (inspector.muscle_readout,legacy):
            bar.setFixedSize(420,260);state.apply(bar)
        a=inspector.muscle_readout.grab().toImage();b=legacy.grab().toImage()
        assert not a.isNull() and a==b
        red=sum(a.pixelColor(x,y).red()>220 and a.pixelColor(x,y).green()<150
                for y in range(a.height()) for x in range(a.width()))
        assert red>20  # outside-range peak lines remain after current demand recovers
    finally:legacy.close()


def test_keyboard_tuning_commits_without_mouse_release(inspector):
    spy=QSignalSpy(inspector.gain_commit_requested)
    slider=inspector.gain_sliders[0];slider.setFocus()
    QTest.keyClick(slider,Qt.Key.Key_Right);QTest.qWait(520)
    assert len(spy)==1 and spy[0][0][0]>100


def test_busy_and_dynamic_states_disable_changes(inspector):
    inspector.set_busy(True)
    assert not inspector.gain_sliders[0].isEnabled()
    assert not inspector.washout_apply_button.isEnabled()
    inspector.set_busy(False)
    assert inspector.gain_sliders[0].isEnabled()
    inspector.set_recording(replace(inspector.recording,dynamic_profile={'label':'readonly'}))
    assert not inspector.gain_sliders[0].isEnabled()
    assert not inspector.restore_button.isEnabled()


def test_changing_task_preserves_markers_and_notes(inspector):
    r=inspector.recording;h=MuscleExtrema();h.advance(r.frames,0);h.advance(r.frames,2)
    state=muscle_bar_state(r,r.frames[2],extrema=h.extrema(),bounds=plot_bounds(r))
    inspector.set_muscle_state(state);inspector.current_s=.1
    inspector.note_edit.setText('Test note');inspector._add_note()
    for mode in ('tune','validate','review'):
        inspector.set_workspace(mode)
        assert inspector.muscle_readout.extrema==state.extrema
    assert inspector.notes()==({'seconds':.1,'text':'Test note'},)
