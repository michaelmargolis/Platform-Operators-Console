# test_transform_bars_v159.py ver 1
"""Checks the light QPainter transform display and both Designer layouts."""

import os
from pathlib import Path
import xml.etree.ElementTree as ET

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import QtWidgets
import pytest

from ui.ui_widgets import TransformBarsWidget

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def app():
    instance = QtWidgets.QApplication.instance()
    if instance is None:
        instance = QtWidgets.QApplication([])
    yield instance


def test_normalized_values_and_redraw_on_changes(app):
    widget = TransformBarsWidget()
    widget.set_values((0.63, -0.42, 0.0, 1.6, -1.4, 0.005))
    assert widget.values == (63, -42, 0, 100, -100, 0)
    widget.set_values((0.63, -0.42, 0.0, 1.6, -1.4, 0.005))
    assert widget.values == (63, -42, 0, 100, -100, 0)
    with pytest.raises(ValueError):
        widget.set_values((0, 1))
    widget.close()


def test_paint_both_directions_and_small_layout(app):
    widget = TransformBarsWidget(show_values=True)
    widget.resize(180, 110)  # Less space than on the 800x480 operator layout.
    widget.set_values((0.8, -0.8, 0, 0, 0, 0))
    widget.show()
    app.processEvents()
    image = widget.grab().toImage()
    colour = widget.GREEN.rgb() & 0xFFFFFF
    # Both positive and negative green bars must be visible after drawing.
    pixel_rows = []
    for y in range(image.height()):
        count = sum(1 for x in range(image.width())
                    if image.pixel(x, y) & 0xFFFFFF == colour)
        if count >= 4:
            pixel_rows.append(y)
    assert pixel_rows
    assert max(pixel_rows) - min(pixel_rows) > 10
    widget.set_show_values(False)
    widget.resize(130, 80)
    widget.repaint()
    widget.close()


@pytest.mark.parametrize("filename", ["poc_main.ui", "poc_main_800x480.ui"])
def test_designer_layout_has_single_placeholder(filename):
    root = ET.parse(ROOT / "ui" / filename).getroot()
    group = root.find(".//widget[@name='grp_transform_levels']")
    assert group is not None
    assert group.find(".//layout[@name='transformBarsLayout']") is not None
    assert group.find(".//widget[@name='transformBarsPlaceholder']") is not None
    assert not group.findall(".//widget[@class='QProgressBar']")
    assert group.find("./property[@name='visible']") is None
