#transform_viewer.py  ver 1
"""Six-axis raw/processed transform trace viewer adapted from SOP show_washout.py."""

from __future__ import annotations

import numpy as np
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QVBoxLayout
import pyqtgraph as pg
from pyqtgraph import InfiniteLine


NUM_AXES = 6
AXES = ("X", "Y", "Z", "Roll", "Pitch", "Yaw")
MAX_SAMPLES = 2048


class TransformViewer:
    """Display raw simulator and processed POC transforms as six live traces."""

    def __init__(self, parent_widget):
        self.plot_data_in = [[] for _ in range(NUM_AXES)]
        self.plot_data_out = [[] for _ in range(NUM_AXES)]
        self.curves_in = []
        self.curves_out = []

        layout = QVBoxLayout(parent_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        for axis in range(NUM_AXES):
            plot = pg.PlotWidget()
            plot.setYRange(-1.5, 1.5, padding=0)
            plot.setXRange(0, MAX_SAMPLES, padding=0)
            plot.setLabel("left", AXES[axis])
            plot.setMouseEnabled(x=False, y=False)
            plot.setMenuEnabled(False)
            plot.hideButtons()
            plot.setBackground("w")
            plot.plotItem.showGrid(x=True, y=True)
            plot.getAxis("bottom").setStyle(showValues=False)
            plot.addItem(InfiniteLine(pos=0, angle=0, pen=pg.mkPen("k", width=1, style=Qt.DashLine)))
            layout.addWidget(plot)

            self.curves_in.append(
                plot.plot(pen=None, symbol="o", symbolPen="r", symbolBrush="r", symbolSize=3)
            )
            self.curves_out.append(
                plot.plot(pen=None, symbol="o", symbolPen="b", symbolBrush="b", symbolSize=3)
            )

    def update(self, raw_transform, processed_transform):
        if len(raw_transform) != NUM_AXES or len(processed_transform) != NUM_AXES:
            return

        for axis in range(NUM_AXES):
            try:
                in_value = float(raw_transform[axis])
                out_value = float(processed_transform[axis])
            except (TypeError, ValueError):
                continue

            self.plot_data_in[axis].append(in_value)
            self.plot_data_out[axis].append(out_value)
            if len(self.plot_data_in[axis]) > MAX_SAMPLES:
                self.plot_data_in[axis].pop(0)
                self.plot_data_out[axis].pop(0)

            count = len(self.plot_data_in[axis])
            x_values = np.arange(count)
            self.curves_in[axis].setData(x_values, np.asarray(self.plot_data_in[axis], dtype=np.float64))
            self.curves_out[axis].setData(x_values, np.asarray(self.plot_data_out[axis], dtype=np.float64))
