# test_marker_lane_v180.py ver 1
"""Qt6 smoke tests for kinematic interval hit targets and ride marker parity."""
import os
import sys
from types import SimpleNamespace
import pytest


def _app():
    if sys.platform.startswith('linux') and not (os.getenv('DISPLAY') or os.getenv('WAYLAND_DISPLAY')):
        os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    widgets=pytest.importorskip('PyQt6.QtWidgets')
    return widgets.QApplication.instance() or widgets.QApplication(['marker_lane_v180'])


def _event(start, end, peak):
    return SimpleNamespace(start_s=start,end_s=end,seconds=peak,
                           duration_s=end-start,name='Final length clipped',
                           color='#ef6d65',details='M1 clipping')


def test_kinematic_bar_spans_duration_and_selects_peak():
    _app()
    from tools.sim_player.panels import TimelineMarkers
    from tools.sim_player.marker_intervals import timeline_x
    from PyQt6.QtCore import Qt, QPointF
    from PyQt6.QtGui import QMouseEvent
    lane=TimelineMarkers()
    lane.resize(1036,52)
    event=_event(10,40,25)
    lane.set_recording((event,),100,category='kinematic')
    assert not lane.grab().isNull()
    assert lane.closest_event(timeline_x(12,1036,100),18) is event
    assert lane.closest_event(timeline_x(38,1036,100),18) is event
    assert lane.closest_event(timeline_x(38,1036,100),40) is None
    seeks=[]
    lane.seek_requested.connect(seeks.append)
    pos=QPointF(timeline_x(12,1036,100),18)
    lane.mousePressEvent(QMouseEvent(QMouseEvent.Type.MouseButtonPress,pos,
                                    Qt.MouseButton.LeftButton,Qt.MouseButton.LeftButton,
                                    Qt.KeyboardModifier.NoModifier))
    assert seeks==[25]
    lane.close()


def test_ride_markers_unchanged():
    _app()
    from tools.sim_player.panels import TimelineMarkers
    lane=TimelineMarkers()
    lane.resize(1036,52)
    event=SimpleNamespace(seconds=50,name='Leaving station',color='#f00')
    lane.set_recording((event,),100,category='ride')
    assert lane.closest_event(lane.x_at(50),18) is event
    assert lane.closest_event(lane.x_at(60),18) is None
    assert not lane.grab().isNull()
    lane.close()
