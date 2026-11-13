"""Optional genuine Qt widget/paint tests; run on the installed Windows player."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest
pytest.importorskip('PyQt6.QtWidgets')
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QColor
from types import SimpleNamespace as NS
from tools.sim_player.task_workspace import PlotCanvas
from tools.sim_player.task_workspace_models import AXIS_COLORS


def test_coloured_legend_draws_in_real_widget():
    app=QApplication.instance() or QApplication([])
    canvas=PlotCanvas();canvas.resize(900,400)
    frames=tuple(NS(processed_axes=(i/100.,)*6) for i in range(20))
    r=NS(frames=frames,times=tuple(i*.05 for i in range(20)),duration=.95)
    canvas.set_recording(r);canvas.show();app.processEvents()
    image=canvas.grab().toImage()
    # Legend region, not overlapping coincident traces.
    pixels={image.pixelColor(x,y).name() for y in range(8,42) for x in range(65,850)}
    assert all(color in pixels for color in AXIS_COLORS)
    canvas.close();canvas.deleteLater();app.processEvents()


def test_reference_dialog_builds_both_tabs_without_importing(monkeypatch):
    from tools.sim_player import canonical_dialog
    app=QApplication.instance() or QApplication([])
    monkeypatch.setattr(canonical_dialog,'load_media',lambda key:None)
    d=canonical_dialog.CanonicalVideoDialog()
    d.show();app.processEvents()
    assert d.tabs.count()==2
    assert d.worker is None and not d.import_button.isEnabled()
    assert d.csv.isReadOnly()
    assert not d.calibration_verified.isVisible()
    assert d.table.columnCount()==2
    d.close();d.deleteLater();app.processEvents()
